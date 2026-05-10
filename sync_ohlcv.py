"""
Sync OHLCV ngày mới nhất cho tất cả ticker trong DB.
Chạy: python sync_ohlcv.py
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from scanner.database import get_all_last_dates, get_watchlist, upsert_ohlcv
from scanner.utils import logger

MAX_WORKERS = 3
FETCH_DELAY = 1.2    # giây chờ giữa mỗi request trong 1 worker
LOG_EVERY   = 50     # log tiến độ mỗi N ticker


def fetch_latest(ticker: str, start: date, end: date):
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
            except Exception:
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
    logger.info(f"  Can fetch : {len(need)} ticker")
    logger.info(f"  Da up-to-date: {skip_count} ticker")

    if not need:
        logger.info("Tat ca da up to date. Ket thuc.")
        logger.info(f"=== Xong trong {time.time()-t0:.1f}s ===")
        return

    est = len(need) / MAX_WORKERS * FETCH_DELAY
    logger.info(f"Workers: {MAX_WORKERS} | Delay: {FETCH_DELAY}s | Uoc tinh: ~{est/60:.1f} phut")

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
                rate    = done / elapsed if elapsed > 0 else 0
                eta     = (len(need) - done) / rate if rate > 0 else 0
                logger.info(
                    f"  [{done}/{len(need)}] OK={ok} | "
                    f"elapsed={elapsed:.0f}s | ETA={eta:.0f}s"
                )

    total = time.time() - t0
    logger.info(f"=== Sync xong: {ok} OK, {len(failed)} that bai | Tong thoi gian: {total:.1f}s ===")
    if failed:
        logger.info(f"That bai ({len(failed)}): {failed[:20]}")


if __name__ == "__main__":
    sync()
