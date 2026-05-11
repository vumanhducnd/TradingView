"""
Sync OHLCV ngày mới nhất cho tất cả ticker trong DB.
Chạy: python sync_ohlcv.py

Chiến lược 2 bước:
  Bước 1 — price_board batch: 1 API call lấy toàn bộ ticker (~0.5s)
            → upsert ngay các mã có data hôm nay
  Bước 2 — fallback Quote.history(): chỉ cho mã bị miss hoặc cần backfill
            (mã mới, mã bị lỗi bước 1, mã thiếu nhiều ngày)
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from dotenv import load_dotenv
load_dotenv()

from scanner.config import LOOKBACK_DAYS

import pandas as pd

from scanner.database import get_all_last_dates, get_watchlist, upsert_ohlcv
from scanner.utils import logger

HAS_KEY         = bool(os.getenv("VNSTOCK_API_KEY", "").strip())
FALLBACK_WORKERS = 2   if HAS_KEY else 1
FALLBACK_DELAY   = 1.5 if HAS_KEY else 3.5
RATE_LIMIT_WAIT  = 65


# ─── Bước 1: price_board batch ────────────────────────────────────────────────

def sync_via_price_board(tickers: list[str], today: date) -> set[str]:
    """
    Fetch toàn bộ tickers bằng 1 lần gọi price_board.
    Trả về set ticker đã upsert thành công.
    """
    logger.info(f"Buoc 1: price_board batch ({len(tickers)} tickers)...")
    t0 = time.time()

    try:
        from scanner.data_fetcher import _set_api_key
        _set_api_key()
        from vnstock import Trading
        df = Trading(source="VCI").price_board(symbols_list=tickers)
    except Exception as e:
        logger.warning(f"  price_board that bai: {e}")
        return set()

    if df is None or df.empty:
        logger.warning("  price_board tra ve rong")
        return set()

    # Flatten MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            f"{a}.{b}".lower() if b and str(b) != "nan" else str(a).lower()
            for a, b in df.columns
        ]
    else:
        df.columns = [str(c).lower().strip() for c in df.columns]

    df = df.rename(columns={
        "listing.symbol":           "ticker",
        "match.open_price":         "open",
        "match.highest":            "high",
        "match.lowest":             "low",
        "match.match_price":        "close",
        "match.accumulated_volume": "volume",
    })

    required = {"ticker", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        logger.warning(f"  price_board thieu cot: {required - set(df.columns)}")
        return set()

    elapsed_fetch = time.time() - t0
    logger.info(f"  Fetch xong: {elapsed_fetch:.2f}s | {len(df)} rows")

    # Upsert từng ticker vào DB
    ok: set[str] = set()
    skipped = 0
    t_db = time.time()

    for _, row in df.iterrows():
        ticker = str(row["ticker"]).strip().upper()
        try:
            close = float(row["close"] or 0)
            if close <= 0:
                skipped += 1
                continue

            high = float(row["high"] or 0) or close
            low  = float(row["low"]  or 0) or close
            if high < low:
                skipped += 1
                continue

            # price_board trả về VND thực (41500), DB lưu VND/1000 (41.5)
            bar_df = pd.DataFrame([{
                "open":   (float(row["open"] or 0) or close) / 1000,
                "high":   high   / 1000,
                "low":    low    / 1000,
                "close":  close  / 1000,
                "volume": float(row["volume"] or 0),
            }], index=[pd.Timestamp(today)])

            n = upsert_ohlcv(ticker, bar_df)
            if n > 0:
                ok.add(ticker)
        except Exception as e:
            logger.debug(f"  {ticker} upsert loi: {e}")

    elapsed_db = time.time() - t_db
    logger.info(
        f"  Ket qua: {len(ok)} upsert | {skipped} bo qua (close=0) | "
        f"DB {elapsed_db:.1f}s"
    )
    return ok


# ─── Bước 2: fallback per-ticker ──────────────────────────────────────────────

def fetch_history(ticker: str, start: date, end: date, retry: int = 0):
    """Fetch lịch sử OHLCV từng ticker (dùng cho backfill hoặc miss từ bước 1)."""
    try:
        from vnstock.api.quote import Quote
        from scanner.data_fetcher import _normalize_columns
        for source in ("VCI", "TCBS"):
            try:
                df = Quote(symbol=ticker, source=source).history(
                    start=start.strftime("%Y-%m-%d"),
                    end=end.strftime("%Y-%m-%d"),
                    interval="1D",
                )
                if df is not None and not df.empty:
                    return _normalize_columns(df)
            except Exception as e:
                if any(k in str(e).lower() for k in ("rate limit", "429", "too many")):
                    if retry < 2:
                        logger.warning(f"{ticker}: rate limit, cho {RATE_LIMIT_WAIT}s...")
                        time.sleep(RATE_LIMIT_WAIT)
                        return fetch_history(ticker, start, end, retry + 1)
                continue
    except Exception:
        pass
    return None


def sync_fallback(need: list[tuple], label: str = "fallback") -> tuple[int, list]:
    """Loop per-ticker với ThreadPool. need = [(ticker, start, end), ...]"""
    if not need:
        return 0, []

    logger.info(f"Buoc 2 ({label}): {len(need)} tickers, workers={FALLBACK_WORKERS}...")
    est = len(need) / FALLBACK_WORKERS * FALLBACK_DELAY / 60
    logger.info(f"  Uoc tinh: ~{est:.0f} phut")

    ok, failed = 0, []

    def worker(args):
        ticker, start, end = args
        if start > end:
            return ticker, None
        time.sleep(FALLBACK_DELAY)
        return ticker, fetch_history(ticker, start, end)

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=FALLBACK_WORKERS) as pool:
        futures = {pool.submit(worker, a): a[0] for a in need}
        for future in as_completed(futures):
            ticker, df = future.result()
            done += 1
            if df is not None and not df.empty:
                upsert_ohlcv(ticker, df)
                ok += 1
            else:
                failed.append(ticker)

            if done % 50 == 0 or done == len(need):
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 1
                eta  = (len(need) - done) / rate
                logger.info(
                    f"  [{done}/{len(need)}] OK={ok} fail={len(failed)} | "
                    f"{elapsed:.0f}s elapsed, ETA {eta:.0f}s"
                )

    return ok, failed


# ─── Main ─────────────────────────────────────────────────────────────────────

def sync(tickers_override: list[str] | None = None):
    from scanner.data_fetcher import _set_api_key
    _set_api_key()

    t0    = time.time()
    today = date.today()
    logger.info(f"=== Bat dau sync OHLCV ({today}) ===")
    logger.info(f"API key: {'co' if HAS_KEY else 'khong'}")

    tickers    = tickers_override or get_watchlist()
    last_dates = get_all_last_dates()

    if not tickers:
        logger.info("Watchlist trong")
        return

    # Phân loại ticker
    cutoff = today - timedelta(days=LOOKBACK_DAYS)  # 400 ngày — đủ warm-up cho mọi indicator
    need_today:    list[str]            = []  # chỉ cần bar hôm nay
    need_backfill: list[tuple]          = []  # thiếu nhiều ngày → phải dùng history

    for t in tickers:
        last = last_dates.get(t)
        if last is None:
            need_backfill.append((t, cutoff, today))          # chưa có gì
        elif last < today - timedelta(days=1):
            start = last + timedelta(days=1)
            if (today - start).days > 1:
                need_backfill.append((t, start, today))       # thiếu > 1 ngày
            else:
                need_today.append(t)                          # chỉ thiếu hôm qua/hôm nay
        elif last < today:
            need_today.append(t)                              # chỉ thiếu hôm nay

    skip_count = len(tickers) - len(need_today) - len(need_backfill)
    logger.info(
        f"Watchlist: {len(tickers)} | "
        f"Batch hom nay: {len(need_today)} | "
        f"Backfill: {len(need_backfill)} | "
        f"Skip: {skip_count}"
    )

    total_ok = 0
    all_failed: list[str] = []

    # ── Bước 1: price_board cho mã chỉ cần hôm nay ──
    if need_today:
        synced = sync_via_price_board(need_today, today)
        total_ok += len(synced)
        # Mã không lấy được từ price_board → đẩy sang fallback
        missed = [(t, today, today) for t in need_today if t not in synced]
        if missed:
            logger.info(f"  price_board miss {len(missed)} tickers → fallback")
            ok2, failed2 = sync_fallback(missed, label="miss tu price_board")
            total_ok   += ok2
            all_failed += failed2

    # ── Bước 2: per-ticker cho backfill ──
    if need_backfill:
        ok3, failed3 = sync_fallback(need_backfill, label="backfill")
        total_ok   += ok3
        all_failed += failed3

    total = time.time() - t0
    logger.info(f"=== Sync xong: {total_ok} OK, {len(all_failed)} that bai | {total:.1f}s ===")
    if all_failed:
        logger.info(f"That bai ({len(all_failed)}): {all_failed[:20]}")


if __name__ == "__main__":
    import sys
    if "--vn100" in sys.argv:
        from scanner.database import get_vn100_watchlist
        vn100 = get_vn100_watchlist()
        logger.info(f"Mode: VN100 only ({len(vn100)} ma)")
        sync(tickers_override=vn100)
    else:
        sync()
