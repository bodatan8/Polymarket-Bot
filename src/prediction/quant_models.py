"""
Quantitative Probability Models

Implements rigorous mathematical models for probability estimation:
1. Stochastic process models (GBM, Ornstein-Uhlenbeck)
2. Bayesian updating
3. Volatility estimation (realized volatility)
4. Regime detection
"""
import math
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from scipy.stats import norm
from collections import deque

from src.signals.price_feed import MomentumData


@dataclass
class ProbabilityDistribution:
    """Probability distribution with uncertainty."""
    mean: float  # Expected probability
    std: float   # Standard deviation (uncertainty)
    confidence: float  # Confidence level (0-1)
    
    def get_confidence_interval(self, level: float = 0.95) -> Tuple[float, float]:
        """Get confidence interval for probability."""
        z = norm.ppf((1 + level) / 2)
        lower = max(0.0, min(1.0, self.mean - z * self.std))
        upper = max(0.0, min(1.0, self.mean + z * self.std))
        return lower, upper


class StochasticProcessModel:
    """
    Models price evolution using stochastic processes.
    
    For 15-minute crypto markets, we use:
    - Geometric Brownian Motion (GBM) for trending markets
    - Ornstein-Uhlenbeck (OU) for mean-reverting markets
    """
    
    def __init__(self):
        self.price_history: dict[str, deque] = {}
        self.max_history = 1000  # Keep last 1000 price points
    
    def record_price(self, asset: str, price: float, timestamp: float):
        """Record price observation."""
        if asset not in self.price_history:
            self.price_history[asset] = deque(maxlen=self.max_history)
        self.price_history[asset].append((timestamp, price))
    
    def estimate_volatility(self, asset: str, window_seconds: int = 300) -> float:
        """
        Calculate realized volatility from price history.
        
        Uses log returns: RV = std(ln(S_t/S_{t-1}))
        Annualized: σ_annual = σ_5min × √(number_of_5min_periods_per_year)
        """
        if asset not in self.price_history:
            return 0.02  # Default 2% volatility
        
        history = list(self.price_history[asset])
        if len(history) < 2:
            return 0.02
        
        # Get prices in window
        now = time.time()
        cutoff = now - window_seconds
        window_prices = [(t, p) for t, p in history if t >= cutoff]
        
        if len(window_prices) < 2:
            return 0.02
        
        # Calculate log returns
        returns = []
        for i in range(1, len(window_prices)):
            if window_prices[i-1][1] > 0:
                ret = math.log(window_prices[i][1] / window_prices[i-1][1])
                returns.append(ret)
        
        if len(returns) < 2:
            return 0.02
        
        # Standard deviation of returns
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std_ret = math.sqrt(variance)
        
        # Annualize (assuming 1-second intervals)
        # For 15-min markets, we care about short-term vol
        # Scale to 15-minute timeframe
        periods_per_15min = 900  # 15 minutes = 900 seconds
        annualized_vol = std_ret * math.sqrt(periods_per_15min)
        
        return max(0.001, min(0.50, annualized_vol))  # Clamp to reasonable range
    
    def estimate_drift(self, asset: str, window_seconds: int = 300) -> float:
        """
        Estimate drift (expected return) from price history.
        
        μ = mean(ln(S_t/S_{t-1}))
        """
        if asset not in self.price_history:
            return 0.0
        
        history = list(self.price_history[asset])
        if len(history) < 2:
            return 0.0
        
        now = time.time()
        cutoff = now - window_seconds
        window_prices = [(t, p) for t, p in history if t >= cutoff]
        
        if len(window_prices) < 2:
            return 0.0
        
        # Calculate log returns
        returns = []
        for i in range(1, len(window_prices)):
            if window_prices[i-1][1] > 0:
                ret = math.log(window_prices[i][1] / window_prices[i-1][1])
                returns.append(ret)
        
        if len(returns) == 0:
            return 0.0
        
        # Mean return (drift)
        drift = sum(returns) / len(returns)
        
        # Annualize
        periods_per_15min = 900
        annualized_drift = drift * periods_per_15min
        
        return max(-0.50, min(0.50, annualized_drift))  # Clamp to reasonable range
    
    def calculate_probability_gbm(
        self,
        current_price: float,
        time_left_seconds: float,
        volatility: Optional[float] = None,
        drift: Optional[float] = None,
        asset: str = "BTC"
    ) -> ProbabilityDistribution:
        """
        Calculate probability using Geometric Brownian Motion.
        
        Model: dS(t) = μ*S(t)*dt + σ*S(t)*dW(t)
        
        Probability that price goes up:
        P(S_T > S_0) = Φ((μ*T - 0.5*σ²*T) / (σ*√T))
        
        Where:
        - μ = drift (expected return)
        - σ = volatility
        - T = time to expiry
        - Φ = cumulative normal distribution
        """
        if volatility is None:
            volatility = self.estimate_volatility(asset)
        if drift is None:
            drift = self.estimate_drift(asset)
        
        if time_left_seconds <= 0:
            return ProbabilityDistribution(mean=0.5, std=0.0, confidence=1.0)
        
        # Convert to years for standard formulas
        T = time_left_seconds / (365.25 * 24 * 3600)  # Years
        
        # GBM probability formula
        # For log-normal: P(S_T > S_0) = Φ((μ - 0.5*σ²)*T / (σ*√T))
        if volatility > 0:
            z_score = (drift - 0.5 * volatility**2) * T / (volatility * math.sqrt(T))
            prob_up = norm.cdf(z_score)
        else:
            prob_up = 0.5
        
        # Uncertainty increases with time and volatility
        # Standard error of probability estimate
        std_error = volatility * math.sqrt(T) / 2  # Approximate
        confidence = 1.0 / (1.0 + std_error * 10)  # Higher uncertainty = lower confidence
        
        return ProbabilityDistribution(
            mean=max(0.01, min(0.99, prob_up)),
            std=min(0.25, std_error),
            confidence=max(0.1, min(1.0, confidence))
        )
    
    def calculate_probability_ou(
        self,
        current_price: float,
        mean_price: float,
        time_left_seconds: float,
        volatility: Optional[float] = None,
        reversion_speed: float = 0.1,
        asset: str = "BTC"
    ) -> ProbabilityDistribution:
        """
        Calculate probability using Ornstein-Uhlenbeck (mean-reverting).
        
        Model: dS(t) = θ(μ - S(t))dt + σ*dW(t)
        
        Better for markets that tend to revert to mean.
        """
        if volatility is None:
            volatility = self.estimate_volatility(asset)
        
        if time_left_seconds <= 0:
            return ProbabilityDistribution(mean=0.5, std=0.0, confidence=1.0)
        
        T = time_left_seconds / (365.25 * 24 * 3600)  # Years
        
        # OU process: expected value reverts to mean
        # E[S_T] = μ + (S_0 - μ) * exp(-θ*T)
        expected_price = mean_price + (current_price - mean_price) * math.exp(-reversion_speed * T)
        
        # Variance: Var[S_T] = σ²/(2θ) * (1 - exp(-2θ*T))
        if reversion_speed > 0:
            variance = (volatility**2 / (2 * reversion_speed)) * (1 - math.exp(-2 * reversion_speed * T))
        else:
            variance = volatility**2 * T
        
        std_price = math.sqrt(variance)
        
        # Probability that price goes up (assuming we start at current_price)
        if std_price > 0:
            z_score = (expected_price - current_price) / std_price
            prob_up = norm.cdf(z_score)
        else:
            prob_up = 0.5
        
        # Confidence based on reversion speed and volatility
        confidence = min(1.0, reversion_speed * 10) * (1.0 / (1.0 + volatility * 5))
        
        return ProbabilityDistribution(
            mean=max(0.01, min(0.99, prob_up)),
            std=min(0.25, std_price / current_price if current_price > 0 else 0.1),
            confidence=max(0.1, min(1.0, confidence))
        )


