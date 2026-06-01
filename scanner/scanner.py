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

    # Ghi nhớ thứ tự VN100 để dùng khi sort kết quả
    ticker_order = {t: i for i, t in enumerate(all_data.keys())}

    rows = []
    for ticker, df in all_data.items():
        try:
            info = analyze_ticker(df, style=style)
            info["ticker"] = ticker
            info["vn100_rank"] = ticker_order[ticker]
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

    results = _rank(results)
    return results


def run_scan_dual(
    ticker_data: dict | None = None,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """
    Scan cả ngắn hạn (7/2.0) lẫn dài hạn (10/3.0).
    Trả về 1 DataFrame với cột prefix long_ và short_.
    Thêm cột both_buy / both_sell khi cả 2 cùng đồng thuận.
    """
    long_df  = run_scan(ticker_data=ticker_data, tickers=tickers, style="long")
    short_df = run_scan(ticker_data=ticker_data, tickers=tickers, style="short")

    if long_df.empty or short_df.empty:
        return long_df if not long_df.empty else short_df

    # Cột dùng chung (không prefix)
    shared = ["ticker", "close", "bias_norm", "bias_label", "b_score", "r_score",
              "volume", "turnover",
              "ss1", "ss2", "ss3", "ss4", "ss5", "ss6", "ss7",
              "super_score", "is_super_stock",
              "vsr_sup", "vsr_res", "vsr_near_breakout",
              "vsr_dist_to_res_pct", "vsr_base_width_pct"]
    # Cột riêng từng style
    style_cols = ["trend", "supertrend", "support", "resistance",
                  "buy_signal", "sell_signal",
                  "last_signal_type", "last_signal_date",
                  "last_signal_price", "bars_since_signal", "signal_pnl_pct",
                  "prev_buy_date", "prev_buy_price"]
    # Cột criteria (bull_ema, ...)
    crit_cols = [c for c in long_df.columns if c.startswith("bull_") or c.startswith("bear_")]

    # Prefix các cột style
    long_rename  = {c: f"long_{c}"  for c in style_cols if c in long_df.columns}
    short_rename = {c: f"short_{c}" for c in style_cols if c in short_df.columns}

    long_sub  = long_df[["ticker"] + [c for c in style_cols if c in long_df.columns] + crit_cols].rename(columns=long_rename)
    short_sub = short_df[["ticker"] + [c for c in style_cols if c in short_df.columns]].rename(columns=short_rename)
    shared_sub = long_df[[c for c in shared if c in long_df.columns]]

    merged = shared_sub.merge(long_sub, on="ticker", how="left")
    merged = merged.merge(short_sub, on="ticker", how="left")

    # Đồng thuận cả 2 style
    merged["both_buy"]  = merged.get("long_buy_signal",  False) & merged.get("short_buy_signal",  False)
    merged["both_sell"] = merged.get("long_sell_signal", False) & merged.get("short_sell_signal", False)

    # Rank: both > long > short > nothing
    merged["_rank"] = 3
    merged.loc[merged["long_buy_signal"]  | merged["long_sell_signal"],  "_rank"] = 2
    merged.loc[merged["short_buy_signal"] | merged["short_sell_signal"], "_rank"] = 1
    merged.loc[merged["both_buy"]         | merged["both_sell"],         "_rank"] = 0

    sort_cols = ["_rank", "bias_norm"]
    sort_asc  = [True, False]
    if "vn100_rank" in merged.columns:
        sort_cols.append("vn100_rank")
        sort_asc.append(True)
    merged = merged.sort_values(sort_cols, ascending=sort_asc).drop(columns="_rank")
    return merged.reset_index(drop=True)


def get_super_buy_stocks(results: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    Loc 'sieu co phieu vung mua' — co phieu tot nhat dang o vung gia vao lenh hap dan.

    Tieu chi CUNG (phai thoa het):
      - long_trend == 1 (xu huong dai han tang)
      - bias_norm >= 60
      - b_score >= 5/9
      - Khong co tin hieu BAN (ca ngan han lan dai han)

    Diem THUONG (cang cao cang tot):
      - both_buy: +4  (ca 2 khung deu MUA)
      - long_buy_signal: +3
      - short_buy_signal: +2
      - bias_norm >= 75: +3  / >= 65: +1
      - b_score >= 7: +2     / >= 6: +1
      - short_trend == 1: +2 (ca 2 khung trend tang)
      - bull_vol: +2          (volume xac nhan)
      - bull_macd + bull_rsi + bull_adx: +1 moi cai
      - Vung mua (0-7% tren long_support): +3
      - Tiem nang len (>5% den long_resistance): +2
    """
    df = results.copy()

    # ── Hard filters ──────────────────────────────────────────────────────────
    mask = (
        (df.get("long_trend", 0) == 1) &
        (df["bias_norm"] >= 60) &
        (df["b_score"] >= 5) &
        (~df.get("long_sell_signal",  pd.Series(False, index=df.index)).astype(bool)) &
        (~df.get("short_sell_signal", pd.Series(False, index=df.index)).astype(bool))
    )
    df = df[mask].copy()
    if df.empty:
        return df

    # ── Scoring ───────────────────────────────────────────────────────────────
    score = pd.Series(0.0, index=df.index)

    # Tin hieu
    if "both_buy"         in df.columns: score += df["both_buy"].astype(bool)        * 4
    if "long_buy_signal"  in df.columns: score += df["long_buy_signal"].astype(bool) * 3
    if "short_buy_signal" in df.columns: score += df["short_buy_signal"].astype(bool)* 2

    # BiasNorm
    score += (df["bias_norm"] >= 75).astype(int) * 3
    score += ((df["bias_norm"] >= 65) & (df["bias_norm"] < 75)).astype(int) * 1

    # bScore
    score += (df["b_score"] >= 7).astype(int) * 2
    score += ((df["b_score"] == 6)).astype(int) * 1

    # Trend ngan han
    if "short_trend" in df.columns:
        score += (df["short_trend"] == 1).astype(int) * 2

    # Indicator key
    for col in ("bull_vol", "bull_macd", "bull_rsi", "bull_adx"):
        if col in df.columns:
            score += df[col].astype(bool).astype(int) * (2 if col == "bull_vol" else 1)

    # Vung mua: gia 0-7% tren long_support
    close   = pd.to_numeric(df["close"],         errors="coerce").fillna(0)
    sup     = pd.to_numeric(df.get("long_support",    0), errors="coerce").fillna(0)
    res     = pd.to_numeric(df.get("long_resistance",  0), errors="coerce").fillna(0)

    dist_sup = ((close - sup) / sup.replace(0, 1) * 100)
    dist_res = ((res - close)  / close.replace(0, 1) * 100)

    df["dist_support_pct"]    = dist_sup.round(1)
    df["dist_resistance_pct"] = dist_res.round(1)

    score += ((dist_sup >= 0) & (dist_sup <= 7)).astype(int)  * 3   # vung mua ly tuong
    score += ((dist_sup >  7) & (dist_sup <= 15)).astype(int) * 1   # con chap nhan
    score += (dist_res  >= 5).astype(int)                     * 2   # co tiem nang

    df["super_score"] = score.round(1)

    return df.nlargest(top_n, "super_score").reset_index(drop=True)


def get_current_signals(results: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Extract buy and sell signal rows. Hỗ trợ cả single và dual style."""
    # Dual style
    if "both_buy" in results.columns:
        buy_mask  = results.get("long_buy_signal",  False) | results.get("short_buy_signal",  False)
        sell_mask = results.get("long_sell_signal", False) | results.get("short_sell_signal", False)
        return {
            "buy":      results[buy_mask].copy(),
            "sell":     results[sell_mask].copy(),
            "both_buy":  results[results["both_buy"]].copy(),
            "both_sell": results[results["both_sell"]].copy(),
        }
    # Single style
    return {
        "buy":  results[results["buy_signal"]].copy(),
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
    """Sort: tín hiệu trước (bias_norm desc), cùng nhóm thì VN100 rank ưu tiên."""
    df = df.copy()
    df["_rank_group"] = 2  # default: no signal
    df.loc[df["buy_signal"], "_rank_group"] = 0
    df.loc[df["sell_signal"], "_rank_group"] = 1

    sort_cols = ["_rank_group", "bias_norm"]
    sort_asc  = [True, False]
    if "vn100_rank" in df.columns:
        sort_cols.append("vn100_rank")
        sort_asc.append(True)

    df = df.sort_values(by=sort_cols, ascending=sort_asc).drop(columns=["_rank_group"])
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
