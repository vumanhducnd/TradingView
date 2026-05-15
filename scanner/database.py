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
    """Lưu kết quả quét — 1 dòng/ticker (OVERWRITE), không giữ lịch sử theo ngày."""
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

    today = scan_date or date.today()
    rows = []
    for _, row in df.iterrows():
        r = dict(row)
        rows.append((
            r.get("ticker"),
            today,
            _v(r, "close",    float),
            _v(r, "bias_norm", float),
            _v(r, "b_score",  int),
            _v(r, "r_score",  int),
            *[_bool(r, f"bull_{c}") for c in criteria],
            _v(r, "volume",   int),
            _v(r, "turnover", float),
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
            _date(r, "long_last_signal_date"),
            _v(r, "long_last_signal_price",  float),
            _date(r, "short_last_signal_date"),
            _v(r, "short_last_signal_price", float),
            _v(r, "long_signal_pnl_pct",     float),
            _v(r, "short_signal_pnl_pct",    float),
            _v(r, "long_bars_since_signal",  int),
            _v(r, "short_bars_since_signal", int),
            _bool(r, "both_buy"),
            _bool(r, "both_sell"),
            # Siêu cổ phiếu
            _bool(r, "ss1"), _bool(r, "ss2"), _bool(r, "ss3"),
            _bool(r, "ss4"), _bool(r, "ss5"), _bool(r, "ss6"), _bool(r, "ss7"),
            _v(r, "super_score",    int),
            _bool(r, "is_super_stock"),
        ))

    sql = """
        INSERT INTO scan_results (
            ticker, updated_at, close, bias_norm, b_score, r_score,
            bull_ema, bull_vwap, bull_rsi, bull_macd, bull_adx,
            bull_obv, bull_stoch, bull_candle, bull_vol,
            volume, turnover,
            long_trend, short_trend,
            long_buy_signal, short_buy_signal, long_sell_signal, short_sell_signal,
            long_supertrend, short_supertrend,
            long_last_signal_type, short_last_signal_type,
            long_last_signal_date, long_last_signal_price,
            short_last_signal_date, short_last_signal_price,
            long_signal_pnl_pct, short_signal_pnl_pct,
            long_bars_since_signal, short_bars_since_signal,
            both_buy, both_sell,
            ss1, ss2, ss3, ss4, ss5, ss6, ss7,
            super_score, is_super_stock
        ) VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            updated_at = EXCLUDED.updated_at,
            close = EXCLUDED.close, bias_norm = EXCLUDED.bias_norm,
            b_score = EXCLUDED.b_score, r_score = EXCLUDED.r_score,
            volume = EXCLUDED.volume, turnover = EXCLUDED.turnover,
            long_trend = EXCLUDED.long_trend, short_trend = EXCLUDED.short_trend,
            long_buy_signal = EXCLUDED.long_buy_signal,
            short_buy_signal = EXCLUDED.short_buy_signal,
            long_sell_signal = EXCLUDED.long_sell_signal,
            short_sell_signal = EXCLUDED.short_sell_signal,
            long_supertrend = EXCLUDED.long_supertrend,
            short_supertrend = EXCLUDED.short_supertrend,
            long_last_signal_type = EXCLUDED.long_last_signal_type,
            short_last_signal_type = EXCLUDED.short_last_signal_type,
            long_last_signal_date = EXCLUDED.long_last_signal_date,
            long_last_signal_price = EXCLUDED.long_last_signal_price,
            short_last_signal_date = EXCLUDED.short_last_signal_date,
            short_last_signal_price = EXCLUDED.short_last_signal_price,
            long_signal_pnl_pct = EXCLUDED.long_signal_pnl_pct,
            short_signal_pnl_pct = EXCLUDED.short_signal_pnl_pct,
            long_bars_since_signal = EXCLUDED.long_bars_since_signal,
            short_bars_since_signal = EXCLUDED.short_bars_since_signal,
            both_buy = EXCLUDED.both_buy, both_sell = EXCLUDED.both_sell,
            ss1 = EXCLUDED.ss1, ss2 = EXCLUDED.ss2, ss3 = EXCLUDED.ss3,
            ss4 = EXCLUDED.ss4, ss5 = EXCLUDED.ss5, ss6 = EXCLUDED.ss6,
            ss7 = EXCLUDED.ss7,
            super_score = EXCLUDED.super_score,
            is_super_stock = EXCLUDED.is_super_stock
    """
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)
    logger.info(f"scan_results: upserted {len(rows)} tickers (updated_at={today})")


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


