"""
Sync OHLCV ngày hôm nay cho tất cả ticker bằng price_board (bulk, 1-2 API call).
Fallback sang Quote.history() nếu price_board thất bại.
Chạy: python sync_ohlcv.py
"""

import time
from datetime import date, timedelta

from scanner.database import get_all_last_dates, get_watchlist, upsert_ohlcv
from scanner.utils import logger

CHUNK_SIZE = 500  # Số ticker mỗi lần gọi price_board


def sync_via_price_board(tickers: list[str], today: date) -> dict[str, int]:
    """
    Fetch giá hôm nay bằng price_board (bulk) — 1 call / CHUNK_SIZE ticker.
    Returns dict {ticker: rows_inserted}.
    """
    import pandas as pd
    import scanner.utils  # patch SSL before vnstock loads
    from vnstock import Trading

    result = {}
    total_calls = 0

    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[i:i + CHUNK_SIZE]
        t_chunk = time.time()
        try:
            t = Trading(source="VCI")
            df = t.price_board(symbols_list=chunk)
            total_calls += 1
        except Exception as e:
            logger.warning(f"price_board chunk {i//CHUNK_SIZE+1} loi: {e}")
            continue

        if df is None or df.empty:
            continue

        # Normalize columns — price_board trả về tên cột khác nhau tùy source
        col_map = {}
        for col in df.columns:
            cl = col.lower().strip()
            for target, variants in {
                "ticker":  ["ticker", "symbol", "code", "ma_ck"],
                "open":    ["open", "mo_cua", "gia_mo"],
                "high":    ["high", "cao_nhat", "gia_cao"],
                "low":     ["low",  "thap_nhat", "gia_thap"],
                "close":   ["close", "gia_dong_cua", "dong_cua", "price", "last_price", "ref", "close_price"],
                "volume":  ["volume", "klgd", "khoi_luong", "total_volume"],
            }.items():
                if cl in variants:
                    col_map[col] = target
                    break
        df = df.rename(columns=col_map)

        required = {"ticker", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            logger.warning(f"price_board thieu cot: {missing}. Co: {list(df.columns)}")
            continue

        df["date"] = today
        df = df[["ticker", "date", "open", "high", "low", "close", "volume"]].copy()
        df = df[df["close"] > 0]  # bỏ ticker không có giá

        for _, row in df.iterrows():
            tk = str(row["ticker"]).upper()
            ohlcv = pd.DataFrame([{
                "date":   today,
                "open":   float(row["open"]  or row["close"]),
                "high":   float(row["high"]  or row["close"]),
                "low":    float(row["low"]   or row["close"]),
                "close":  float(row["close"]),
                "volume": int(row["volume"] or 0),
            }]).set_index("date")
            upsert_ohlcv(tk, ohlcv)
            result[tk] = 1

        elapsed = time.time() - t_chunk
        logger.info(
            f"  price_board chunk {i//CHUNK_SIZE+1}: "
            f"{len(result)} tickers OK | {elapsed:.1f}s"
        )

    logger.info(f"price_board: {total_calls} API calls, {len(result)} tickers updated")
    return result


def sync_via_history(tickers: list[str], last_dates: dict, today: date) -> dict[str, int]:
    """Fallback: fetch từng ticker bằng Quote.history()."""
    import os, time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scanner.data_fetcher import _normalize_columns

    HAS_KEY    = bool(os.getenv("VNSTOCK_API_KEY", "").strip())
    WORKERS    = 2 if HAS_KEY else 1
    DELAY      = 1.5 if HAS_KEY else 3.5
    LOG_EVERY  = 50

    cutoff = today - timedelta(days=5)
    need = [
        (t, (last_dates[t] + timedelta(days=1)) if t in last_dates else cutoff, today)
        for t in tickers
    ]

    logger.info(f"history fallback: {len(need)} tickers | workers={WORKERS} delay={DELAY}s")

    result = {}

    def worker(args):
        tk, start, end = args
        if start > end:
            return tk, None
        _time.sleep(DELAY)
        try:
            from vnstock.api.quote import Quote
            for src in ("VCI", "TCBS"):
                try:
                    q = Quote(symbol=tk, source=src)
                    df = q.history(
                        start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        interval="1D",
                    )
                    if df is not None and not df.empty:
                        return tk, _normalize_columns(df)
                except Exception:
                    continue
        except Exception:
            pass
        return tk, None

    done, failed = 0, []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(worker, a): a[0] for a in need}
        for future in as_completed(futures):
            tk, df = future.result()
            done += 1
            if df is not None and not df.empty:
                upsert_ohlcv(tk, df)
                result[tk] = len(df)
            else:
                failed.append(tk)
            if done % LOG_EVERY == 0 or done == len(need):
                elapsed = time.time() - t0
                eta = (len(need) - done) / (done / elapsed) if done else 0
                logger.info(f"  [{done}/{len(need)}] OK={len(result)} | {elapsed:.0f}s ETA={eta:.0f}s")

    return result


def sync():
    t0 = time.time()
    today = date.today()
    logger.info(f"=== Bat dau sync OHLCV ({today}) ===")

    tickers = get_watchlist()
    if not tickers:
        logger.info("Watchlist trong")
        return

    last_dates   = get_all_last_dates()
    need_tickers = [t for t in tickers if t not in last_dates or last_dates[t] < today - timedelta(days=1)]
    skip_count   = len(tickers) - len(need_tickers)

    logger.info(f"Watchlist: {len(tickers)} | Can sync: {len(need_tickers)} | Skip: {skip_count}")

    if not need_tickers:
        logger.info(f"Tat ca da up to date. ({time.time()-t0:.1f}s)")
        return

    # Thu price_board truoc (nhanh, bulk)
    logger.info(f"Thu price_board bulk ({len(need_tickers)} tickers, ~{len(need_tickers)//CHUNK_SIZE+1} API calls)...")
    updated = sync_via_price_board(need_tickers, today)

    still_need = [t for t in need_tickers if t not in updated]
    if still_need:
        logger.info(f"price_board miss {len(still_need)} tickers, fallback sang history...")
        updated2 = sync_via_history(still_need, last_dates, today)
        updated.update(updated2)

    total = time.time() - t0
    logger.info(f"=== Sync xong: {len(updated)}/{len(need_tickers)} tickers | {total:.1f}s ===")


if __name__ == "__main__":
    sync()
