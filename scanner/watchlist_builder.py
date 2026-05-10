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
        # Lưu kèm exchange info
        upsert_watchlist(all_tickers)  # all_tickers là list[(ticker, exchange)]
        logger.info(f"Watchlist: {len(all_tickers)} mã (không lọc)")
        return [t for t, _ in all_tickers]

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


def _get_all_tickers() -> list[tuple[str, str]]:
    """
    Lấy toàn bộ mã VN. HOSE/HNX/UPCOM gán đúng sàn, còn lại UNKNOWN.
    Returns list of (ticker, exchange).
    """
    import re
    stock_pattern = re.compile(r'^[A-Z]{2,4}$')

    # Bước 1: Exchange map từ 3 sàn chính
    exchange_map: dict[str, str] = {}
    for exchange in ["HOSE", "HNX", "UPCOM"]:
        batch = _fetch_exchange_tickers_with_info(exchange)
        for t in batch:
            exchange_map[t] = exchange
        logger.info(f"  {exchange}: {len(batch)} mã")

    # Bước 2: Full list từ all_symbols
    all_tickers: list[str] = []
    try:
        from vnstock.api.listing import Listing
        df = Listing(source="KBS").all_symbols()
        if df is not None and not df.empty:
            col = _find_ticker_col(df)
            raw = df[col].str.upper().str.strip().tolist()
            all_tickers = [t for t in raw if stock_pattern.match(t)]
    except Exception as e:
        logger.debug(f"all_symbols failed: {e}")

    # Fallback nếu all_symbols trống
    if not all_tickers:
        all_tickers = list(exchange_map.keys())

    # Bước 3: Gán exchange, UNKNOWN nếu không map được
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    unknown = 0
    for t in all_tickers:
        if t in seen:
            continue
        seen.add(t)
        exch = exchange_map.get(t, "UNKNOWN")
        if exch == "UNKNOWN":
            unknown += 1
        result.append((t, exch))

    logger.info(f"Tổng: {len(result)} mã | {len(result)-unknown} có sàn | {unknown} UNKNOWN")
    return result


def _fetch_exchange_tickers_with_info(exchange: str) -> list[str]:
    """
    Lấy danh sách ticker cổ phiếu từ 1 sàn dùng symbols_by_group.
    Lọc bỏ trái phiếu/chứng quyền (chỉ giữ mã 2-4 chữ cái).
    """
    import re
    stock_pattern = re.compile(r'^[A-Z]{2,4}$')

    try:
        from vnstock.api.listing import Listing
        import pandas as pd
        listing = Listing(source="KBS")
        result = listing.symbols_by_group(exchange)  # HOSE / HNX / UPCOM

        # Xử lý cả Series lẫn DataFrame
        if isinstance(result, pd.Series):
            tickers = result.str.upper().str.strip().tolist()
        elif isinstance(result, pd.DataFrame) and not result.empty:
            col = _find_ticker_col(result)
            tickers = result[col].str.upper().str.strip().tolist()
        else:
            tickers = []

        valid = [t for t in tickers if stock_pattern.match(t)]
        if valid:
            logger.debug(f"  {exchange} KBS: {len(tickers)} raw → {len(valid)} valid")
            return valid
    except Exception as e:
        logger.debug(f"{exchange} KBS symbols_by_group failed: {e}")

    return []


def _quick_crawl(tickers: list[str], days: int = QUICK_CRAWL_DAYS, force: bool = False) -> None:
    """
    Crawl nhanh N ngày gần nhất cho toàn bộ tickers.
    Bỏ qua mã đã có data gần đây (resume friendly).
    """
    from scanner.database import get_all_last_dates
    last_dates = get_all_last_dates() if not force else {}
    cutoff = date.today() - timedelta(days=days)
    today = date.today()

    # Hỗ trợ cả list[str] và list[(ticker, exchange)]
    ticker_list = [t if isinstance(t, str) else t[0] for t in tickers]
    need = [t for t in ticker_list if not (last_dates.get(t) and last_dates[t] >= cutoff)]
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
