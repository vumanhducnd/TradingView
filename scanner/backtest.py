"""
Backtest engine replicating ManhDucCapital.pine backtest logic.
State machine: FLAT → (buy_signal) → LONG → (sell_signal) → FLAT
No stop-loss. One position at a time.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from pathlib import Path

from scanner.config import BACKTEST_DIR, COMPOUND_MODE, DEFAULT_CAPITAL, ENTRY_MODE
from scanner.indicators import calc_supertrend, calc_bias_norm
from scanner.utils import logger


def run_backtest(
    ticker: str,
    df: pd.DataFrame,
    style: str = "long",
    initial_capital: float = DEFAULT_CAPITAL,
    compound: bool = COMPOUND_MODE,
    entry_mode: str = ENTRY_MODE,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """
    Replay buy/sell signals on historical data.
    date_from/date_to: lọc khoảng thời gian (YYYY-MM-DD).
    Returns summary dict + list of trades.
    """
    df = calc_supertrend(df, style=style)
    df = calc_bias_norm(df)
    df = df.dropna(subset=["buy_signal", "sell_signal"])

    if date_from:
        df = df[df.index >= pd.Timestamp(date_from)]
    if date_to:
        df = df[df.index <= pd.Timestamp(date_to)]

    trades = []
    capital = initial_capital
    max_capital = initial_capital
    max_drawdown = 0.0
    entry_price = None
    entry_date = None
    entry_bar = None

    for i, (dt, row) in enumerate(df.iterrows()):
        # Enter LONG
        if entry_price is None and row["buy_signal"]:
            entry_price = _buy_price(row, entry_mode)
            entry_date = dt
            entry_bar = i
            continue

        # Exit LONG
        if entry_price is not None and row["sell_signal"]:
            exit_price = _sell_price(row, entry_mode)
            hold_days = i - entry_bar
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            base = capital if compound else initial_capital
            pnl_amount = base * pnl_pct / 100

            capital += pnl_amount
            max_capital = max(max_capital, capital)
            dd = (capital - max_capital) / max_capital * 100
            max_drawdown = min(max_drawdown, dd)

            trades.append({
                "ticker": ticker,
                "buy_date": str(entry_date)[:10],
                "buy_price": round(entry_price, 2),
                "sell_date": str(dt)[:10],
                "sell_price": round(exit_price, 2),
                "hold_days": hold_days,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_amount": round(pnl_amount, 0),
                "capital_after": round(capital, 0),
                "win": pnl_pct > 0,
            })
            entry_price = None
            entry_date = None
            entry_bar = None

    total_trades = len(trades)
    win_trades = sum(1 for t in trades if t["win"])
    total_return = (capital - initial_capital) / initial_capital * 100

    # Thanh khoản trung bình 20 phiên gần nhất (volume × close × 1000 / 1e9 = tỷ VND)
    try:
        last20 = df.tail(20)
        avg_tk_ty = round((last20["volume"] * last20["close"] * 1000).mean() / 1e9, 2)
    except Exception:
        avg_tk_ty = 0.0

    return {
        "ticker": ticker,
        "avg_tk_20p_ty": avg_tk_ty,
        "total_trades": total_trades,
        "win_trades": win_trades,
        "loss_trades": total_trades - win_trades,
        "win_rate": round(win_trades / total_trades * 100, 1) if total_trades else 0,
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "avg_hold_days": round(np.mean([t["hold_days"] for t in trades]), 1) if trades else 0,
        "final_capital": round(capital, 0),
        "initial_capital": initial_capital,
        "trades": trades,
    }


def run_portfolio_backtest(
    ticker_data: dict[str, pd.DataFrame],
    style: str = "long",
    initial_capital: float = DEFAULT_CAPITAL,
    compound: bool = COMPOUND_MODE,
    entry_mode: str = ENTRY_MODE,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """
    Run backtest for all tickers. Saves results to CSV. Returns summary DataFrame.
    """
    all_trades = []
    summaries = []

    for ticker, df in ticker_data.items():
        try:
            result = run_backtest(ticker, df, style, initial_capital, compound, entry_mode,
                                  date_from=date_from, date_to=date_to)
            summaries.append({k: v for k, v in result.items() if k != "trades"})
            all_trades.extend(result["trades"])
        except Exception as e:
            logger.warning(f"Backtest failed for {ticker}: {e}")

    summary_df = pd.DataFrame(summaries)
    trades_df = pd.DataFrame(all_trades)

    # Lưu vào PostgreSQL
    _save_to_db(summary_df, trades_df, style, date_from, date_to)

    logger.info(f"Backtest complete: {len(summaries)} tickers, {len(all_trades)} trades")
    return summary_df


def _save_to_db(
    summary_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    style: str,
    date_from: str | None,
    date_to: str | None,
) -> None:
    """Lưu kết quả backtest vào PostgreSQL."""
    try:
        from scanner.database import db_cursor
        import psycopg2.extras

        # Xóa kết quả cũ cùng style trước khi lưu mới
        with db_cursor() as cur:
            cur.execute("DELETE FROM backtest_summary WHERE style = %s", (style,))
            cur.execute("DELETE FROM backtest_trades WHERE style = %s", (style,))

        # Lưu summary
        if not summary_df.empty:
            sql_sum = """
                INSERT INTO backtest_summary
                    (ticker, style, total_trades, win_trades, win_rate,
                     total_return_pct, max_drawdown_pct, avg_hold_days, updated_at)
                VALUES %s
                ON CONFLICT (ticker) DO UPDATE SET
                    style = EXCLUDED.style,
                    total_trades = EXCLUDED.total_trades,
                    win_trades = EXCLUDED.win_trades,
                    win_rate = EXCLUDED.win_rate,
                    total_return_pct = EXCLUDED.total_return_pct,
                    max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                    avg_hold_days = EXCLUDED.avg_hold_days,
                    updated_at = NOW()
            """
            def _f(v):
                return float(v) if v is not None else None

            rows_sum = [
                (
                    r.get("ticker"), style,
                    int(r.get("total_trades", 0)),
                    int(r.get("win_trades", 0)),
                    _f(r.get("win_rate")),
                    _f(r.get("total_return_pct")),
                    _f(r.get("max_drawdown_pct")),
                    _f(r.get("avg_hold_days")),
                )
                for _, r in summary_df.iterrows()
            ]
            with db_cursor() as cur:
                psycopg2.extras.execute_values(cur, sql_sum, rows_sum)

        # Lưu trades
        if not trades_df.empty:
            sql_tr = """
                INSERT INTO backtest_trades
                    (ticker, style, buy_date, buy_price, sell_date, sell_price,
                     hold_days, pnl_pct, pnl_amount, win)
                VALUES %s
            """
            rows_tr = [
                (
                    r.get("ticker"), style,
                    r.get("buy_date"), _f(r.get("buy_price")),
                    r.get("sell_date"), _f(r.get("sell_price")),
                    int(r.get("hold_days", 0)),
                    _f(r.get("pnl_pct")),
                    _f(r.get("pnl_amount")),
                    bool(r.get("win", False)),
                )
                for _, r in trades_df.iterrows()
            ]
            with db_cursor() as cur:
                psycopg2.extras.execute_values(cur, sql_tr, rows_tr)

        logger.info(f"DB: saved {len(summary_df)} summaries, {len(trades_df)} trades (style={style})")
    except Exception as e:
        logger.warning(f"DB save backtest failed: {e}")


def _buy_price(row: pd.Series, mode: str) -> float:
    """Entry price depending on mode (replicates Pine entryMode)."""
    if mode == "best":
        return float(row["low"])
    if mode == "ideal":
        return float((row["high"] + row["low"]) / 2)
    return float(row["high"])  # realistic: worst case


def _sell_price(row: pd.Series, mode: str) -> float:
    if mode == "best":
        return float(row["high"])
    if mode == "ideal":
        return float((row["high"] + row["low"]) / 2)
    return float(row["low"])  # realistic: worst case


if __name__ == "__main__":
    from scanner.data_fetcher import fetch_ohlcv
    ticker = "VIC"
    df = fetch_ohlcv(ticker, days=500)
    if df is not None:
        result = run_backtest(ticker, df)
        print(f"\n=== Backtest {ticker} ===")
        print(f"Trades: {result['total_trades']}")
        print(f"Win rate: {result['win_rate']}%")
        print(f"Total return: {result['total_return_pct']}%")
        print(f"Max drawdown: {result['max_drawdown_pct']}%")
        print(f"Avg hold: {result['avg_hold_days']} days")
