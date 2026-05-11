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

def get_connection() -> PgConnection:
    # Ưu tiên DATABASE_URL (Neon / cloud) — SSL đã có sẵn trong URL
    dsn = os.getenv("DATABASE_URL", "").strip()
    if dsn:
        return psycopg2.connect(dsn)

    # Fallback: individual params (Docker local)
    params: dict = {
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", 5432)),
        "dbname":   os.getenv("DB_NAME", "trading"),
        "user":     os.getenv("DB_USER", "trading_user"),
        "password": os.getenv("DB_PASSWORD", "trading_pass"),
    }
    sslmode = os.getenv("DB_SSLMODE")
    if sslmode:
        params["sslmode"] = sslmode
    return psycopg2.connect(**params)


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

def upsert_watchlist(
    tickers: list[str] | list[tuple[str, str]],
    exchange: str = "HOSE",
) -> None:
    """
    tickers: list[str] → lưu vn100_rank theo vị trí trong list (index+1)
             list[tuple(ticker, exchange)] → exchange riêng, rank theo vị trí
    """
    # Đảm bảo cột tồn tại (migration safe cho DB cũ)
    with db_cursor() as cur:
        cur.execute("ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS vn100_rank INT")

    sql = """
        INSERT INTO watchlist (ticker, exchange, vn100_rank, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
            exchange   = EXCLUDED.exchange,
            vn100_rank = EXCLUDED.vn100_rank,
            updated_at = NOW()
    """
    if tickers and isinstance(tickers[0], tuple):
        rows = [(t, ex, i + 1) for i, (t, ex) in enumerate(tickers)]
    else:
        rows = [(t, exchange, i + 1) for i, t in enumerate(tickers)]

    with db_cursor() as cur:
        cur.executemany(sql, rows)
    logger.info(f"Watchlist: upserted {len(rows)} tickers (rank 1–{len(rows)})")


def get_watchlist() -> list[str]:
    """Trả về danh sách ticker, ưu tiên VN100 rank trước, còn lại alpha."""
    with db_cursor(commit=False) as cur:
        cur.execute(
            "SELECT ticker FROM watchlist ORDER BY vn100_rank NULLS LAST, ticker"
        )
        return [r["ticker"] for r in cur.fetchall()]


def get_vn100_watchlist() -> list[str]:
    """Chỉ trả về các mã có vn100_rank (top VN100), theo thứ tự rank."""
    with db_cursor(commit=False) as cur:
        cur.execute(
            "SELECT ticker FROM watchlist WHERE vn100_rank IS NOT NULL ORDER BY vn100_rank"
        )
        return [r["ticker"] for r in cur.fetchall()]


