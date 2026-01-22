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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
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
        INSERT INTO positions (market_id, market_name, asset, side, entry_price, amount_usd, shares, target_price, start_time, end_time, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        position.market_id, position.market_name, position.asset, position.side,
        position.entry_price, position.amount_usd, position.shares, position.target_price,
        position.start_time, position.end_time, position.status
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
    cursor.execute("UPDATE stats SET total_bets = 0, wins = 0, losses = 0, total_wagered = 0, total_pnl = 0")
    conn.commit()
    conn.close()


# Initialize on import
init_db()
