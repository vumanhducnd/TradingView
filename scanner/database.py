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


def get_top300_thanh_khoan(n: int = 300) -> list[str]:
    """
    Trả về top N mã theo vn100_rank đã đánh trong watchlist (rank 1 = TK cao nhất).
    Fallback về avg_turnover_20d nếu chưa có rank.
    """
    try:
        with db_cursor(commit=False) as cur:
            # Ưu tiên dùng rank đã đánh từ watchlist_builder
            cur.execute(
                """
                SELECT ticker FROM watchlist
                WHERE vn100_rank IS NOT NULL
                ORDER BY vn100_rank ASC
                LIMIT %s
                """,
                (n,),
            )
            result = [r["ticker"] for r in cur.fetchall()]
            if len(result) >= min(n, 50):
                return result
    except Exception:
        pass

    # Fallback: avg_turnover_20d
    try:
        with db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT ticker FROM watchlist
                WHERE avg_turnover_20d IS NOT NULL AND avg_turnover_20d > 0
                ORDER BY avg_turnover_20d DESC
                LIMIT %s
                """,
                (n,),
            )
            result = [r["ticker"] for r in cur.fetchall()]
            if len(result) >= min(n, 50):
                return result
    except Exception:
        pass

    # Fallback cuối: tính trực tiếp từ ohlcv
    logger.warning("Khong co rank trong watchlist, tinh tu ohlcv...")
    try:
        with db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT ticker FROM ohlcv
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                  AND close > 0 AND volume > 0
                GROUP BY ticker
                HAVING COUNT(*) >= 10
                ORDER BY AVG(volume * close) DESC
                LIMIT %s
                """,
                (n,),
            )
            result = [r["ticker"] for r in cur.fetchall()]
            if result:
                return result
    except Exception as e:
        logger.warning(f"get_top300_thanh_khoan fallback failed: {e}")
    return get_vn100_watchlist()[:n]


