"""
Main orchestrator: fetch all tickers, run indicators, rank signals, save snapshot.
"""

import sys
import pandas as pd
from datetime import date

from scanner.config import SIGNALS_DIR, ST_DEFAULT_STYLE
from scanner.data_fetcher import fetch_all_tickers, get_vn100_tickers
from scanner.indicators import analyze_ticker
from scanner.utils import bias_label, is_trading_day, logger, today_str


def run_scan(
    tickers: list[str] | None = None,
    style: str = ST_DEFAULT_STYLE,
    ticker_data: dict | None = None,
) -> pd.DataFrame:
    """
    Run full scan across all tickers.
    ticker_data: dict {ticker: df} đọc từ DB (ưu tiên dùng nếu có)
    Returns a ranked DataFrame with one row per ticker.
    """
    if ticker_data is None:
        if tickers is None:
            tickers = get_vn100_tickers()
        all_data = fetch_all_tickers(tickers)
    else:
        all_data = ticker_data

    rows = []
    for ticker, df in all_data.items():
        try:
            info = analyze_ticker(df, style=style)
            info["ticker"] = ticker
            # Flatten bull/bear criteria thành cột riêng
            for k, v in info.pop("bull_criteria", {}).items():
                info[f"bull_{k}"] = v
            for k, v in info.pop("bear_criteria", {}).items():
                info[f"bear_{k}"] = v
            rows.append(info)
        except Exception as e:
            logger.warning(f"{ticker}: analysis failed — {e}")

    if not rows:
        logger.error("No tickers processed successfully")
        return pd.DataFrame()

    results = pd.DataFrame(rows)
    results["bias_label"] = results["bias_norm"].apply(bias_label)

    # Gắn lịch sử MUA/BÁN và P&L cho tất cả ticker
    try:
        from scanner.database import load_last_signals
        tickers = results["ticker"].tolist()
        sig_df = load_last_signals(tickers)

        def _get(ticker, stype, field):
            if sig_df.empty:
                return None
            row = sig_df[(sig_df["ticker"] == ticker) & (sig_df["signal_type"] == stype)]
            return row.iloc[0][field] if not row.empty else None

        results["buy_date"]   = results["ticker"].map(lambda t: _get(t, "MUA", "signal_date"))
        results["buy_price"]  = results["ticker"].map(lambda t: _get(t, "MUA", "price"))
        results["sell_date"]  = results["ticker"].map(lambda t: _get(t, "BÁN", "signal_date"))
        results["sell_price"] = results["ticker"].map(lambda t: _get(t, "BÁN", "price"))

        def _pnl(row):
            bp = row.get("buy_price")
            if not bp or bp <= 0:
                return None
            # Nếu đã bán → dùng sell_price, chưa bán → dùng close
            ep = row.get("sell_price") or row.get("close")
            return round((ep - bp) / bp * 100, 2) if ep else None

        results["pnl_pct"] = results.apply(_pnl, axis=1)

        def _max_loss(row):
            bp = row.get("buy_price")
            st = row.get("support")
            if not bp or not st or bp <= 0:
                return None
            return round((bp - st) / bp * 100, 2)

        results["max_loss_pct"] = results.apply(_max_loss, axis=1)

        # Số phiên đang giữ lệnh
        def _hold_days(row):
            bd = row.get("buy_date")
            if not bd:
                return None
            from datetime import date as _date
            end = row.get("sell_date") or _date.today()
            try:
                start = bd if isinstance(bd, _date) else pd.to_datetime(bd).date()
                end   = end if isinstance(end, _date) else pd.to_datetime(end).date()
                return (end - start).days
            except Exception:
                return None

        results["hold_days"] = results.apply(_hold_days, axis=1)

    except Exception as e:
        logger.debug(f"load_last_signals failed: {e}")

    results = _rank(results)
    return results


def get_current_signals(results: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Extract buy and sell signal rows."""
    return {
        "buy": results[results["buy_signal"]].copy(),
        "sell": results[results["sell_signal"]].copy(),
    }


def save_daily_snapshot(results: pd.DataFrame, scan_date: str | None = None) -> str:
    """Save results to data/signals/signals_YYYY-MM-DD.csv. Returns file path."""
    if scan_date is None:
        scan_date = today_str()
    path = SIGNALS_DIR / f"signals_{scan_date}.csv"

    export_cols = [
        "ticker", "close", "trend", "supertrend", "bias_norm", "bias_label",
        "buy_signal", "sell_signal", "b_score", "r_score", "atr", "volume",
    ]
    # Add individual criteria columns if present
    criteria = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]
    for c in criteria:
        if f"bull_{c}" in results.columns:
            export_cols.append(f"bull_{c}")

    cols_present = [c for c in export_cols if c in results.columns]
    results[cols_present].to_csv(path, index=False)
    logger.info(f"Snapshot saved: {path}")
    return str(path)


def _rank(df: pd.DataFrame) -> pd.DataFrame:
    """Sort: buy signals first (bias_norm desc), sell signals next, rest last."""
    df = df.copy()
    df["_rank_group"] = 2  # default: no signal
    df.loc[df["buy_signal"], "_rank_group"] = 0
    df.loc[df["sell_signal"], "_rank_group"] = 1

    df = df.sort_values(
        by=["_rank_group", "bias_norm"],
        ascending=[True, False],
    ).drop(columns=["_rank_group"])
    return df.reset_index(drop=True)


# --- Entry point for GitHub Actions ---

def main() -> None:
    force = "--force" in sys.argv
    if not force and not is_trading_day():
        logger.info(f"Today ({date.today()}) is not a trading day. Skipping scan. (dùng --force để bỏ qua)")
        sys.exit(0)

    logger.info("=== ManhDucCapital Scanner START ===")
    results = run_scan()

    if results.empty:
        logger.error("Scan returned no results")
        sys.exit(1)

    signals = get_current_signals(results)
    snapshot_path = save_daily_snapshot(results)

    # Excel report
    try:
        from scanner.excel_report import build_excel_report
        build_excel_report(results, signals)
    except Exception as e:
        logger.warning(f"Excel report failed: {e}")

    # Telegram notification
    try:
        from scanner.telegram_bot import send_daily_report
        send_daily_report(results, signals)
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")

    buy_count = len(signals["buy"])
    sell_count = len(signals["sell"])
    logger.info(f"=== DONE: {buy_count} MUA, {sell_count} BÁN, {len(results)} tổng ===")


if __name__ == "__main__":
    main()
