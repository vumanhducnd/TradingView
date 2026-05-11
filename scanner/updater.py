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
            n = upsert_ohlcv(ticker, df)
            ok += 1
            logger.info(f"  {ticker}: +{n} nến")
        else:
            failed.append(ticker)
            logger.warning(f"  {ticker}: không có data")

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
    from scanner.database import get_vn100_watchlist
    from scanner.scanner import get_current_signals, run_scan

    vn100 = get_vn100_watchlist()
    ticker_data = load_all_from_db(tickers=vn100 if vn100 else None)
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


def run_scan_only() -> None:
    """Scan dual (ngắn hạn + dài hạn) từ DB — giống test_one.py."""
    from scanner.data_fetcher import load_all_from_db
    from scanner.scanner import get_current_signals, run_scan_dual
    from scanner.database import load_scan_results, load_scan_dates
    from scanner.telegram_bot import send_daily_report

    logger.info("=== Bat dau scan-only (dual mode) ===")

    force = "--force" in sys.argv

    # Nếu không phải ngày giao dịch và không force → load kết quả cũ từ DB, gửi lại
    if not force and not is_trading_day():
        dates = load_scan_dates()
        if dates:
            logger.info(f"Nghi giao dich — load ket qua {dates[0]} tu DB")
            results = load_scan_results(dates[0])
            if not results.empty:
                signals = get_current_signals(results)
                send_daily_report(results, signals)
                buy_n  = len(signals.get("buy",  []))
                sell_n = len(signals.get("sell", []))
                logger.info(f"=== Xong (cache): {buy_n} MUA, {sell_n} BAN ===")
                return

    logger.info("Buoc 1/4: Load OHLCV tu DB (chi VN100)...")
    from scanner.database import get_vn100_watchlist
    vn100 = get_vn100_watchlist()
    ticker_data = load_all_from_db(tickers=vn100 if vn100 else None)
    if not ticker_data:
        logger.error("Khong load duoc data tu DB")
        return

    logger.info(f"Buoc 2/4: Tinh SuperTrend ngan han + dai han cho {len(ticker_data)} tickers...")
    results = run_scan_dual(ticker_data=ticker_data)
    if results.empty:
        logger.error("Scan khong co ket qua")
        return

    from scanner.scanner import get_super_buy_stocks
    super_stocks = get_super_buy_stocks(results, top_n=10)
    logger.info(f"  Super co phieu vung mua: {len(super_stocks)} ma")

    signals = get_current_signals(results)
    buy_n   = len(signals.get("buy",  []))
    sell_n  = len(signals.get("sell", []))
    both_n  = len(signals.get("both_buy", [])) + len(signals.get("both_sell", []))
    logger.info(f"  {len(results)} ma | MUA={buy_n} | BAN={sell_n} | DONG THUAN={both_n}")

    logger.info("Buoc 3/4: Luu ket qua vao DB...")
    save_scan_results(results)
    save_signals(results)

    logger.info("Buoc 4/4: Gui Telegram + Excel...")
    ai_analysis = {}
    try:
        from scanner.ai_analyst import run_full_analysis
        ai_analysis = run_full_analysis(results)
    except Exception as e:
        logger.warning(f"AI analysis failed: {e}")

    try:
        send_daily_report(results, signals, ai_analysis=ai_analysis, super_stocks=super_stocks)
    except Exception as e:
        logger.warning(f"Telegram failed: {e}")

    # Cập nhật thanh khoản trung bình 20 phiên cho toàn bộ watchlist
    try:
        from scanner.database import update_liquidity_stats
        n_updated = update_liquidity_stats(days=20)
        logger.info(f"Liquidity stats updated: {n_updated} tickers")
    except Exception as e:
        logger.warning(f"update_liquidity_stats failed: {e}")

    logger.info(f"=== Scan-only xong: {buy_n} MUA, {sell_n} BAN, {both_n} DONG THUAN ===")


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
    if "--scan-only" in sys.argv:
        run_scan_only()
    else:
        force = "--force" in sys.argv
        run_full_pipeline(force=force)
