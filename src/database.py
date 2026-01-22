"""
SQLite database for storing simulated bets and positions.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

DB_PATH = Path(__file__).parent.parent / "data" / "bets.db"


@dataclass
class Position:
    id: Optional[int]
    market_id: str
    market_name: str
    asset: str  # BTC, ETH, SOL, XRP
    side: str  # Up or Down
    entry_price: float  # Price paid (e.g., 0.84 for 84¢)
    amount_usd: float  # Amount bet in USD
    shares: float  # Number of shares bought
    target_price: float  # Price to beat
    start_time: str  # ISO format
    end_time: str  # ISO format
    status: str  # open, won, lost
    exit_price: Optional[float] = None  # Final price if resolved
    pnl: Optional[float] = None  # Profit/loss
    resolved_at: Optional[str] = None
    created_at: Optional[str] = None
    # Trading decision metadata
    edge: Optional[float] = None  # Calculated edge percentage
    true_prob: Optional[float] = None  # Estimated true probability
    signal_strength: Optional[float] = None  # Signal strength (0-1)
    timing_bucket: Optional[str] = None  # Time bucket (e.g., "2-5min")
    reasoning: Optional[str] = None  # Full reasoning string


def init_db():
    """Initialize the database with required tables."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_name TEXT NOT NULL,
            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            amount_usd REAL NOT NULL,
            shares REAL NOT NULL,
            target_price REAL NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            exit_price REAL,
            pnl REAL,
            resolved_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            edge REAL,
            true_prob REAL,
            signal_strength REAL,
            timing_bucket TEXT,
            reasoning TEXT
        )
    """)
    
    # Add new columns to existing tables (migration)
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN edge REAL")
    except sqlite3.OperationalError:
        pass  # Column already exists
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN true_prob REAL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN signal_strength REAL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN timing_bucket TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE positions ADD COLUMN reasoning TEXT")
    except sqlite3.OperationalError:
        pass
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_bets INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_wagered REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # === SIGNAL ACCURACY TRACKING TABLE ===
    # Tracks individual signal predictions to measure their accuracy over time
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            signal_name TEXT NOT NULL,
            signal_value REAL NOT NULL,
            signal_confidence REAL NOT NULL,
            predicted_direction TEXT NOT NULL,
            actual_direction TEXT,
            was_correct INTEGER,
            pnl_contribution REAL,
            asset TEXT,
            timing_bucket TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            FOREIGN KEY (position_id) REFERENCES positions(id)
        )
    """)
    
    # === PROBABILITY CALIBRATION TABLE ===
    # Tracks predicted vs actual probabilities for calibration
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS probability_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            predicted_prob REAL NOT NULL,
            prob_bucket TEXT NOT NULL,
            won INTEGER,
            pnl REAL,
            asset TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            FOREIGN KEY (position_id) REFERENCES positions(id)
        )
    """)
    
    # Insert initial stats row if not exists
    cursor.execute("SELECT COUNT(*) FROM stats")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO stats (total_bets, wins, losses, total_wagered, total_pnl) VALUES (0, 0, 0, 0, 0)")
    
    conn.commit()
    conn.close()


def add_position(position: Position) -> int:
    """Add a new position to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO positions (market_id, market_name, asset, side, entry_price, amount_usd, shares, target_price, start_time, end_time, status, edge, true_prob, signal_strength, timing_bucket, reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        position.market_id, position.market_name, position.asset, position.side,
        position.entry_price, position.amount_usd, position.shares, position.target_price,
        position.start_time, position.end_time, position.status,
        position.edge, position.true_prob, position.signal_strength, position.timing_bucket, position.reasoning
    ))
    
    position_id = cursor.lastrowid
    
    # Update stats
    cursor.execute("""
        UPDATE stats SET total_bets = total_bets + 1, total_wagered = total_wagered + ?, updated_at = ?
    """, (position.amount_usd, datetime.utcnow().isoformat()))
    
    conn.commit()
    conn.close()
    
    return position_id


def resolve_position(position_id: int, won: bool, exit_price: float):
    """Resolve a position as won or lost."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get the position
    cursor.execute("SELECT amount_usd, shares, entry_price FROM positions WHERE id = ?", (position_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    
    amount_usd, shares, entry_price = row
    
    # Calculate P&L
    if won:
        pnl = shares * 1.0 - amount_usd  # Win: get $1 per share
        status = "won"
    else:
        pnl = -amount_usd  # Lose: lose entire bet
        status = "lost"
    
    cursor.execute("""
        UPDATE positions SET status = ?, exit_price = ?, pnl = ?, resolved_at = ?
        WHERE id = ?
    """, (status, exit_price, pnl, datetime.utcnow().isoformat(), position_id))
    
    # Update stats
    if won:
        cursor.execute("""
            UPDATE stats SET wins = wins + 1, total_pnl = total_pnl + ?, updated_at = ?
        """, (pnl, datetime.utcnow().isoformat()))
    else:
        cursor.execute("""
            UPDATE stats SET losses = losses + 1, total_pnl = total_pnl + ?, updated_at = ?
        """, (pnl, datetime.utcnow().isoformat()))
    
    conn.commit()
    conn.close()


def get_open_positions() -> list[dict]:
    """Get all open positions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_closed_positions(limit: int = 50) -> list[dict]:
    """Get closed positions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM positions WHERE status != 'open' ORDER BY resolved_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_all_positions(limit: int = 100) -> list[dict]:
    """Get all positions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM positions ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def get_stats() -> dict:
    """Get overall stats."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM stats LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if row:
        stats = dict(row)
        if stats['total_bets'] > 0:
            stats['win_rate'] = round(stats['wins'] / stats['total_bets'] * 100, 1)
        else:
            stats['win_rate'] = 0
        return stats
    
    return {"total_bets": 0, "wins": 0, "losses": 0, "total_wagered": 0, "total_pnl": 0, "win_rate": 0}


def reset_db():
    """Reset all data (for testing)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM positions")
    cursor.execute("DELETE FROM signal_predictions")
    cursor.execute("DELETE FROM probability_predictions")
    cursor.execute("UPDATE stats SET total_bets = 0, wins = 0, losses = 0, total_wagered = 0, total_pnl = 0")
    conn.commit()
    conn.close()