def get_top_liquid_tickers(n: int = 300, days: int = 20) -> list[str]:
    """
    Trả về top N mã thanh khoản cao nhất dựa trên trung bình turnover (volume*close)
    trong N ngày giao dịch gần nhất. Fallback về VN100 nếu DB không đủ data.
    """
    try:
        with db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT ticker
                FROM ohlcv
                WHERE date >= CURRENT_DATE - INTERVAL '%(days)s days'
                  AND close > 0 AND volume > 0
                GROUP BY ticker
                HAVING COUNT(*) >= %(min_bars)s
                ORDER BY AVG(volume * close) DESC
                LIMIT %(n)s
                """,
                {"days": days, "min_bars": max(5, days // 2), "n": n},
            )
            result = [r["ticker"] for r in cur.fetchall()]
            if result:
                return result
    except Exception as e:
        logger.warning(f"get_top_liquid_tickers failed: {e}")
    # Fallback
    return get_vn100_watchlist()


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


def load_all_ohlcv_bulk(days: int = 400) -> dict[str, pd.DataFrame]:
    """Load OHLCV toàn bộ ticker trong 1 query — nhanh hơn gọi từng ticker."""
    since = date.today() - timedelta(days=days)
    sql = """
        SELECT ticker, date, open, high, low, close, volume
        FROM ohlcv
        WHERE date >= %s
        ORDER BY ticker, date ASC
    """
    with db_cursor(commit=False) as cur:
        cur.execute(sql, (since,))
        rows = cur.fetchall()

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(int)

    raw = {}
    for ticker, group in df.groupby("ticker"):
        g = group.set_index("date").sort_index()[["open", "high", "low", "close", "volume"]]
        if len(g) >= 50:
            raw[str(ticker)] = g

    # Sắp xếp theo vn100_rank để scan ưu tiên đúng thứ tự
    ordered_tickers = get_watchlist()
    result = {t: raw[t] for t in ordered_tickers if t in raw}
    # Thêm các ticker không có trong watchlist (nếu có) vào cuối
    for t in raw:
        if t not in result:
            result[t] = raw[t]

    logger.info(f"Bulk load OHLCV: {len(result)} tickers, {len(df):,} rows")
    return result


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
    """Lưu toàn bộ kết quả quét vào scan_results — đồng bộ đầy đủ các cột."""
    if df.empty:
        return
    if scan_date is None:
        scan_date = date.today()

    def _v(row, col, cast=None):
        v = row.get(col)
        try:
            if v is None or pd.isna(v):  # handles None, float NaN, NaT
                return None
        except (TypeError, ValueError):
            pass
        try:
            return cast(v) if cast else v
        except Exception:
            return None

    def _date(row, col):
        v = row.get(col)
        try:
            if v is None or pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        return v

    def _bool(row, col):
        v = row.get(col)
        return bool(v) if v is not None else False

    criteria = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]

    # Xác định style: single hoặc dual
    is_dual = "long_buy_signal" in df.columns

    rows = []
    for _, row in df.iterrows():
        r = dict(row)
        rows.append((
            scan_date,
            r.get("ticker"),
            _v(r, "close",      float),
            _v(r, "trend",      int)   if not is_dual else None,
            _v(r, "supertrend", float) if not is_dual else None,
            _v(r, "bias_norm",  float),
            _v(r, "b_score",    int),
            _v(r, "r_score",    int),
            _bool(r, "buy_signal")  if not is_dual else None,
            _bool(r, "sell_signal") if not is_dual else None,
            *[_bool(r, f"bull_{c}") for c in criteria],
            _v(r, "atr",    float),
            _v(r, "volume", int),
            # Cột mới
            _v(r, "support",           float),
            _v(r, "resistance",        float),
            _v(r, "turnover",          float),
            # Dual mode: ưu tiên long_, fallback sang field gốc
            r.get("long_last_signal_type") or r.get("last_signal_type"),
            _date(r, "long_last_signal_date") or _date(r, "last_signal_date"),
            _v(r, "long_last_signal_price",  float) or _v(r, "last_signal_price", float),
            _v(r, "long_bars_since_signal",  int)   or _v(r, "bars_since_signal",  int),
            _v(r, "long_signal_pnl_pct",     float) or _v(r, "signal_pnl_pct",     float),
            r.get("bias_label"),
            # Dual mode
            _v(r, "long_trend",  int),
            _v(r, "short_trend", int),
            _bool(r, "long_buy_signal"),
            _bool(r, "short_buy_signal"),
            _bool(r, "long_sell_signal"),
            _bool(r, "short_sell_signal"),
            _v(r, "long_supertrend",  float),
            _v(r, "short_supertrend", float),
            r.get("long_last_signal_type"),
            r.get("short_last_signal_type"),
            _v(r, "long_signal_pnl_pct",  float),
            _v(r, "short_signal_pnl_pct", float),
            _v(r, "long_bars_since_signal",  int),
            _v(r, "short_bars_since_signal", int),
            _bool(r, "both_buy"),
            _bool(r, "both_sell"),
            # Lịch sử lệnh
            _date(r, "buy_date"),
            _v(r, "buy_price",  float),
            _date(r, "sell_date"),
            _v(r, "sell_price", float),
            _v(r, "hold_days",  int),
            _v(r, "pnl_pct",    float),
            _v(r, "max_loss_pct", float),
        ))

    sql = """
        INSERT INTO scan_results (
            scan_date, ticker, close, trend, supertrend, bias_norm,
            b_score, r_score, buy_signal, sell_signal,
            bull_ema, bull_vwap, bull_rsi, bull_macd, bull_adx,
            bull_obv, bull_stoch, bull_candle, bull_vol,
            atr, volume,
            support, resistance, turnover,
            last_signal_type, last_signal_date, last_signal_price,
            bars_since_signal, signal_pnl_pct, bias_label,
            long_trend, short_trend,
            long_buy_signal, short_buy_signal, long_sell_signal, short_sell_signal,
            long_supertrend, short_supertrend,
            long_last_signal_type, short_last_signal_type,
            long_signal_pnl_pct, short_signal_pnl_pct,
            long_bars_since_signal, short_bars_since_signal,
            both_buy, both_sell,
            buy_date, buy_price, sell_date, sell_price,
            hold_days, pnl_pct, max_loss_pct
        ) VALUES %s
        ON CONFLICT (scan_date, ticker) DO UPDATE SET
            close = EXCLUDED.close, bias_norm = EXCLUDED.bias_norm,
            b_score = EXCLUDED.b_score, r_score = EXCLUDED.r_score,
            support = EXCLUDED.support, resistance = EXCLUDED.resistance,
            turnover = EXCLUDED.turnover,
            last_signal_type = EXCLUDED.last_signal_type,
            last_signal_date = EXCLUDED.last_signal_date,
            last_signal_price = EXCLUDED.last_signal_price,
            bars_since_signal = EXCLUDED.bars_since_signal,
            signal_pnl_pct = EXCLUDED.signal_pnl_pct,
            bias_label = EXCLUDED.bias_label,
            long_trend = EXCLUDED.long_trend, short_trend = EXCLUDED.short_trend,
            long_buy_signal = EXCLUDED.long_buy_signal,
            short_buy_signal = EXCLUDED.short_buy_signal,
            long_sell_signal = EXCLUDED.long_sell_signal,
            short_sell_signal = EXCLUDED.short_sell_signal,
            long_supertrend = EXCLUDED.long_supertrend,
            short_supertrend = EXCLUDED.short_supertrend,
            long_last_signal_type = EXCLUDED.long_last_signal_type,
            short_last_signal_type = EXCLUDED.short_last_signal_type,
            long_signal_pnl_pct = EXCLUDED.long_signal_pnl_pct,
            short_signal_pnl_pct = EXCLUDED.short_signal_pnl_pct,
            long_bars_since_signal = EXCLUDED.long_bars_since_signal,
            short_bars_since_signal = EXCLUDED.short_bars_since_signal,
            both_buy = EXCLUDED.both_buy, both_sell = EXCLUDED.both_sell,
            buy_date = EXCLUDED.buy_date, buy_price = EXCLUDED.buy_price,
            sell_date = EXCLUDED.sell_date, sell_price = EXCLUDED.sell_price,
            hold_days = EXCLUDED.hold_days, pnl_pct = EXCLUDED.pnl_pct,
            max_loss_pct = EXCLUDED.max_loss_pct
    """
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
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Convert Decimal → float (pandas 2.x compatible)
    for col in df.columns:
        if df[col].dtype == object:
            try:
                converted = pd.to_numeric(df[col], errors="coerce")
                if converted.notna().any():
                    df[col] = converted
            except Exception:
                pass

    # Convert boolean columns (float 0.0/1.0 → bool)
    bool_cols = [c for c in df.columns if any(x in c for x in [
        "buy_signal", "sell_signal", "both_buy", "both_sell",
        "bull_", "bear_",
    ])]
    for col in bool_cols:
        if col in df.columns:
            df[col] = df[col].fillna(False).astype(bool)

    # Convert numeric columns
    numeric_cols = ["close", "bias_norm", "supertrend", "support", "resistance",
                    "turnover", "atr", "last_signal_price", "signal_pnl_pct",
                    "long_supertrend", "short_supertrend",
                    "long_signal_pnl_pct", "short_signal_pnl_pct",
                    "buy_price", "sell_price", "pnl_pct", "max_loss_pct"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    int_cols = ["b_score", "r_score", "trend", "long_trend", "short_trend",
                "bars_since_signal", "long_bars_since_signal", "short_bars_since_signal",
                "hold_days", "volume"]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


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