def _ensure_signals_schema() -> None:
    """Migration: đơn giản hoá bảng signals — chỉ giữ 4 cột thiết yếu."""
    drop_cols = ["price", "supertrend", "bias_norm", "b_score",
                 "closed", "sell_date", "sell_price", "pnl_pct"]
    with db_cursor() as cur:
        # Thêm cột style nếu chưa có (DB cũ)
        cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS style        VARCHAR(10)   DEFAULT 'long'")
        cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_price NUMERIC(12,4)")
        cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS signal_st    NUMERIC(12,4)")
        cur.execute("ALTER TABLE signals ADD COLUMN IF NOT EXISTS is_fakeout   BOOLEAN       DEFAULT FALSE")
        for col in drop_cols:
            try:
                cur.execute(f"ALTER TABLE signals DROP COLUMN IF EXISTS {col}")
            except Exception:
                pass
        # Unique constraint
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'signals_ticker_date_type_style_key'
                ) THEN
                    ALTER TABLE signals
                    ADD CONSTRAINT signals_ticker_date_type_style_key
                    UNIQUE (ticker, signal_date, signal_type, style);
                END IF;
            END$$;
        """)


def save_signals(df: pd.DataFrame, scan_date: date | None = None) -> None:
    """
    Lưu tín hiệu MUA/BÁN vào bảng signals.
    Mục đích duy nhất: chống alert trùng giữa phiên sáng/chiều trong cùng ngày.
    """
    if scan_date is None:
        scan_date = date.today()

    _ensure_signals_schema()

    is_dual = "long_buy_signal" in df.columns
    styles = (
        [("long",  "long_buy_signal",  "long_sell_signal"),
         ("short", "short_buy_signal", "short_sell_signal")]
        if is_dual else
        [("long",  "buy_signal",       "sell_signal")]
    )

    total = 0
    with db_cursor() as cur:
        for style, buy_col, sell_col in styles:
            for _, row in df.iterrows():
                ticker = row["ticker"]
                if row.get(buy_col):
                    cur.execute(
                        """
                        INSERT INTO signals (ticker, signal_date, signal_type, style)
                        VALUES (%s, %s, 'MUA', %s)
                        ON CONFLICT ON CONSTRAINT signals_ticker_date_type_style_key DO NOTHING
                        """,
                        (ticker, scan_date, style),
                    )
                    total += 1
                if row.get(sell_col):
                    cur.execute(
                        """
                        INSERT INTO signals (ticker, signal_date, signal_type, style)
                        VALUES (%s, %s, 'BÁN', %s)
                        ON CONFLICT ON CONSTRAINT signals_ticker_date_type_style_key DO NOTHING
                        """,
                        (ticker, scan_date, style),
                    )
                    total += 1

    logger.info(f"signals: {total} bản ghi cho {scan_date} (dual={is_dual})")
    _purge_old_signals(keep_days=3)


def _purge_old_signals(keep_days: int = 3) -> None:
    """Xóa signals cũ hơn keep_days ngày, chỉ giữ lại dữ liệu gần nhất."""
    try:
        with db_cursor() as cur:
            cur.execute(
                "DELETE FROM signals WHERE signal_date < CURRENT_DATE - %s::int",
                (keep_days,),
            )
            deleted = cur.rowcount
        if deleted:
            logger.info(f"signals: da xoa {deleted} ban ghi cu (> {keep_days} ngay)")
    except Exception as e:
        logger.warning(f"_purge_old_signals failed: {e}")


# ─── Query helpers cho Dashboard ──────────────────────────────────────────────

def load_daily_change(tickers: list[str]) -> dict[str, float]:
    """
    Tính % thay đổi giá hôm nay so với hôm qua từ bảng ohlcv.
    Trả về {ticker: pct_change}.
    """
    if not tickers:
        return {}
    try:
        with db_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT ticker,
                       close,
                       LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS prev_close
                FROM ohlcv
                WHERE ticker = ANY(%s)
                  AND date >= CURRENT_DATE - INTERVAL '5 days'
                ORDER BY ticker, date DESC
                """,
                (tickers,),
            )
            rows = cur.fetchall()

        result: dict[str, float] = {}
        for r in rows:
            t = r["ticker"]
            if t in result:
                continue
            c, p = float(r["close"] or 0), float(r["prev_close"] or 0)
            if c > 0 and p > 0:
                result[t] = round((c - p) / p * 100, 2)
        return result
    except Exception as e:
        logger.debug(f"load_daily_change: {e}")
        return {}


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


def load_scan_results(scan_date: date | None = None) -> pd.DataFrame:
    """Load kết quả scan mới nhất (1 dòng/ticker, không phân biệt ngày)."""
    sql = "SELECT * FROM scan_results ORDER BY bias_norm DESC"
    with db_cursor(commit=False) as cur:
        cur.execute(sql)
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


