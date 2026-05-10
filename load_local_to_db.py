"""
Load dữ liệu local CSV lên Neon DB.
Chạy: python load_local_to_db.py
"""

import glob
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2.extras

from scanner.database import db_cursor
from scanner.utils import logger

ROOT = Path(__file__).parent
SIGNALS_DIR = ROOT / "data" / "signals"
BACKTEST_DIR = ROOT / "data" / "backtest"


# ─── 1. scan_results ──────────────────────────────────────────────────────────

def load_signals_csv():
    files = sorted(glob.glob(str(SIGNALS_DIR / "signals_*.csv")))
    if not files:
        logger.info("Không có file signals CSV")
        return

    total = 0
    for file in files:
        scan_date_str = Path(file).stem.replace("signals_", "")
        try:
            scan_date = date.fromisoformat(scan_date_str)
        except ValueError:
            continue

        df = pd.read_csv(file)
        bool_cols = [c for c in df.columns if c.startswith("bull_")]
        for col in bool_cols:
            df[col] = df[col].astype(bool)

        rows = []
        for _, row in df.iterrows():
            rows.append((
                scan_date,
                row.get("ticker"),
                _f(row, "close"),
                None,   # trend — không có trong CSV
                None,   # supertrend
                _f(row, "bias_norm"),
                _i(row, "b_score"),
                _i(row, "r_score"),
                False,  # buy_signal
                False,  # sell_signal
                _b(row, "bull_ema"),
                _b(row, "bull_vwap"),
                _b(row, "bull_rsi"),
                _b(row, "bull_macd"),
                _b(row, "bull_adx"),
                _b(row, "bull_obv"),
                _b(row, "bull_stoch"),
                _b(row, "bull_candle"),
                _b(row, "bull_vol"),
                None,   # atr
                _i(row, "volume"),
            ))

        sql = """
            INSERT INTO scan_results (
                scan_date, ticker, close, trend, supertrend, bias_norm,
                b_score, r_score, buy_signal, sell_signal,
                bull_ema, bull_vwap, bull_rsi, bull_macd, bull_adx,
                bull_obv, bull_stoch, bull_candle, bull_vol,
                atr, volume
            ) VALUES %s
            ON CONFLICT (scan_date, ticker) DO UPDATE SET
                close = EXCLUDED.close, bias_norm = EXCLUDED.bias_norm,
                b_score = EXCLUDED.b_score, r_score = EXCLUDED.r_score,
                bull_ema = EXCLUDED.bull_ema, bull_vwap = EXCLUDED.bull_vwap,
                bull_rsi = EXCLUDED.bull_rsi, bull_macd = EXCLUDED.bull_macd,
                bull_adx = EXCLUDED.bull_adx, bull_obv = EXCLUDED.bull_obv,
                bull_stoch = EXCLUDED.bull_stoch, bull_candle = EXCLUDED.bull_candle,
                bull_vol = EXCLUDED.bull_vol, volume = EXCLUDED.volume
        """
        with db_cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows)

        logger.info(f"scan_results: {scan_date} → {len(rows)} rows")
        total += len(rows)

    logger.info(f"scan_results tong: {total} rows")


# ─── 2. backtest_summary ──────────────────────────────────────────────────────

def load_backtest_summary():
    file = BACKTEST_DIR / "backtest_results.csv"
    if not file.exists():
        logger.info("Khong co backtest_results.csv")
        return

    df = pd.read_csv(file)
    rows = [
        (
            row["ticker"],
            "long",
            _i(row, "total_trades"),
            _i(row, "win_trades"),
            _f(row, "win_rate"),
            _f(row, "total_return_pct"),
            _f(row, "max_drawdown_pct"),
            _f(row, "avg_hold_days"),
        )
        for _, row in df.iterrows()
    ]

    sql = """
        INSERT INTO backtest_summary (
            ticker, style, total_trades, win_trades, win_rate,
            total_return_pct, max_drawdown_pct, avg_hold_days
        ) VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            total_trades     = EXCLUDED.total_trades,
            win_trades       = EXCLUDED.win_trades,
            win_rate         = EXCLUDED.win_rate,
            total_return_pct = EXCLUDED.total_return_pct,
            max_drawdown_pct = EXCLUDED.max_drawdown_pct,
            avg_hold_days    = EXCLUDED.avg_hold_days,
            updated_at       = NOW()
    """
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)

    logger.info(f"backtest_summary: {len(rows)} rows")


# ─── 3. backtest_trades ───────────────────────────────────────────────────────

def load_backtest_trades():
    file = BACKTEST_DIR / "backtest_trades.csv"
    if not file.exists():
        logger.info("Khong co backtest_trades.csv")
        return

    df = pd.read_csv(file)
    df["win"] = df["win"].astype(bool)

    # Xóa trades cũ rồi insert lại để tránh duplicate
    with db_cursor() as cur:
        cur.execute("DELETE FROM backtest_trades WHERE style = 'long'")

    rows = [
        (
            row["ticker"], "long",
            row.get("buy_date"),  row.get("buy_price"),
            row.get("sell_date"), row.get("sell_price"),
            _i(row, "hold_days"),
            _f(row, "pnl_pct"),
            _f(row, "pnl_amount"),
            bool(row.get("win", False)),
        )
        for _, row in df.iterrows()
    ]

    sql = """
        INSERT INTO backtest_trades (
            ticker, style, buy_date, buy_price, sell_date, sell_price,
            hold_days, pnl_pct, pnl_amount, win
        ) VALUES %s
    """
    with db_cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows)

    logger.info(f"backtest_trades: {len(rows)} rows")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _f(row, col):
    v = row.get(col)
    try:
        return float(v) if v is not None and v == v else None
    except (TypeError, ValueError):
        return None

def _i(row, col):
    v = row.get(col)
    try:
        return int(v) if v is not None and v == v else None
    except (TypeError, ValueError):
        return None

def _b(row, col):
    v = row.get(col)
    return bool(v) if v is not None else False


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=== Load local CSV -> Neon DB ===")
    load_signals_csv()
    load_backtest_summary()
    load_backtest_trades()
    logger.info("=== Xong ===")
