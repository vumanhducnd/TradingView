"""
PostgreSQL connection and CRUD operations.
Dùng psycopg2 trực tiếp (không ORM) để dễ hiểu và debug.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Generator

import pandas as pd
import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

from scanner.utils import logger


# ─── Connection ───────────────────────────────────────────────────────────────

def _conn_params() -> dict:
    return {
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", 5432)),
        "dbname":   os.getenv("DB_NAME", "trading"),
        "user":     os.getenv("DB_USER", "trading_user"),
        "password": os.getenv("DB_PASSWORD", "trading_pass"),
    }


def get_connection() -> PgConnection:
    return psycopg2.connect(**_conn_params())


@contextmanager
def db_cursor(commit: bool = True) -> Generator:
    """Context manager: tự động commit/rollback và đóng connection."""
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def test_connection() -> bool:
    try:
        with db_cursor(commit=False) as cur:
            cur.execute("SELECT 1")
        logger.info("PostgreSQL: kết nối OK")
        return True
    except Exception as e:
        logger.error(f"PostgreSQL: kết nối thất bại — {e}")
        return False


# ─── Watchlist ────────────────────────────────────────────────────────────────

def upsert_watchlist(tickers: list[str], exchange: str = "HOSE") -> None:
    sql = """
        INSERT INTO watchlist (ticker, exchange, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET exchange = EXCLUDED.exchange, updated_at = NOW()
    """
    with db_cursor() as cur:
        cur.executemany(sql, [(t, exchange) for t in tickers])
    logger.info(f"Watchlist: upserted {len(tickers)} tickers")


def get_watchlist() -> list[str]:
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT ticker FROM watchlist ORDER BY ticker")
        return [r["ticker"] for r in cur.fetchall()]


# ─── OHLCV ────────────────────────────────────────────────────────────────────

def upsert_ohlcv(ticker: str, df: pd.DataFrame) -> int:
    """
    Insert OHLCV rows. Bỏ qua nếu (ticker, date) đã tồn tại.
    Returns số dòng inserted.
    """
    if df.empty:
        return 0

    sql = """
        INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
        VALUES %s
        ON CONFLICT (ticker, date) DO UPDATE SET
            open   = EXCLUDED.open,
            high   = EXCLUDED.high,
            low    = EXCLUDED.low,
            close  = EXCLUDED.close,
            volume = EXCLUDED.volume
    """
    df = df.reset_index()
    date_col = "date" if "date" in df.columns else df.columns[0]
    rows = [
        (
            ticker,
            row[date_col].date() if hasattr(row[date_col], "date") else row[date_col],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            int(row["volume"]),
        )
        for _, row in df.iterrows()
    ]
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)
    return len(rows)


def load_ohlcv(ticker: str, days: int = 400) -> pd.DataFrame:
    """Load OHLCV từ DB, trả về DataFrame với DatetimeIndex."""
    since = date.today() - timedelta(days=days)
    sql = """
        SELECT date, open, high, low, close, volume
        FROM ohlcv
        WHERE ticker = %s AND date >= %s
        ORDER BY date ASC
    """
    with db_cursor(commit=False) as cur:
        cur.execute(sql, (ticker, since))
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)
    return df


def get_last_date(ticker: str) -> date | None:
    """Trả về ngày mới nhất đã có trong DB cho ticker này."""
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT MAX(date) FROM ohlcv WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
    return row["max"] if row and row["max"] else None


def get_all_last_dates() -> dict[str, date]:
    """Trả về {ticker: last_date} cho tất cả tickers trong DB."""
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT ticker, MAX(date) AS last_date FROM ohlcv GROUP BY ticker")
        return {r["ticker"]: r["last_date"] for r in cur.fetchall()}


# ─── Scan Results ─────────────────────────────────────────────────────────────

def save_scan_results(df: pd.DataFrame, scan_date: date | None = None) -> None:
    """Lưu toàn bộ kết quả quét vào scan_results."""
    if df.empty:
        return
    if scan_date is None:
        scan_date = date.today()

    criteria = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]

    sql = """
        INSERT INTO scan_results (
            scan_date, ticker, close, trend, supertrend, bias_norm,
            b_score, r_score, buy_signal, sell_signal,
            bull_ema, bull_vwap, bull_rsi, bull_macd, bull_adx,
            bull_obv, bull_stoch, bull_candle, bull_vol,
            atr, volume
        ) VALUES %s
        ON CONFLICT (scan_date, ticker) DO UPDATE SET
            close       = EXCLUDED.close,
            trend       = EXCLUDED.trend,
            supertrend  = EXCLUDED.supertrend,
            bias_norm   = EXCLUDED.bias_norm,
            b_score     = EXCLUDED.b_score,
            r_score     = EXCLUDED.r_score,
            buy_signal  = EXCLUDED.buy_signal,
            sell_signal = EXCLUDED.sell_signal,
            bull_ema    = EXCLUDED.bull_ema,   bull_vwap   = EXCLUDED.bull_vwap,
            bull_rsi    = EXCLUDED.bull_rsi,   bull_macd   = EXCLUDED.bull_macd,
            bull_adx    = EXCLUDED.bull_adx,   bull_obv    = EXCLUDED.bull_obv,
            bull_stoch  = EXCLUDED.bull_stoch, bull_candle = EXCLUDED.bull_candle,
            bull_vol    = EXCLUDED.bull_vol,
            atr         = EXCLUDED.atr,
            volume      = EXCLUDED.volume
    """

    def _bool(row, col):
        v = row.get(col)
        return bool(v) if v is not None else False

    def _float(row, col):
        v = row.get(col)
        return float(v) if v is not None else None

    rows = [
        (
            scan_date,
            row.get("ticker"),
            _float(row, "close"),
            int(row.get("trend", 0)),
            _float(row, "supertrend"),
            _float(row, "bias_norm"),
            int(row.get("b_score", 0)),
            int(row.get("r_score", 0)),
            _bool(row, "buy_signal"),
            _bool(row, "sell_signal"),
            *[_bool(row, f"bull_{c}") for c in criteria],
            _float(row, "atr"),
            int(row.get("volume", 0)) if row.get("volume") else None,
        )
        for _, row in df.iterrows()
    ]

    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)
    logger.info(f"scan_results: saved {len(rows)} rows for {scan_date}")


def save_signals(df: pd.DataFrame, scan_date: date | None = None) -> None:
    """Lưu chỉ những mã có tín hiệu MUA/BÁN vào bảng signals."""
    if scan_date is None:
        scan_date = date.today()

    rows = []
    for _, row in df.iterrows():
        if row.get("buy_signal"):
            rows.append((row["ticker"], scan_date, "MUA", row.get("close"),
                         row.get("supertrend"), row.get("bias_norm"), row.get("b_score")))
        if row.get("sell_signal"):
            rows.append((row["ticker"], scan_date, "BÁN", row.get("close"),
                         row.get("supertrend"), row.get("bias_norm"), row.get("b_score")))

    if not rows:
        return

    sql = """
        INSERT INTO signals (ticker, signal_date, signal_type, price, supertrend, bias_norm, b_score)
        VALUES %s
        ON CONFLICT (ticker, signal_date, signal_type) DO NOTHING
    """
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)
    logger.info(f"signals: saved {len(rows)} signals for {scan_date}")


# ─── Query helpers cho Dashboard ──────────────────────────────────────────────

def load_scan_results(scan_date: date) -> pd.DataFrame:
    sql = "SELECT * FROM scan_results WHERE scan_date = %s ORDER BY bias_norm DESC"
    with db_cursor(commit=False) as cur:
        cur.execute(sql, (scan_date,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_signal_history(days: int = 90) -> pd.DataFrame:
    since = date.today() - timedelta(days=days)
    sql = "SELECT * FROM signals WHERE signal_date >= %s ORDER BY signal_date DESC"
    with db_cursor(commit=False) as cur:
        cur.execute(sql, (since,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_scan_dates() -> list[date]:
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT DISTINCT scan_date FROM scan_results ORDER BY scan_date DESC")
        return [r["scan_date"] for r in cur.fetchall()]


def load_open_positions() -> dict[str, dict]:
    """
    Tìm vị thế đang mở: ticker có tín hiệu MUA gần nhất chưa có BÁN sau đó.
    Returns {ticker: {buy_date, buy_price}}
    """
    sql = """
        SELECT DISTINCT ON (ticker)
            ticker,
            signal_date AS buy_date,
            price       AS buy_price
        FROM signals
        WHERE signal_type = 'MUA'
          AND NOT EXISTS (
              SELECT 1 FROM signals s2
              WHERE s2.ticker      = signals.ticker
                AND s2.signal_type = 'BÁN'
                AND s2.signal_date > signals.signal_date
          )
        ORDER BY ticker, signal_date DESC
    """
    with db_cursor(commit=False) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {r["ticker"]: {"buy_date": r["buy_date"], "buy_price": float(r["buy_price"])} for r in rows}


def load_last_signals(tickers: list[str]) -> pd.DataFrame:
    """
    Lấy tín hiệu MUA/BÁN gần nhất của từng ticker.
    """
    sql = """
        SELECT DISTINCT ON (ticker, signal_type)
            ticker, signal_type, signal_date, price
        FROM signals
        WHERE ticker = ANY(%s)
        ORDER BY ticker, signal_type, signal_date DESC
    """
    with db_cursor(commit=False) as cur:
        cur.execute(sql, (tickers,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()
