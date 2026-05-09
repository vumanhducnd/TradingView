"""
Daily updater — chạy sau khi thị trường đóng cửa (3:30 PM ICT).
Chỉ fetch bar mới nhất cho từng ticker → insert vào DB.
Sau đó chạy scanner đọc từ DB → không cần API nữa.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

from scanner.config import FETCH_DELAY
from scanner.database import (
    get_all_last_dates,
    get_watchlist,
    save_scan_results,
    save_signals,
    upsert_ohlcv,
)
from scanner.utils import is_trading_day, logger


def update_daily(force: bool = False) -> bool:
    """
    Fetch dữ liệu mới nhất cho tất cả ticker trong watchlist.
    Returns True nếu có ít nhất 1 ticker cập nhật thành công.
    """
    today = date.today()

    if not force and not is_trading_day(today):
        logger.info(f"Hôm nay ({today}) không phải ngày giao dịch. Bỏ qua.")
        return False

    tickers = get_watchlist()
    if not tickers:
        logger.error("Watchlist trống. Chạy crawler.py --init-watchlist trước.")
        return False

    last_dates = get_all_last_dates()
    ok, skipped, failed = 0, 0, []

    logger.info(f"Daily update: {len(tickers)} tickers, ngày {today}")

    for i, ticker in enumerate(tickers, 1):
        last = last_dates.get(ticker)

        # Đã có data hôm nay → bỏ qua
        if last and last >= today and not force:
            skipped += 1
            continue

        # Chỉ lấy từ ngày tiếp theo sau last (hoặc 5 ngày gần nhất nếu không có)
        start = (last + timedelta(days=1)) if last else (today - timedelta(days=5))
        df = _fetch_latest(ticker, start, today)

        if df is not None and not df.empty:
            upsert_ohlcv(ticker, df)
            ok += 1
            logger.info(f"[{i}/{len(tickers)}] {ticker}: +{len(df)} bar(s)")
        else:
            failed.append(ticker)
            logger.debug(f"[{i}/{len(tickers)}] {ticker}: no new data")

        if i < len(tickers):
            time.sleep(FETCH_DELAY)

    logger.info(f"Update xong: {ok} cập nhật, {skipped} bỏ qua, {len(failed)} thất bại")
    if failed:
        logger.warning(f"Failed: {failed}")

    return ok > 0


def run_full_pipeline(force: bool = False) -> None:
    """
    Pipeline hoàn chỉnh sau khi thị trường đóng:
    1. Update data mới vào DB
    2. Chạy scanner (đọc từ DB)
    3. Lưu kết quả vào DB
    4. Gửi Telegram
    5. Xuất Excel
    """
    # Step 1: Update data
    updated = update_daily(force=force)
    if not updated and not force:
        logger.info("Không có data mới. Kết thúc.")
        return

    # Step 2 & 3: Scan + save to DB
    from scanner.data_fetcher import load_all_from_db
    from scanner.scanner import get_current_signals, run_scan

    ticker_data = load_all_from_db()
    if not ticker_data:
        logger.error("Không load được data từ DB")
        return

    results = run_scan(ticker_data=ticker_data)
    if results.empty:
        logger.error("Scan không có kết quả")
        return

    signals = get_current_signals(results)

    # Lưu vào DB
    save_scan_results(results)
    save_signals(results)

    # Lưu CSV snapshot (backup)
    from scanner.scanner import save_daily_snapshot
    save_daily_snapshot(results)

    # Step 4: Telegram
    try:
        from scanner.telegram_bot import send_daily_report
        send_daily_report(results, signals)
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")

    # Step 5: Excel
    try:
        from scanner.excel_report import build_excel_report
        build_excel_report(results, signals)
    except Exception as e:
        logger.warning(f"Excel failed: {e}")

    buy_n = len(signals.get("buy", []))
    sell_n = len(signals.get("sell", []))
    logger.info(f"=== Pipeline xong: {buy_n} MUA, {sell_n} BÁN ===")


def _fetch_latest(ticker: str, start: date, end: date):
    """Fetch OHLCV từ vnstock API (chỉ vài bar gần nhất)."""
    try:
        from vnstock.api.quote import Quote
        q = Quote(symbol=ticker, source="VCI")
        df = q.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1D",
        )
        if df is not None and not df.empty:
            from scanner.data_fetcher import _normalize_columns
            return _normalize_columns(df)
    except Exception as e:
        logger.debug(f"{ticker} VCI: {e}")
        # Fallback TCBS
        try:
            from vnstock.api.quote import Quote
            q = Quote(symbol=ticker, source="TCBS")
            df = q.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1D",
            )
            if df is not None and not df.empty:
                from scanner.data_fetcher import _normalize_columns
                return _normalize_columns(df)
        except Exception as e2:
            logger.debug(f"{ticker} TCBS: {e2}")
    return None


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_full_pipeline(force=force)
