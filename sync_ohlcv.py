"""
Sync OHLCV ngày mới nhất cho tất cả ticker trong DB.
Chạy: python sync_ohlcv.py
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from scanner.database import get_all_last_dates, get_watchlist, upsert_ohlcv
from scanner.utils import logger

LOG_EVERY      = 50
RATE_LIMIT_WAIT = 65  # giây chờ khi bị rate limit

# Điều chỉnh theo API key: guest=20/phút, community=60/phút
_HAS_KEY    = bool(os.getenv("VNSTOCK_API_KEY", "").strip())
MAX_WORKERS = 2   if _HAS_KEY else 1
FETCH_DELAY = 1.5 if _HAS_KEY else 3.5


def fetch_latest(ticker: str, start: date, end: date, retry: int = 0):
    try:
        from vnstock.api.quote import Quote
        from scanner.data_fetcher import _normalize_columns
        for source in ("VCI", "TCBS"):
            try:
                q = Quote(symbol=ticker, source=source)
                df = q.history(
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval="1D",
                )
                if df is not None and not df.empty:
                    return _normalize_columns(df)
            except Exception as e:
                err = str(e).lower()
                if "rate limit" in err or "429" in err or "too many" in err:
                    if retry < 2:
                        logger.warning(f"{ticker}: rate limit, cho {RATE_LIMIT_WAIT}s...")
                        time.sleep(RATE_LIMIT_WAIT)
                        return fetch_latest(ticker, start, end, retry + 1)
                continue
    except Exception:
        pass
    return None


def sync():
    t0 = time.time()
    logger.info("=== Bat dau sync OHLCV ===")

    tickers = get_watchlist()
    if not tickers:
        logger.info("Watchlist trong, dung lai")
        return

    last_dates = get_all_last_dates()
    today      = date.today()
    cutoff     = today - timedelta(days=5)

    need = [
        (t, (last_dates[t] + timedelta(days=1)) if t in last_dates else cutoff, today)
        for t in tickers
        if t not in last_dates or last_dates[t] < today - timedelta(days=1)
    ]
    skip_count = len(tickers) - len(need)

    logger.info(f"Watchlist: {len(tickers)} tickers")
    logger.info(f"  Can fetch    : {len(need)}")
    logger.info(f"  Da up-to-date: {skip_count}")
    logger.info(f"  API key      : {'co' if _HAS_KEY else 'khong'}")
    logger.info(f"  Workers      : {MAX_WORKERS} | Delay: {FETCH_DELAY}s/worker")

    if not need:
        logger.info(f"Tat ca da up to date. Tong: {time.time()-t0:.1f}s")
        return

    est = len(need) / MAX_WORKERS * FETCH_DELAY / 60
    logger.info(f"  Uoc tinh     : ~{est:.1f} phut")

    ok, failed = 0, []

    def worker(args):
        ticker, start, end = args
        if start > end:
            return ticker, None
        time.sleep(FETCH_DELAY)
        df = fetch_latest(ticker, start, end)
        return ticker, df

    t_fetch = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(worker, args): args[0] for args in need}
        for future in as_completed(futures):
            ticker, df = future.result()
            done += 1
            if df is not None and not df.empty:
                upsert_ohlcv(ticker, df)
                ok += 1
            else:
                failed.append(ticker)

            if done % LOG_EVERY == 0 or done == len(need):
                elapsed = time.time() - t_fetch
                rate    = done / elapsed if elapsed > 0 else 1
                eta     = (len(need) - done) / rate
                logger.info(
                    f"  [{done}/{len(need)}] OK={ok} fail={len(failed)} | "
                    f"elapsed={elapsed:.0f}s ETA={eta:.0f}s"
                )

    total = time.time() - t0
    logger.info(f"=== Sync xong: {ok} OK, {len(failed)} that bai | {total:.1f}s ===")
    if failed:
        logger.info(f"That bai ({len(failed)}): {failed[:20]}")


if __name__ == "__main__":
    sync()
