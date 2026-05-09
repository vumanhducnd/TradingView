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
) -> dict:
    """
    Replay buy/sell signals on historical data.
    Returns summary dict + list of trades.
    """
    df = calc_supertrend(df, style=style)
    df = calc_bias_norm(df)
    df = df.dropna(subset=["buy_signal", "sell_signal"])

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

    return {
        "ticker": ticker,
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
) -> pd.DataFrame:
    """
    Run backtest for all tickers. Saves results to CSV. Returns summary DataFrame.
    """
    all_trades = []
    summaries = []

    for ticker, df in ticker_data.items():
        try:
            result = run_backtest(ticker, df, style, initial_capital, compound, entry_mode)
            summaries.append({k: v for k, v in result.items() if k != "trades"})
            all_trades.extend(result["trades"])
        except Exception as e:
            logger.warning(f"Backtest failed for {ticker}: {e}")

    summary_df = pd.DataFrame(summaries)
    trades_df = pd.DataFrame(all_trades)

    # Save
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = BACKTEST_DIR / "backtest_results.csv"
    trades_path = BACKTEST_DIR / "backtest_trades.csv"

    summary_df.to_csv(summary_path, index=False)
    if not trades_df.empty:
        trades_df.to_csv(trades_path, index=False)

    logger.info(f"Backtest complete: {len(summaries)} tickers, {len(all_trades)} trades")
    logger.info(f"Saved: {summary_path}, {trades_path}")
    return summary_df


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