class BayesianUpdater:
    """
    Bayesian probability updating.
    
    Combines prior probability (from historical data) with likelihood
    (from current signals) to get posterior probability.
    
    P(Up | signals) = P(signals | Up) × P(Up) / P(signals)
    """
    
    def __init__(self):
        # Prior probabilities from historical data
        # Format: asset -> (up_probability, sample_count)
        self.priors: dict[str, Tuple[float, int]] = {}
    
    def update_prior(self, asset: str, won: bool, total_bets: int):
        """Update prior probability from historical outcomes."""
        if asset not in self.priors:
            self.priors[asset] = (0.5, 0)
        
        current_prob, current_count = self.priors[asset]
        
        # Bayesian update: weighted average
        # New prior = (old_count × old_prob + new_outcome) / (old_count + 1)
        new_count = current_count + 1
        new_prob = (current_count * current_prob + (1.0 if won else 0.0)) / new_count
        
        # Use exponential moving average for stability
        alpha = 0.1  # Learning rate
        smoothed_prob = alpha * (1.0 if won else 0.0) + (1 - alpha) * current_prob
        
        self.priors[asset] = (smoothed_prob, new_count)
    
    def get_prior(self, asset: str) -> Tuple[float, float]:
        """
        Get prior probability and confidence.
        
        Returns: (probability, confidence)
        """
        if asset not in self.priors:
            return 0.5, 0.1  # Neutral prior with low confidence
        
        prob, count = self.priors[asset]
        confidence = min(1.0, count / 100)  # Full confidence at 100 samples
        
        return prob, confidence
    
    def update(
        self,
        prior_prob: float,
        signal_prob: float,
        signal_strength: float,
        signal_confidence: float
    ) -> ProbabilityDistribution:
        """
        Bayesian update combining prior and signal.
        
        Args:
            prior_prob: Historical probability (0-1)
            signal_prob: Signal-based probability (0-1)
            signal_strength: How strong the signal is (0-1)
            signal_confidence: Confidence in signal (0-1)
        
        Returns:
            Posterior probability distribution
        """
        # Weight signal by its strength and confidence
        signal_weight = signal_strength * signal_confidence
        
        # Prior weight decreases as we get more signal information
        prior_weight = 1.0 - signal_weight
        
        # Weighted average (simplified Bayesian update)
        posterior_mean = prior_weight * prior_prob + signal_weight * signal_prob
        
        # Uncertainty: higher when priors and signals disagree
        disagreement = abs(prior_prob - signal_prob)
        uncertainty = disagreement * (1.0 - signal_weight) + 0.1 * signal_weight
        
        # Confidence: higher when we have strong, confident signals
        confidence = signal_weight + (1.0 - signal_weight) * 0.5
        
        return ProbabilityDistribution(
            mean=max(0.01, min(0.99, posterior_mean)),
            std=min(0.25, uncertainty),
            confidence=max(0.1, min(1.0, confidence))
        )


