"""
Daily updater — chạy sau khi thị trường đóng cửa (3:30 PM ICT).
Chỉ fetch bar mới nhất cho từng ticker → insert vào DB.
Sau đó chạy scanner đọc từ DB → không cần API nữa.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

from scanner.config import FETCH_DELAY, LOOKBACK_DAYS
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
    Dùng price_board bulk (1 API call) thay vì per-ticker.
    Fallback sang per-ticker nếu price_board thất bại.
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
    need_update = [t for t in tickers if force or not last_dates.get(t) or last_dates[t] < today]

    if not need_update:
        logger.info(f"Tất cả {len(tickers)} ticker đã có data hôm nay. Bỏ qua.")
        return True

    logger.info(f"Daily update: {len(need_update)}/{len(tickers)} ticker cần cập nhật ngày {today}")

    # ── Thử bulk trước (1 API call) ──────────────────────────────────────────
    from scanner.live_scanner import _fetch_via_price_board
    from scanner.database import bulk_upsert_today

    bars = _fetch_via_price_board(need_update)
    if bars:
        n = bulk_upsert_today(bars, today)
        logger.info(f"Bulk update OK: {n} rows, {len(bars)}/{len(need_update)} ticker")
        failed = [t for t in need_update if t not in bars]
        if failed:
            logger.warning(f"price_board thiếu {len(failed)} ticker: {failed[:10]}...")
        return True

    # ── Fallback per-ticker ───────────────────────────────────────────────────
    logger.warning("price_board thất bại, fallback per-ticker...")
    ok, failed = 0, []

    for i, ticker in enumerate(need_update, 1):
        last = last_dates.get(ticker)
        start = (last + timedelta(days=1)) if last else (today - timedelta(days=5))
        df = _fetch_latest(ticker, start, today)

        if df is not None and not df.empty:
            n = upsert_ohlcv(ticker, df)
            ok += 1
            logger.info(f"  {ticker}: +{n} nến")
        else:
            failed.append(ticker)
            logger.warning(f"  {ticker}: không có data")

        if i < len(need_update):
            time.sleep(FETCH_DELAY)

    logger.info(f"Per-ticker xong: {ok} cập nhật, {len(failed)} thất bại")
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
        from scanner.company_data import build_company_data
        from scanner.excel_report import build_excel_report
        company_data = build_company_data(results, signals)
        build_excel_report(results, signals, company_data=company_data)
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

    # ── Bước 0: Fetch giá cuối ngày cho tất cả mã (price_board) ──────────────
    logger.info("Buoc 0/4: Fetch gia cuoi ngay tat ca ma qua price_board...")
    try:
        from scanner.database import get_watchlist, bulk_upsert_today
        from scanner.live_scanner import _fetch_via_price_board
        all_tickers = get_watchlist()
        today_bars = _fetch_via_price_board(all_tickers)
        if today_bars:
            n_upsert = bulk_upsert_today(today_bars, date.today())
            logger.info(f"  Upsert gia cuoi ngay: {n_upsert}/{len(all_tickers)} ma")
        else:
            logger.warning("  price_board khong lay duoc gia — dung data DB hien co")
    except Exception as e:
        logger.warning(f"  Fetch gia cuoi ngay that bai: {e}")

    logger.info("Buoc 1/4: Load OHLCV bulk tu DB (top 300)...")
    from scanner.database import get_top300_thanh_khoan, load_all_ohlcv_bulk
    top300 = get_top300_thanh_khoan(n=300)
    ticker_data = load_all_ohlcv_bulk(tickers=top300, days=LOOKBACK_DAYS)
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

    # Lọc + ghi nhận intraday reversal để đề cập trong báo cáo
    # - fake_breakout: MUA trong phiên nhưng cuối ngày không giữ được (BÁN cuối ngày)
    # - fake_breakdown: BÁN trong phiên nhưng cuối ngày giá hồi (MUA cuối ngày)
    intraday_reversals: dict[str, dict[str, list[str]]] = {
        "long":  {"fake_breakout": [], "fake_breakdown": []},
        "short": {"fake_breakout": [], "fake_breakdown": []},
    }
    try:
        from scanner.database import db_cursor
        with db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT ticker, style, signal_type FROM signals WHERE signal_date = CURRENT_DATE"
            )
            rows = cur.fetchall()
        bought_today = {(r["ticker"], r["style"]) for r in rows if r["signal_type"] == "MUA"}
        sold_today   = {(r["ticker"], r["style"]) for r in rows if r["signal_type"] == "BÁN"}

        # Fake breakout: MUA trong phiên → cuối ngày trend vẫn âm (không giữ được)
        # Bắt cả 2 trường hợp:
        #   1. Cuối ngày có sell_signal mới (flip)
        #   2. Cuối ngày trend âm (quay về xu hướng giảm cũ, không tạo flip mới)
        if bought_today:
            # Case 1: có sell_signal cuối ngày → lọc khỏi signals
            for sig_key in ["sell", "both_sell"]:
                df = signals.get(sig_key)
                if df is not None and not df.empty:
                    is_fake = df["ticker"].apply(
                        lambda t: (t, "long") in bought_today or (t, "short") in bought_today
                    )
                    for _, row in df[is_fake].iterrows():
                        t = row["ticker"]
                        if (t, "long")  in bought_today: intraday_reversals["long"]["fake_breakout"].append(t)
                        if (t, "short") in bought_today: intraday_reversals["short"]["fake_breakout"].append(t)
                    if is_fake.sum():
                        signals[sig_key] = df[~is_fake]
                        logger.info(f"  Loc {is_fake.sum()} fake breakout khoi '{sig_key}'")

            # Case 2: trend âm cuối ngày (không có sell_signal mới, chỉ ghi nhận để báo)
            already_noted = (
                set(intraday_reversals["long"]["fake_breakout"]) |
                set(intraday_reversals["short"]["fake_breakout"])
            )
            for style in ["long", "short"]:
                trend_col = f"{style}_trend"
                if trend_col not in results.columns:
                    continue
                neg_trend = set(results[results[trend_col] == -1]["ticker"])
                for (t, s) in bought_today:
                    if s == style and t in neg_trend and t not in already_noted:
                        intraday_reversals[style]["fake_breakout"].append(t)
                        logger.info(f"  Fake breakout (trend am): {t} [{style}]")

        # Fake breakdown: BÁN trong phiên → cuối ngày trend dương (rút chân giả)
        # Case 1: cuối ngày có buy_signal mới → lọc khỏi signals
        if sold_today:
            for sig_key in ["buy", "both_buy"]:
                df = signals.get(sig_key)
                if df is not None and not df.empty:
                    is_fake = df["ticker"].apply(
                        lambda t: (t, "long") in sold_today or (t, "short") in sold_today
                    )
                    for _, row in df[is_fake].iterrows():
                        t = row["ticker"]
                        if (t, "long")  in sold_today: intraday_reversals["long"]["fake_breakdown"].append(t)
                        if (t, "short") in sold_today: intraday_reversals["short"]["fake_breakdown"].append(t)
                    if is_fake.sum():
                        signals[sig_key] = df[~is_fake]
                        logger.info(f"  Loc {is_fake.sum()} fake breakdown khoi '{sig_key}'")

            # Case 2: trend dương cuối ngày (quay về tăng cũ, không tạo buy_signal mới)
            already_noted = (
                set(intraday_reversals["long"]["fake_breakdown"]) |
                set(intraday_reversals["short"]["fake_breakdown"])
            )
            for style in ["long", "short"]:
                trend_col = f"{style}_trend"
                if trend_col not in results.columns:
                    continue
                pos_trend = set(results[results[trend_col] == 1]["ticker"])
                for (t, s) in sold_today:
                    if s == style and t in pos_trend and t not in already_noted:
                        intraday_reversals[style]["fake_breakdown"].append(t)
                        logger.info(f"  Fake breakdown (trend duong): {t} [{style}]")

    except Exception as e:
        logger.debug(f"Filter intraday reversal: {e}")

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
        send_daily_report(results, signals, ai_analysis=ai_analysis, super_stocks=super_stocks,
                          intraday_reversals=intraday_reversals)
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
