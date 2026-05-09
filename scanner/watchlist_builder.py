"""
Xây dựng watchlist top N mã theo thanh khoản + vốn hóa.

Quy trình:
  1. Lấy tất cả mã HOSE + HNX từ vnstock
  2. Crawl nhanh 20 ngày gần nhất cho từng mã
  3. Tính avg_turnover = avg(close × volume) trong 20 ngày
  4. Lọc: loại penny stock (close < 5,000), lấy top N theo turnover
  5. Lưu vào DB watchlist
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

import pandas as pd

import scanner.utils  # noqa: F401 — phải import trước để patch SSL
from scanner.config import FETCH_DELAY
from scanner.database import get_connection, upsert_ohlcv, upsert_watchlist
from scanner.utils import logger

# Ngưỡng lọc mặc định
MIN_PRICE = 5_000          # bỏ penny stock dưới 5,000 VND
MIN_AVG_TURNOVER = 1e9     # thanh khoản tối thiểu 1 tỷ VND/ngày
QUICK_CRAWL_DAYS = 30      # crawl nhanh 30 ngày để tính thanh khoản
TOP_N_DEFAULT = 500


def build_watchlist(
    top_n: int = 0,
    filter_liquidity: bool = False,
    force: bool = False,
) -> list[str]:
    """
    Xây dựng và lưu watchlist.
    top_n=0 + filter_liquidity=False → lấy toàn bộ HOSE+HNX+UPCOM.
    Returns danh sách ticker đã lưu.
    """
    # Bước 1: Lấy tất cả mã từ HOSE + HNX + UPCOM
    all_tickers = _get_all_tickers()
    if not all_tickers:
        logger.error("Không lấy được danh sách mã từ API")
        sys.exit(1)

    logger.info(f"Tổng mã HOSE+HNX+UPCOM: {len(all_tickers)}")

    if not filter_liquidity:
        # Lưu thẳng không lọc
        upsert_watchlist(all_tickers)
        logger.info(f"Watchlist: {len(all_tickers)} mã (không lọc)")
        return all_tickers

    # Nếu muốn lọc theo thanh khoản: crawl nhanh trước
    _quick_crawl(all_tickers, days=QUICK_CRAWL_DAYS, force=force)
    limit = top_n if top_n > 0 else len(all_tickers)
    ranked = _rank_by_liquidity(all_tickers, top_n=limit)
    if not ranked:
        logger.error("Không có mã nào đủ điều kiện thanh khoản")
        return []

    upsert_watchlist(ranked)
    logger.info(f"Watchlist cập nhật: {len(ranked)} mã (đã lọc thanh khoản)")
    return ranked


def _get_all_tickers() -> list[str]:
    """Lấy toàn bộ mã VN từ KBS source (1500+ mã HOSE+HNX+UPCOM)."""
    # KBS source: dùng all_symbols() → trả về toàn bộ ~1500 mã
    try:
        from vnstock.api.listing import Listing
        listing = Listing(source="KBS")
        df = listing.all_symbols()
        if df is not None and not df.empty:
            col = _find_ticker_col(df)
            tickers = df[col].str.upper().str.strip().tolist()
            logger.info(f"KBS all_symbols: {len(tickers)} mã")
            return tickers
    except Exception as e:
        logger.debug(f"KBS all_symbols failed: {e}")

    # Fallback: symbols_by_exchange từng sàn
    tickers = []
    for source in ["VCI", "KBS"]:
        if tickers:
            break
        for exchange in ["HOSE", "HNX", "UPCOM"]:
            try:
                from vnstock.api.listing import Listing
                listing = Listing(source=source)
                df = listing.symbols_by_exchange(exchange=exchange)
                if df is not None and not df.empty:
                    col = _find_ticker_col(df)
                    batch = df[col].str.upper().str.strip().tolist()
                    logger.info(f"  {exchange} ({source}): {len(batch)} mã")
                    tickers.extend(batch)
            except Exception as e:
                logger.debug(f"{exchange} {source} failed: {e}")

    seen: set[str] = set()
    return [t for t in tickers if not (t in seen or seen.add(t))]


def _quick_crawl(tickers: list[str], days: int = QUICK_CRAWL_DAYS, force: bool = False) -> None:
    """
    Crawl nhanh N ngày gần nhất cho toàn bộ tickers.
    Bỏ qua mã đã có data gần đây (resume friendly).
    """
    from scanner.database import get_all_last_dates
    last_dates = get_all_last_dates() if not force else {}
    cutoff = date.today() - timedelta(days=days)
    today = date.today()

    need = [t for t in tickers if not (last_dates.get(t) and last_dates[t] >= cutoff)]
    skip = len(tickers) - len(need)
    logger.info(f"Quick crawl: {len(need)} fetch, {skip} skip | est ~{len(need)*FETCH_DELAY/60:.1f} phút")

    ok, fail = 0, []
    for i, ticker in enumerate(need, 1):
        last = last_dates.get(ticker)
        start = (last + timedelta(days=1)) if last else cutoff

        df = _fetch_ohlcv(ticker, start, today)
        if df is not None and not df.empty:
            upsert_ohlcv(ticker, df)
            ok += 1
            if i % 20 == 0:
                logger.info(f"  [{i}/{len(need)}] {ok} OK, {len(fail)} fail")
        else:
            fail.append(ticker)

        if i < len(need):
            time.sleep(FETCH_DELAY)

    logger.info(f"Quick crawl xong: {ok} OK, {len(fail)} thất bại")


def _rank_by_liquidity(tickers: list[str], top_n: int) -> list[str]:
    """
    Truy vấn DB, tính avg_turnover 20 ngày, lọc và lấy top N.
    """
    cutoff = date.today() - timedelta(days=30)
    sql = """
        SELECT
            ticker,
            AVG(close)              AS avg_close,
            AVG(volume)             AS avg_volume,
            AVG(close * volume)     AS avg_turnover,
            COUNT(*)                AS bar_count
        FROM ohlcv
        WHERE ticker = ANY(%s)
          AND date >= %s
        GROUP BY ticker
        HAVING
            COUNT(*) >= 10
            AND AVG(close) >= %s
            AND AVG(close * volume) >= %s
        ORDER BY avg_turnover DESC
        LIMIT %s
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (tickers, cutoff, MIN_PRICE, MIN_AVG_TURNOVER, top_n))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    df = pd.DataFrame(rows, columns=["ticker", "avg_close", "avg_volume", "avg_turnover", "bar_count"])
    logger.info(f"\nTop 10 thanh khoản cao nhất:")
    for _, r in df.head(10).iterrows():
        turnover_b = r["avg_turnover"] / 1e9
        logger.info(f"  {r['ticker']}: {turnover_b:.1f} tỷ/ngày | giá {r['avg_close']:,.0f}")

    logger.info(f"\nLoại bỏ: {len(tickers) - len(df)} mã (thanh khoản thấp / thiếu data)")
    return df["ticker"].tolist()