# === SIGNAL PREDICTION TRACKING ===

def record_signal_prediction(
    position_id: int,
    signal_name: str,
    signal_value: float,
    signal_confidence: float,
    predicted_direction: str,
    asset: str,
    timing_bucket: Optional[str] = None
) -> int:
    """Record a signal prediction for later accuracy analysis."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO signal_predictions 
        (position_id, signal_name, signal_value, signal_confidence, predicted_direction, asset, timing_bucket)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (position_id, signal_name, signal_value, signal_confidence, predicted_direction, asset, timing_bucket))
    
    pred_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return pred_id


def resolve_signal_predictions(
    position_id: int,
    actual_direction: str,
    pnl: float
):
    """Resolve all signal predictions for a position."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all predictions for this position
    cursor.execute("""
        SELECT id, predicted_direction, signal_value FROM signal_predictions 
        WHERE position_id = ? AND was_correct IS NULL
    """, (position_id,))
    
    rows = cursor.fetchall()
    for row in rows:
        pred_id, predicted_dir, signal_value = row
        
        # Was this signal correct?
        # Signal predicted up (positive value) and actual was up = correct
        # Signal predicted down (negative value) and actual was down = correct
        if abs(signal_value) < 0.1:
            # Neutral signal - mark as correct (didn't make a strong prediction)
            was_correct = 1
            pnl_contrib = 0
        else:
            was_correct = 1 if predicted_dir == actual_direction else 0
            # PnL contribution proportional to signal strength
            pnl_contrib = pnl * abs(signal_value) if was_correct else -abs(pnl) * abs(signal_value)
        
        cursor.execute("""
            UPDATE signal_predictions 
            SET actual_direction = ?, was_correct = ?, pnl_contribution = ?, resolved_at = ?
            WHERE id = ?
        """, (actual_direction, was_correct, pnl_contrib, datetime.utcnow().isoformat(), pred_id))
    
    conn.commit()
    conn.close()


def get_signal_accuracy(signal_name: Optional[str] = None, limit: int = 100) -> dict:
    """Get accuracy statistics for signals."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if signal_name:
        cursor.execute("""
            SELECT signal_name, 
                   COUNT(*) as total,
                   SUM(was_correct) as correct,
                   SUM(pnl_contribution) as total_pnl
            FROM signal_predictions 
            WHERE signal_name = ? AND was_correct IS NOT NULL
            GROUP BY signal_name
        """, (signal_name,))
    else:
        cursor.execute("""
            SELECT signal_name, 
                   COUNT(*) as total,
                   SUM(was_correct) as correct,
                   SUM(pnl_contribution) as total_pnl
            FROM signal_predictions 
            WHERE was_correct IS NOT NULL
            GROUP BY signal_name
        """)
    
    rows = cursor.fetchall()
    conn.close()
    
    result = {}
    for row in rows:
        name = row['signal_name']
        total = row['total']
        correct = row['correct'] or 0
        total_pnl = row['total_pnl'] or 0
        
        result[name] = {
            'total': total,
            'correct': correct,
            'accuracy': round(correct / total * 100, 1) if total > 0 else 0,
            'pnl': round(total_pnl, 2)
        }
    
    return result


def record_probability_prediction(
    position_id: int,
    predicted_prob: float,
    prob_bucket: str,
    asset: str
) -> int:
    """Record a probability prediction for calibration analysis."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO probability_predictions (position_id, predicted_prob, prob_bucket, asset)
        VALUES (?, ?, ?, ?)
    """, (position_id, predicted_prob, prob_bucket, asset))
    
    pred_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return pred_id


def resolve_probability_prediction(position_id: int, won: bool, pnl: float):
    """Resolve a probability prediction."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE probability_predictions 
        SET won = ?, pnl = ?, resolved_at = ?
        WHERE position_id = ? AND won IS NULL
    """, (1 if won else 0, pnl, datetime.utcnow().isoformat(), position_id))
    
    conn.commit()
    conn.close()


def get_probability_calibration() -> dict:
    """Get calibration data by probability bucket."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT prob_bucket,
               COUNT(*) as total,
               SUM(won) as wins,
               AVG(predicted_prob) as avg_predicted,
               SUM(pnl) as total_pnl
        FROM probability_predictions
        WHERE won IS NOT NULL
        GROUP BY prob_bucket
        ORDER BY avg_predicted
    """)
    
    rows = cursor.fetchall()
    conn.close()
    
    result = {}
    for row in rows:
        bucket = row['prob_bucket']
        total = row['total']
        wins = row['wins'] or 0
        avg_pred = row['avg_predicted'] or 0.5
        total_pnl = row['total_pnl'] or 0
        
        result[bucket] = {
            'total': total,
            'wins': wins,
            'actual_rate': round(wins / total * 100, 1) if total > 0 else 50,
            'predicted_rate': round(avg_pred * 100, 1),
            'deviation': round((wins/total - avg_pred) * 100, 1) if total > 0 else 0,
            'pnl': round(total_pnl, 2)
        }
    
    return result


# Initialize on import
init_db()