class RegimeDetector:
    """
    Detects market regimes to use appropriate models.
    
    Regimes:
    - trending: Strong directional movement
    - mean_reverting: Prices oscillate around mean
    - high_volatility: Large price swings
    - low_volatility: Stable prices
    """
    
    def __init__(self):
        self.price_history: dict[str, deque] = {}
    
    def record_price(self, asset: str, price: float, timestamp: float):
        """Record price observation."""
        if asset not in self.price_history:
            self.price_history[asset] = deque(maxlen=100)
        self.price_history[asset].append((timestamp, price))
    
    def detect_regime(
        self,
        asset: str,
        momentum: float,
        volatility: float,
        window_seconds: int = 300
    ) -> Tuple[str, float]:
        """
        Detect current market regime.
        
        Returns: (regime_name, confidence)
        """
        if asset not in self.price_history:
            return "unknown", 0.1
        
        history = list(self.price_history[asset])
        if len(history) < 10:
            return "unknown", 0.1
        
        now = time.time()
        cutoff = now - window_seconds
        window_prices = [p for t, p in history if t >= cutoff]
        
        if len(window_prices) < 10:
            return "unknown", 0.1
        
        # Calculate price change
        price_change = (window_prices[-1] - window_prices[0]) / window_prices[0] if window_prices[0] > 0 else 0
        
        # Calculate autocorrelation (mean reversion indicator)
        returns = []
        for i in range(1, len(window_prices)):
            if window_prices[i-1] > 0:
                ret = (window_prices[i] - window_prices[i-1]) / window_prices[i-1]
                returns.append(ret)
        
        autocorr = 0.0
        if len(returns) >= 2:
            mean_ret = sum(returns) / len(returns)
            numerator = sum((returns[i] - mean_ret) * (returns[i-1] - mean_ret) 
                          for i in range(1, len(returns)))
            denominator = sum((r - mean_ret)**2 for r in returns)
            if denominator > 0:
                autocorr = numerator / denominator
        
        # Regime detection logic
        abs_momentum = abs(momentum)
        abs_price_change = abs(price_change)
        
        # High volatility regime
        if volatility > 0.03:
            return "high_volatility", 0.8
        
        # Trending regime: strong momentum, low autocorrelation
        if abs_momentum > 0.02 and autocorr < 0.2:
            return "trending", 0.7
        
        # Mean reverting: negative autocorrelation
        if autocorr < -0.2:
            return "mean_reverting", 0.7
        
        # Low volatility: stable prices
        if volatility < 0.01 and abs_price_change < 0.005:
            return "low_volatility", 0.6
        
        # Default: unknown/neutral
        return "unknown", 0.3