def _fetch_ohlcv(ticker: str, start: date, end: date):
    """Fetch với fallback VCI → TCBS."""
    for source in ["VCI", "TCBS"]:
        try:
            from vnstock.api.quote import Quote
            q = Quote(symbol=ticker, source=source)
            df = q.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1D",
            )
            if df is not None and not df.empty:
                from scanner.data_fetcher import _normalize_columns
                return _normalize_columns(df)
        except Exception:
            continue
    return None


def _find_ticker_col(df: pd.DataFrame) -> str:
    for name in ["ticker", "symbol", "code", "Ticker", "Symbol", "Code"]:
        if name in df.columns:
            return name
    return df.columns[0]


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build VN stock watchlist")
    parser.add_argument("--top", type=int, default=0,
                        help="Giới hạn số mã (default: 0 = tất cả)")
    parser.add_argument("--filter-liquidity", action="store_true",
                        help="Lọc theo thanh khoản (crawl nhanh 30 ngày trước)")
    parser.add_argument("--min-price", type=float, default=MIN_PRICE,
                        help="Giá tối thiểu VND (chỉ dùng khi --filter-liquidity)")
    parser.add_argument("--min-turnover", type=float, default=MIN_AVG_TURNOVER,
                        help="Thanh khoản tối thiểu VND/ngày")
    parser.add_argument("--force", action="store_true", help="Crawl lại dù đã có data")
    args = parser.parse_args()

    MIN_PRICE = args.min_price
    MIN_AVG_TURNOVER = args.min_turnover

    result = build_watchlist(
        top_n=args.top,
        filter_liquidity=args.filter_liquidity,
        force=args.force,
    )
    print(f"\nWatchlist: {len(result)} mã")
    print(result[:20], "...")
