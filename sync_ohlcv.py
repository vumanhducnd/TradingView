"""
Sync OHLCV ngày mới nhất cho tất cả ticker trong DB.
Chạy: python sync_ohlcv.py
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from scanner.database import get_all_last_dates, get_watchlist, upsert_ohlcv
from scanner.utils import logger

MAX_WORKERS = 3      # tăng lên để fetch song song
FETCH_DELAY = 1.2    # giây chờ giữa mỗi request trong 1 worker


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
    tickers = get_watchlist()
    if not tickers:
        logger.info("Watchlist trong")
        return

    last_dates = get_all_last_dates()
    today = date.today()
    cutoff = today - timedelta(days=5)

    # Chỉ fetch ticker thực sự thiếu data gần đây
    need = [
        (t, (last_dates[t] + timedelta(days=1)) if t in last_dates else cutoff, today)
        for t in tickers
        if t not in last_dates or last_dates[t] < today - timedelta(days=1)
    ]

    logger.info(f"Can sync: {len(need)}/{len(tickers)} tickers")
    if not need:
        logger.info("Da up to date")
        return

    ok, failed = 0, []

    def worker(args):
        ticker, start, end = args
        if start > end:
            return ticker, None
        time.sleep(FETCH_DELAY)
        df = fetch_latest(ticker, start, end)
        return ticker, df

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(worker, args): args[0] for args in need}
        done = 0
        for future in as_completed(futures):
            ticker, df = future.result()
            done += 1
            if df is not None and not df.empty:
                upsert_ohlcv(ticker, df)
                ok += 1
                if ok % 50 == 0:
                    logger.info(f"  [{done}/{len(need)}] OK={ok}")
            else:
                failed.append(ticker)

    logger.info(f"Sync xong: {ok} OK, {len(failed)} that bai")
    if failed:
        logger.info(f"Failed ({len(failed)}): {failed[:20]}...")


if __name__ == "__main__":
    sync()