class QuantProbabilityCalculator:
    """
    Main quant-style probability calculator.
    
    Combines:
    1. Stochastic process models (GBM/OU)
    2. Bayesian updating
    3. Volatility estimation
    4. Regime detection
    """
    
    def __init__(self):
        self.stochastic_model = StochasticProcessModel()
        self.bayesian_updater = BayesianUpdater()
        self.regime_detector = RegimeDetector()
    
    def calculate_probability(
        self,
        asset: str,
        current_price: float,
        time_left_seconds: float,
        momentum_data: Optional[MomentumData] = None,
        signal_probability: float = 0.5,
        signal_strength: float = 0.0,
        signal_confidence: float = 0.5
    ) -> ProbabilityDistribution:
        """
        Calculate probability using quant models.
        
        Args:
            asset: Asset symbol (BTC, ETH, etc.)
            current_price: Current crypto price
            time_left_seconds: Time until market expiry
            momentum_data: Momentum information
            signal_probability: Probability from signals (0-1)
            signal_strength: Strength of signal (0-1)
            signal_confidence: Confidence in signal (0-1)
        
        Returns:
            ProbabilityDistribution with mean, std, confidence
        """
        # Record price for volatility estimation
        self.stochastic_model.record_price(asset, current_price, time.time())
        self.regime_detector.record_price(asset, current_price, time.time())
        
        # Estimate volatility and drift
        volatility = self.stochastic_model.estimate_volatility(asset)
        drift = self.stochastic_model.estimate_drift(asset)
        
        # Detect regime
        momentum = momentum_data.trend_strength if momentum_data else 0.0
        regime, regime_confidence = self.regime_detector.detect_regime(
            asset, momentum, volatility
        )
        
        # Calculate probability based on regime
        if regime == "trending":
            # Use GBM for trending markets
            stochastic_prob = self.stochastic_model.calculate_probability_gbm(
                current_price, time_left_seconds, volatility, drift, asset
            )
        elif regime == "mean_reverting":
            # Use OU for mean-reverting markets
            # Estimate mean price from history
            mean_price = current_price  # Simplified
            stochastic_prob = self.stochastic_model.calculate_probability_ou(
                current_price, mean_price, time_left_seconds, volatility, 0.1, asset
            )
        else:
            # Default to GBM
            stochastic_prob = self.stochastic_model.calculate_probability_gbm(
                current_price, time_left_seconds, volatility, drift, asset
            )
        
        # Get prior from historical data
        prior_prob, prior_confidence = self.bayesian_updater.get_prior(asset)
        
        # Combine stochastic model with signal using Bayesian updating
        # Use stochastic model as "prior" and signal as "likelihood"
        posterior = self.bayesian_updater.update(
            prior_prob=stochastic_prob.mean,
            signal_prob=signal_probability,
            signal_strength=signal_strength,
            signal_confidence=signal_confidence
        )
        
        # Blend with regime confidence
        final_mean = regime_confidence * stochastic_prob.mean + (1 - regime_confidence) * posterior.mean
        final_std = max(stochastic_prob.std, posterior.std)
        final_confidence = min(stochastic_prob.confidence, posterior.confidence) * regime_confidence
        
        return ProbabilityDistribution(
            mean=max(0.01, min(0.99, final_mean)),
            std=min(0.25, final_std),
            confidence=max(0.1, min(1.0, final_confidence))
        )
    
    def record_outcome(self, asset: str, won: bool):
        """Record outcome for Bayesian prior updating."""
        # Get current count
        prior_prob, count = self.bayesian_updater.get_prior(asset)
        self.bayesian_updater.update_prior(asset, won, count + 1)