def update_liquidity_stats(days: int = 20) -> int:
    """
    Tính avg_turnover_20d và avg_volume_20d cho tất cả ticker trong watchlist,
    lưu lại vào bảng watchlist. Gọi hàng ngày sau khi đóng cửa.
    Trả về số ticker đã update.
    """
    # Tạo cột nếu chưa có
    with db_cursor(commit=True) as cur:
        cur.execute("""
            ALTER TABLE watchlist
                ADD COLUMN IF NOT EXISTS avg_turnover_20d DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS avg_volume_20d   DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS liquidity_updated_at TIMESTAMP
        """)

    # Tính và update
    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE watchlist w
            SET avg_turnover_20d      = stats.avg_turnover,
                avg_volume_20d        = stats.avg_volume,
                liquidity_updated_at  = NOW()
            FROM (
                SELECT ticker,
                       AVG(volume * close) AS avg_turnover,
                       AVG(volume)         AS avg_volume
                FROM ohlcv
                WHERE date >= CURRENT_DATE - INTERVAL '%(days)s days'
                  AND close > 0 AND volume > 0
                GROUP BY ticker
                HAVING COUNT(*) >= %(min_bars)s
            ) stats
            WHERE w.ticker = stats.ticker
            """,
            {"days": days, "min_bars": max(5, days // 2)},
        )
        updated = cur.rowcount

    logger.info(f"update_liquidity_stats: {updated} tickers updated (last {days} days)")
    return updated


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


def bulk_upsert_today(bars: dict[str, dict], today: "date") -> int:
    """
    Upsert bar hôm nay cho nhiều ticker trong 1 lần gọi DB duy nhất.
    bars = {ticker: {open, high, low, close, volume}}
    Trả về số rows upserted.
    """
    if not bars:
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
    rows = [
        (ticker, today,
         float(b["open"]), float(b["high"]), float(b["low"]),
         float(b["close"]), int(b["volume"]))
        for ticker, b in bars.items()
        if b.get("close", 0) > 0
    ]
    if not rows:
        return 0
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
    return len(rows)


def load_all_ohlcv_bulk(
    days: int = 400,
    tickers: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Load OHLCV trong 1 query. tickers=None → toàn bộ watchlist."""
    since = date.today() - timedelta(days=days)
    with db_cursor(commit=False) as cur:
        if tickers:
            cur.execute(
                """
                SELECT ticker, date, open, high, low, close, volume
                FROM ohlcv
                WHERE date >= %s AND ticker = ANY(%s)
                ORDER BY ticker, date ASC
                """,
                (since, tickers),
            )
        else:
            cur.execute(
                """
                SELECT ticker, date, open, high, low, close, volume
                FROM ohlcv
                WHERE date >= %s
                ORDER BY ticker, date ASC
                """,
                (since,),
            )
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
            _date(r, "short_last_signal_date"),
            _v(r, "short_last_signal_price", float),
            _date(r, "long_last_signal_date"),
            _v(r, "long_last_signal_price",  float),
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
            # Siêu cổ phiếu
            _bool(r, "ss1"), _bool(r, "ss2"), _bool(r, "ss3"),
            _bool(r, "ss4"), _bool(r, "ss5"), _bool(r, "ss6"), _bool(r, "ss7"),
            _v(r, "super_score",    int),
            _bool(r, "is_super_stock"),
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
            short_last_signal_date, short_last_signal_price,
            long_last_signal_date, long_last_signal_price,
            long_signal_pnl_pct, short_signal_pnl_pct,
            long_bars_since_signal, short_bars_since_signal,
            both_buy, both_sell,
            buy_date, buy_price, sell_date, sell_price,
            hold_days, pnl_pct, max_loss_pct,
            ss1, ss2, ss3, ss4, ss5, ss6, ss7,
            super_score, is_super_stock
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
            short_last_signal_date = EXCLUDED.short_last_signal_date,
            short_last_signal_price = EXCLUDED.short_last_signal_price,
            long_last_signal_date = EXCLUDED.long_last_signal_date,
            long_last_signal_price = EXCLUDED.long_last_signal_price,
            long_signal_pnl_pct = EXCLUDED.long_signal_pnl_pct,
            short_signal_pnl_pct = EXCLUDED.short_signal_pnl_pct,
            long_bars_since_signal = EXCLUDED.long_bars_since_signal,
            short_bars_since_signal = EXCLUDED.short_bars_since_signal,
            both_buy = EXCLUDED.both_buy, both_sell = EXCLUDED.both_sell,
            buy_date = EXCLUDED.buy_date, buy_price = EXCLUDED.buy_price,
            sell_date = EXCLUDED.sell_date, sell_price = EXCLUDED.sell_price,
            hold_days = EXCLUDED.hold_days, pnl_pct = EXCLUDED.pnl_pct,
            max_loss_pct = EXCLUDED.max_loss_pct,
            ss1 = EXCLUDED.ss1, ss2 = EXCLUDED.ss2, ss3 = EXCLUDED.ss3,
            ss4 = EXCLUDED.ss4, ss5 = EXCLUDED.ss5, ss6 = EXCLUDED.ss6,
            ss7 = EXCLUDED.ss7,
            super_score = EXCLUDED.super_score,
            is_super_stock = EXCLUDED.is_super_stock
    """
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)
    logger.info(f"scan_results: saved {len(rows)} rows for {scan_date}")


def ensure_scan_results_columns() -> None:
    """Thêm các cột mới vào scan_results nếu chưa có."""
    migrations = [
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS short_last_signal_date  DATE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS short_last_signal_price NUMERIC(12,4)",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS long_last_signal_date   DATE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS long_last_signal_price  NUMERIC(12,4)",
        # Siêu cổ phiếu 7 tiêu chí
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss1 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss2 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss3 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss4 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss5 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss6 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS ss7 BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS super_score    SMALLINT DEFAULT 0",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS is_super_stock BOOLEAN DEFAULT FALSE",
    ]
    with db_cursor() as cur:
        for sql in migrations:
            try:
                cur.execute(sql)
            except Exception:
                pass


def ensure_signals_columns() -> None:
    """Thêm các cột mới vào bảng signals nếu chưa có (migration an toàn)."""
    migrations = [
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS style      VARCHAR(10)  DEFAULT 'long'",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS closed     BOOLEAN      DEFAULT FALSE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_date  DATE",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS sell_price NUMERIC(12,4)",
        "ALTER TABLE signals ADD COLUMN IF NOT EXISTS pnl_pct    NUMERIC(8,2)",
    ]
    with db_cursor() as cur:
        for sql in migrations:
            try:
                cur.execute(sql)
            except Exception:
                pass  # cột đã tồn tại


def save_signals(df: pd.DataFrame, scan_date: date | None = None) -> None:
    """
    Lưu tín hiệu MUA/BÁN vào bảng signals. Hỗ trợ dual mode (long + short).
    - Buy signal  → INSERT vị thế MUA mới (closed=FALSE)
    - Sell signal → đóng vị thế MUA mở gần nhất (closed=TRUE, tính P&L) + INSERT record BÁN
    """
    if scan_date is None:
        scan_date = date.today()

    ensure_signals_columns()

    is_dual = "long_buy_signal" in df.columns
    styles = (
        [("long",  "long_buy_signal",  "long_sell_signal",  "long_supertrend"),
         ("short", "short_buy_signal", "short_sell_signal", "short_supertrend")]
        if is_dual else
        [("long",  "buy_signal",       "sell_signal",       "supertrend")]
    )

    total = 0
    with db_cursor() as cur:
        for style, buy_col, sell_col, st_col in styles:
            for _, row in df.iterrows():
                ticker  = row["ticker"]
                close   = float(row.get("close") or 0)
                st      = row.get(st_col) or row.get("supertrend")
                bias    = row.get("bias_norm")
                bscore  = row.get("b_score")

                if row.get(buy_col):
                    cur.execute(
                        """
                        INSERT INTO signals
                            (ticker, signal_date, signal_type, style, price, supertrend, bias_norm, b_score, closed)
                        VALUES (%s,%s,'MUA',%s,%s,%s,%s,%s,FALSE)
                        ON CONFLICT DO NOTHING
                        """,
                        (ticker, scan_date, style, close, st, bias, bscore),
                    )
                    total += 1

                if row.get(sell_col):
                    # Đóng vị thế MUA mở gần nhất cùng style
                    cur.execute(
                        """
                        UPDATE signals
                        SET closed     = TRUE,
                            sell_date  = %s,
                            sell_price = %s,
                            pnl_pct    = ROUND((%s - price) / NULLIF(price,0) * 100, 2)
                        WHERE id = (
                            SELECT id FROM signals
                            WHERE ticker = %s AND style = %s
                              AND signal_type = 'MUA' AND closed = FALSE
                            ORDER BY signal_date DESC
                            LIMIT 1
                        )
                        """,
                        (scan_date, close, close, ticker, style),
                    )
                    # Lưu record BÁN để tra cứu lịch sử
                    cur.execute(
                        """
                        INSERT INTO signals
                            (ticker, signal_date, signal_type, style, price, supertrend, bias_norm, b_score, closed)
                        VALUES (%s,%s,'BÁN',%s,%s,%s,%s,%s,TRUE)
                        ON CONFLICT DO NOTHING
                        """,
                        (ticker, scan_date, style, close, st, bias, bscore),
                    )
                    total += 1

    logger.info(f"signals: {total} bản ghi cho {scan_date} (dual={is_dual})")


# ─── Query helpers cho Dashboard ──────────────────────────────────────────────

def load_avg_turnover(tickers: list[str]) -> dict[str, float]:
    """Lấy avg_turnover_20d (VND/1000) từ watchlist cho danh sách tickers."""
    if not tickers:
        return {}
    with db_cursor(commit=False) as cur:
        cur.execute(
            "SELECT ticker, avg_turnover_20d FROM watchlist WHERE ticker = ANY(%s)",
            (tickers,),
        )
        return {r["ticker"]: float(r["avg_turnover_20d"] or 0) for r in cur.fetchall()}


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
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_scan_dates() -> list[date]:
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT DISTINCT scan_date FROM scan_results ORDER BY scan_date DESC")
        return [r["scan_date"] for r in cur.fetchall()]


def load_open_positions(style: str | None = None) -> list[dict]:
    """
    Trả về danh sách vị thế MUA đang mở (closed=FALSE).
    Mỗi dict: ticker, style, signal_date, price, pnl_pct (chưa tính vì chưa đóng).
    """
    sql = """
        SELECT ticker, style, signal_date, price AS buy_price, bias_norm, b_score
        FROM signals
        WHERE signal_type = 'MUA' AND closed = FALSE
        {style_filter}
        ORDER BY signal_date DESC
    """.format(style_filter="AND style = %(style)s" if style else "")
    with db_cursor(commit=False) as cur:
        cur.execute(sql, {"style": style} if style else {})
        return [dict(r) for r in cur.fetchall()]


def load_last_signals(tickers: list[str], style: str | None = None) -> pd.DataFrame:
    """
    Lấy tín hiệu MUA đang mở gần nhất của từng ticker (closed=FALSE).
    Dùng để gắn buy_date/buy_price vào kết quả scan.
    """
    style_filter = "AND style = %(style)s" if style else ""
    sql = f"""
        SELECT DISTINCT ON (ticker)
            ticker, style, signal_date, price
        FROM signals
        WHERE ticker = ANY(%(tickers)s)
          AND signal_type = 'MUA' AND closed = FALSE
          {style_filter}
        ORDER BY ticker, signal_date DESC
    """
    with db_cursor(commit=False) as cur:
        cur.execute(sql, {"tickers": tickers, "style": style})
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()