def load_trade_history_from_ohlcv(style: str = "long", tickers: list[str] | None = None) -> pd.DataFrame:
    """
    Tính SuperTrend từ OHLCV, trả về 1 giao dịch gần nhất (vùng xanh gần nhất) mỗi mã.
    Điểm mua = bar trend flip -1→1, điểm bán = bar trend flip 1→-1 kế tiếp (hoặc None nếu đang giữ).
    """
    from scanner.indicators import calc_supertrend

    try:
        # ohlcv không có cột turnover — chỉ lấy OHLCV để tính SuperTrend
        sql = (
            "SELECT ticker, date, open, high, low, close, volume "
            "FROM ohlcv "
            + ("WHERE ticker = ANY(%s) " if tickers else "")
            + "ORDER BY ticker, date ASC"
        )
        with db_cursor(commit=False) as cur:
            cur.execute(sql, (tickers,) if tickers else ())
            rows = cur.fetchall()
    except Exception as e:
        logger.warning(f"load_trade_history_from_ohlcv: query failed: {e}")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    all_ohlcv = pd.DataFrame(rows)
    records: list[dict] = []

    for ticker, grp in all_ohlcv.groupby("ticker", sort=False):
        df = grp.sort_values("date").reset_index(drop=True)
        if len(df) < 20:
            continue
        try:
            for col in ("open", "high", "low", "close", "volume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = calc_supertrend(df, style=style)
        except Exception as e:
            logger.warning(f"load_trade_history_from_ohlcv: calc_supertrend failed [{ticker}]: {e}")
            continue

        buy_idx_series = df.index[df["buy_signal"] == True]
        if len(buy_idx_series) == 0:
            continue

        last_buy_idx  = buy_idx_series[-1]
        buy_date  = df.loc[last_buy_idx, "date"]
        buy_price = float(df.loc[last_buy_idx, "close"])

        sell_after = df[(df.index > last_buy_idx) & (df["sell_signal"] == True)]
        if sell_after.empty:
            sell_date  = None
            sell_price = None
            status     = "Dang giu"
        else:
            first_sell = sell_after.iloc[0]
            sell_date  = first_sell["date"]
            sell_price = float(first_sell["close"])
            status     = "Da dong"

        records.append({
            "ticker":     ticker,
            "buy_date":   buy_date,
            "buy_price":  buy_price,
            "sell_date":  sell_date,
            "sell_price": sell_price,
            "status":     status,
        })

    if not records:
        return pd.DataFrame()

    result = pd.DataFrame(records)
    mask = result["sell_price"].notna() & (result["buy_price"].astype(float) > 0)
    result["pnl_pct"] = None
    result.loc[mask, "pnl_pct"] = (
        (result.loc[mask, "sell_price"].astype(float) - result.loc[mask, "buy_price"].astype(float))
        / result.loc[mask, "buy_price"].astype(float) * 100
    ).round(2)
    return result


def load_trade_history(style: str = "long", tickers: list[str] | None = None) -> pd.DataFrame:
    """
    Mỗi mã: 1 giao dịch gần nhất = lệnh MUA gần nhất + lệnh BÁN đầu tiên sau đó.
    Không giới hạn ngày để không bỏ sót lệnh mua cũ.
    Giá lấy từ ohlcv (close của ngày tín hiệu).
    """
    ticker_filter = "AND s.ticker = ANY(%(tickers)s)" if tickers else ""
    sql = f"""
        WITH sig_prices AS (
            SELECT s.ticker,
                   s.signal_date,
                   s.signal_type,
                   o.close AS price
            FROM signals s
            LEFT JOIN ohlcv o
                   ON o.ticker = s.ticker AND o.date = s.signal_date
            WHERE s.style = %(style)s
              {ticker_filter}
        ),
        latest_buy AS (
            SELECT DISTINCT ON (ticker)
                   ticker,
                   signal_date AS buy_date,
                   price        AS buy_price
            FROM sig_prices
            WHERE signal_type = 'MUA'
            ORDER BY ticker, signal_date DESC
        ),
        next_sell AS (
            SELECT DISTINCT ON (b.ticker)
                   b.ticker,
                   s.signal_date AS sell_date,
                   s.price        AS sell_price
            FROM latest_buy b
            JOIN sig_prices s
                 ON s.ticker      = b.ticker
                AND s.signal_date > b.buy_date
                AND s.signal_type != 'MUA'
            ORDER BY b.ticker, s.signal_date ASC
        )
        SELECT b.ticker,
               b.buy_date,
               b.buy_price,
               s.sell_date,
               s.sell_price
        FROM latest_buy b
        LEFT JOIN next_sell s ON s.ticker = b.ticker
        ORDER BY b.ticker
    """
    try:
        params: dict = {"style": style}
        if tickers:
            params["tickers"] = tickers
        with db_cursor(commit=False) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        mask = df["sell_price"].notna() & df["buy_price"].notna() & (df["buy_price"].astype(float) > 0)
        df["pnl_pct"] = None
        df.loc[mask, "pnl_pct"] = (
            (df.loc[mask, "sell_price"].astype(float) - df.loc[mask, "buy_price"].astype(float))
            / df.loc[mask, "buy_price"].astype(float) * 100
        ).round(2)
        df["status"] = df["sell_date"].apply(lambda x: "Da dong" if pd.notna(x) else "Dang giu")
        return df
    except Exception as e:
        logger.warning(f"load_trade_history failed: {e}")
        return pd.DataFrame()


def load_scan_dates() -> list[date]:
    """Trả về [updated_at] duy nhất từ scan_results (luôn là ngày scan gần nhất)."""
    with db_cursor(commit=False) as cur:
        cur.execute("SELECT MAX(updated_at) as d FROM scan_results")
        r = cur.fetchone()
        return [r["d"]] if r and r["d"] else []


