"""
Volume Anomaly Detector

Detects unusual volume patterns that may indicate:
- Informed trading (someone knows something)
- Manipulation attempts
- Market regime changes
"""
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional
import statistics

logger = logging.getLogger(__name__)


@dataclass
class VolumeAlert:
    """Volume anomaly alert."""
    asset: str
    alert_type: str  # "spike", "surge", "drought", "unusual_pattern"
    severity: float  # 0-1 scale
    current_volume: float
    baseline_volume: float
    z_score: float
    timestamp: float
    message: str


@dataclass
class VolumeStats:
    """Volume statistics for an asset."""
    current_rate: float  # Volume per second
    avg_rate_1m: float
    avg_rate_5m: float
    avg_rate_15m: float
    std_dev_1m: float
    z_score: float  # Current vs 5m average
    is_anomalous: bool
    trend: str  # "increasing", "decreasing", "stable"


class VolumeDetector:
    """
    Detects volume anomalies using statistical methods.
    
    Key signals:
    1. Volume spikes (sudden 3x+ increase)
    2. Volume surges (sustained high volume)
    3. Volume drought (unusually low volume)
    4. Volume pattern changes (regime shift)
    """
    
    # Thresholds
    SPIKE_THRESHOLD = 3.0  # Z-score for spike
    SURGE_THRESHOLD = 2.0  # Z-score for surge (sustained)
    DROUGHT_THRESHOLD = -1.5  # Z-score for low volume
    
    # History length
    HISTORY_MINUTES = 15
    
    def __init__(self):
        # Volume history: asset -> deque of (timestamp, volume_per_sec)
        self.volume_history: dict[str, deque] = {}
        
        # Recent alerts
        self.alerts: deque = deque(maxlen=100)
        
        # Alert cooldown (prevent spam)
        self._last_alert_time: dict[str, float] = {}
        self._alert_cooldown = 30  # seconds
    
    def record_volume(self, asset: str, volume: float, timestamp: Optional[float] = None):
        """Record volume observation for an asset."""
        if asset not in self.volume_history:
            self.volume_history[asset] = deque(maxlen=self.HISTORY_MINUTES * 60)
        
        ts = timestamp or time.time()
        self.volume_history[asset].append((ts, volume))
    
    def get_volume_stats(self, asset: str) -> VolumeStats:
        """Get volume statistics for an asset."""
        if asset not in self.volume_history:
            return VolumeStats(
                current_rate=0, avg_rate_1m=0, avg_rate_5m=0,
                avg_rate_15m=0, std_dev_1m=0, z_score=0,
                is_anomalous=False, trend="stable"
            )
        
        history = self.volume_history[asset]
        if len(history) < 2:
            return VolumeStats(
                current_rate=0, avg_rate_1m=0, avg_rate_5m=0,
                avg_rate_15m=0, std_dev_1m=0, z_score=0,
                is_anomalous=False, trend="stable"
            )
        
        now = time.time()
        
        # Get volumes in different windows
        def get_volumes_in_window(seconds: int) -> list:
            cutoff = now - seconds
            return [v for t, v in history if t >= cutoff]
        
        vols_1m = get_volumes_in_window(60)
        vols_5m = get_volumes_in_window(300)
        vols_15m = get_volumes_in_window(900)
        
        # Calculate rates (volume per second)
        def calc_rate(vols: list, seconds: int) -> float:
            if not vols:
                return 0
            return sum(vols) / seconds
        
        current_rate = calc_rate(vols_1m[-10:], 10) if len(vols_1m) >= 10 else 0
        avg_rate_1m = calc_rate(vols_1m, 60)
        avg_rate_5m = calc_rate(vols_5m, 300)
        avg_rate_15m = calc_rate(vols_15m, 900)
        
        # Standard deviation of 1-minute rolling rates
        std_dev_1m = 0
        if len(vols_1m) >= 10:
            try:
                std_dev_1m = statistics.stdev(vols_1m[-60:])
            except:
                std_dev_1m = 0
        
        # Z-score: how unusual is current rate vs baseline
        z_score = 0
        if avg_rate_5m > 0 and std_dev_1m > 0:
            z_score = (current_rate - avg_rate_5m) / (std_dev_1m + 0.0001)
        
        # Determine trend
        trend = "stable"
        if len(vols_5m) >= 60:
            first_half = sum(vols_5m[:len(vols_5m)//2])
            second_half = sum(vols_5m[len(vols_5m)//2:])
            if second_half > first_half * 1.5:
                trend = "increasing"
            elif second_half < first_half * 0.66:
                trend = "decreasing"
        
        # Is it anomalous?
        is_anomalous = abs(z_score) > self.SURGE_THRESHOLD
        
        return VolumeStats(
            current_rate=current_rate,
            avg_rate_1m=avg_rate_1m,
            avg_rate_5m=avg_rate_5m,
            avg_rate_15m=avg_rate_15m,
            std_dev_1m=std_dev_1m,
            z_score=z_score,
            is_anomalous=is_anomalous,
            trend=trend
        )
    
    def check_for_anomalies(self, asset: str) -> Optional[VolumeAlert]:
        """Check for volume anomalies and generate alert if found."""
        stats = self.get_volume_stats(asset)
        
        # Check cooldown
        now = time.time()
        if asset in self._last_alert_time:
            if now - self._last_alert_time[asset] < self._alert_cooldown:
                return None
        
        alert = None
        
        # Check for spike
        if stats.z_score >= self.SPIKE_THRESHOLD:
            severity = min(1.0, (stats.z_score - self.SPIKE_THRESHOLD) / 3)
            alert = VolumeAlert(
                asset=asset,
                alert_type="spike",
                severity=severity,
                current_volume=stats.current_rate,
                baseline_volume=stats.avg_rate_5m,
                z_score=stats.z_score,
                timestamp=now,
                message=f"Volume spike: {stats.z_score:.1f}σ above normal"
            )
        
        # Check for sustained surge
        elif stats.z_score >= self.SURGE_THRESHOLD and stats.trend == "increasing":
            severity = min(1.0, (stats.z_score - self.SURGE_THRESHOLD) / 2)
            alert = VolumeAlert(
                asset=asset,
                alert_type="surge",
                severity=severity,
                current_volume=stats.current_rate,
                baseline_volume=stats.avg_rate_5m,
                z_score=stats.z_score,
                timestamp=now,
                message=f"Sustained volume surge: trend increasing"
            )
        
        # Check for drought
        elif stats.z_score <= self.DROUGHT_THRESHOLD:
            severity = min(1.0, abs(stats.z_score + self.DROUGHT_THRESHOLD) / 2)
            alert = VolumeAlert(
                asset=asset,
                alert_type="drought",
                severity=severity,
                current_volume=stats.current_rate,
                baseline_volume=stats.avg_rate_5m,
                z_score=stats.z_score,
                timestamp=now,
                message=f"Volume drought: {abs(stats.z_score):.1f}σ below normal"
            )
        
        if alert:
            self.alerts.append(alert)
            self._last_alert_time[asset] = now
            logger.info(f"[VOLUME] {alert.message} for {asset}")
        
        return alert
    
    def get_signal_strength(self, asset: str) -> float:
        """
        Get volume-based signal strength.
        
        Returns:
            -1 to 1: negative = low volume (weak signal), positive = high volume (strong signal)
        """
        stats = self.get_volume_stats(asset)
        
        # Normalize z-score to -1 to 1 range
        normalized = max(-1, min(1, stats.z_score / self.SPIKE_THRESHOLD))
        
        return normalized
    
    def should_trade(self, asset: str) -> tuple[bool, str]:
        """
        Determine if volume conditions are suitable for trading.
        
        Returns:
            (should_trade, reason)
        """
        stats = self.get_volume_stats(asset)
        
        # Don't trade during drought (low liquidity)
        if stats.z_score <= self.DROUGHT_THRESHOLD:
            return False, f"Volume drought ({stats.z_score:.1f}σ below normal)"
        
        # Be cautious during extreme spikes (possible manipulation)
        if stats.z_score >= self.SPIKE_THRESHOLD * 2:
            return False, f"Extreme volume spike ({stats.z_score:.1f}σ) - possible manipulation"
        
        # Good to trade
        return True, f"Volume normal ({stats.z_score:+.1f}σ)"
    
    def get_recent_alerts(self, asset: Optional[str] = None, limit: int = 10) -> list:
        """Get recent volume alerts."""
        alerts = list(self.alerts)
        if asset:
            alerts = [a for a in alerts if a.asset == asset]
        return alerts[-limit:]
