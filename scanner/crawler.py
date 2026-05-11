"""
Crawler lịch sử — chạy 1 lần đầu để load toàn bộ data vào DB.
Rate-limit aware: tự động chờ khi bị throttled.
Hỗ trợ resume: bỏ qua ticker đã có đủ data.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import scanner.utils  # noqa: F401 — patch SSL trước khi vnstock load

from scanner.config import FETCH_DELAY, LOOKBACK_DAYS, VNSTOCK_API_KEY
from scanner.database import (
    get_all_last_dates,
    get_watchlist,
    upsert_ohlcv,
    upsert_watchlist,
)
from scanner.utils import logger

_RATE_LIMIT_WAIT = 60   # giây chờ khi bị rate limit
_MAX_RETRY = 3


def crawl_all(
    tickers: list[str] | None = None,
    days: int = LOOKBACK_DAYS,
    force: bool = False,
) -> None:
    """
    Crawl lịch sử OHLCV cho toàn bộ watchlist.

    Args:
        tickers: danh sách mã, None = lấy từ DB watchlist
        days:    số ngày lịch sử cần lấy
        force:   True = fetch lại dù đã có data
    """
    if tickers is None:
        tickers = get_watchlist()
        if not tickers:
            logger.error("Watchlist trống. Chạy --init-watchlist trước.")
            sys.exit(1)

    # Lấy ngày mới nhất trong DB cho từng ticker (để resume)
    last_dates = get_all_last_dates() if not force else {}
    cutoff = date.today() - timedelta(days=days)

    # Lọc ticker cần crawl
    need_crawl = []
    skip_count = 0
    for ticker in tickers:
        last = last_dates.get(ticker)
        if last and last >= cutoff and not force:
            skip_count += 1
        else:
            need_crawl.append(ticker)

    logger.info(f"Crawl plan: {len(need_crawl)} fetch, {skip_count} skip (đã đủ data)")
    if not need_crawl:
        logger.info("Tất cả ticker đã có data. Dùng --force để crawl lại.")
        return

    est_min = len(need_crawl) * FETCH_DELAY / 60
    logger.info(f"Ước tính: ~{est_min:.1f} phút (delay={FETCH_DELAY}s/ticker)")

    ok, failed = 0, []

    for i, ticker in enumerate(need_crawl, 1):
        last = last_dates.get(ticker)
        # Nếu có data cũ → chỉ fetch phần thiếu
        start_date = (last + timedelta(days=1)) if (last and not force) else cutoff
        end_date = date.today()

        if start_date > end_date:
            skip_count += 1
            continue

        logger.info(f"[{i}/{len(need_crawl)}] {ticker}: {start_date} → {end_date}")

        df = _fetch_with_retry(ticker, start_date, end_date)
        if df is not None and not df.empty:
            inserted = upsert_ohlcv(ticker, df)
            logger.info(f"  {ticker}: +{inserted} nến")
            ok += 1
        else:
            logger.warning(f"  {ticker}: không có data")
            failed.append(ticker)

        if i < len(need_crawl):
            time.sleep(FETCH_DELAY)

    logger.info(f"\n=== Crawl xong: {ok} OK, {len(failed)} thất bại ===")
    if failed:
        logger.warning(f"Failed: {failed}")


def init_watchlist_from_api() -> list[str]:
    """Lấy danh sách VN100 từ API và lưu vào DB."""
    from scanner.data_fetcher import _fetch_from_api, _load_watchlist_csv
    tickers = _fetch_from_api()
    if not tickers:
        logger.warning("API thất bại, dùng watchlist.csv")
        tickers = _load_watchlist_csv()
    upsert_watchlist(tickers)
    logger.info(f"Watchlist init: {len(tickers)} tickers")
    return tickers


def _fetch_with_retry(ticker: str, start: date, end: date, retry: int = 0):
    """Fetch OHLCV với retry khi bị rate limit."""
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
        return None
    except Exception as e:
        err = str(e).lower()
        if "rate limit" in err or "429" in err or "too many" in err:
            if retry < _MAX_RETRY:
                logger.warning(f"  Rate limit — chờ {_RATE_LIMIT_WAIT}s rồi thử lại...")
                time.sleep(_RATE_LIMIT_WAIT)
                return _fetch_with_retry(ticker, start, end, retry + 1)
        elif "empty" in err or "column 1" in err:
            # Thử TCBS source
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
            except Exception:
                pass
        logger.debug(f"  Lỗi: {e}")
        return None


# ─── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    force = "--force" in sys.argv
    init_wl = "--init-watchlist" in sys.argv

    if init_wl:
        tickers = init_watchlist_from_api()
    else:
        tickers = None

    crawl_all(tickers=tickers, force=force)
