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
from datetime import date, timedelta

import pandas as pd

import scanner.utils  # noqa: F401 — phải import trước để patch SSL
from scanner.database import get_connection, upsert_watchlist, load_industry_map
from scanner.utils import logger

# Ngưỡng lọc — DB lưu giá đơn vị VND/1000 (vd: 48,500 VND → 48.5)
MIN_PRICE        = 5.0     # bỏ penny stock dưới 5,000 VND (= 5.0 trong DB)
MIN_AVG_TURNOVER = 1e6     # thanh khoản tối thiểu 1 tỷ VND/ngày (volume*close trong DB = VND/1000 → 1 tỷ = 1e6)


def build_watchlist(
    top_n: int = 0,
    filter_liquidity: bool = True,
) -> list[str]:
    """
    Xây dựng và lưu watchlist.
    Lấy danh sách mã từ API, tính thanh khoản từ DB (không crawl).
    top_n=0 → lấy tất cả mã đủ điều kiện.
    """
    # Bước 1: Lấy tất cả mã từ HOSE + HNX + UPCOM
    all_tickers = _get_all_tickers()
    if not all_tickers:
        logger.error("Không lấy được danh sách mã từ API")
        sys.exit(1)
    logger.info(f"Tổng mã HOSE+HNX+UPCOM: {len(all_tickers)}")

    # Bước 2: Fetch industry map từ vnstock (best-effort)
    ticker_list_all = [t for t, _ in all_tickers]
    industry_map = _fetch_industry_map(ticker_list_all)
    logger.info(f"Industry data: {len(industry_map)}/{len(ticker_list_all)} mã")

    if not filter_liquidity:
        upsert_watchlist(all_tickers, industry_map=industry_map)
        logger.info(f"Watchlist: {len(all_tickers)} mã (không lọc)")
        return [t for t, _ in all_tickers]

    # Bước 3: Tính thanh khoản từ DB (không crawl)
    ticker_list = [t for t, _ in all_tickers]
    limit = top_n if top_n > 0 else len(ticker_list)
    ranked = _rank_by_liquidity(ticker_list, top_n=limit)
    if not ranked:
        logger.error("Không có mã nào đủ điều kiện — DB chưa có OHLCV?")
        return []

    # Giữ lại exchange info cho các mã được chọn
    exchange_map = {t: ex for t, ex in all_tickers}
    ranked_with_exchange = [(t, exchange_map.get(t, "UNKNOWN")) for t in ranked]
    upsert_watchlist(ranked_with_exchange, industry_map=industry_map)
    logger.info(f"Watchlist cập nhật: {len(ranked)} mã (lọc từ DB, không crawl)")
    return ranked


def _get_all_tickers() -> list[tuple[str, str]]:
    """
    Lấy toàn bộ mã từ HOSE/HNX/UPCOM, gán đúng sàn.
    Returns list of (ticker, exchange).
    """
    # Exchange map từ 3 sàn chính
    exchange_map: dict[str, str] = {}
    for exchange in ["HOSE", "HNX", "UPCOM"]:
        batch = _fetch_exchange_tickers_with_info(exchange)
        for t in batch:
            exchange_map[t] = exchange
        logger.info(f"  {exchange}: {len(batch)} mã")

    # Chỉ dùng mã đã xác định được sàn — bỏ all_symbols() để tránh UNKNOWN
    result = list(exchange_map.items())
    logger.info(f"Tổng: {len(result)} mã có sàn (HOSE+HNX+UPCOM)")
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


def _fetch_industry_map(tickers: list[str]) -> dict[str, str]:
    """
    Fetch ngành ICB cho tất cả tickers từ vnstock Listing.
    Gọi 1 lần khi build watchlist hàng tuần.
    Returns {ticker: industry_name}.
    """
    try:
        from vnstock.api.listing import Listing
        import pandas as pd
        listing = Listing(source="KBS")
        df = listing.symbols_by_industries()
        if df is None or df.empty:
            raise ValueError("empty response")

        # vnstock trả về DataFrame với cột 'symbol'/'ticker' và 'icb_name'/'industry'
        col_ticker   = _find_ticker_col(df)
        col_industry = next(
            (c for c in df.columns if c.lower() in ("icb_name", "industry", "industryname", "industry_name", "sector")),
            None,
        )
        if col_industry is None:
            logger.warning(f"Không tìm thấy cột industry trong: {df.columns.tolist()}")
            return {}

        df[col_ticker] = df[col_ticker].str.upper().str.strip()
        result = dict(zip(df[col_ticker], df[col_industry].str.strip()))
        result = {t: v for t, v in result.items() if t in set(tickers) and v}
        return result
    except Exception as e:
        logger.warning(f"_fetch_industry_map failed: {e} — industry sẽ không được cập nhật")
        return {}


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
        turnover_b = float(r["avg_turnover"]) / 1e6   # VND/1000 * volume → chia 1e6 ra tỷ
        logger.info(f"  {r['ticker']}: {turnover_b:.1f} tỷ/ngày | giá {float(r['avg_close'])*1000:,.0f} VND")

    logger.info(f"\nLoại bỏ: {len(tickers) - len(df)} mã (thanh khoản thấp / thiếu data)")
    return df["ticker"].tolist()


def _find_ticker_col(df: pd.DataFrame) -> str:
    for name in ["ticker", "symbol", "code", "Ticker", "Symbol", "Code"]:
        if name in df.columns:
            return name
    return df.columns[0]


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import argparse
    parser = argparse.ArgumentParser(description="Build VN stock watchlist từ DB")
    parser.add_argument("--top", type=int, default=0,
                        help="Giới hạn số mã (default: 0 = tất cả đủ điều kiện)")
    parser.add_argument("--min-price", type=float, default=MIN_PRICE,
                        help="Giá tối thiểu VND")
    parser.add_argument("--min-turnover", type=float, default=MIN_AVG_TURNOVER,
                        help="Thanh khoản tối thiểu VND/ngày")
    parser.add_argument("--no-filter", action="store_true",
                        help="Lưu toàn bộ mã, không lọc thanh khoản")
    args = parser.parse_args()

    MIN_PRICE = args.min_price
    MIN_AVG_TURNOVER = args.min_turnover

    result = build_watchlist(
        top_n=args.top,
        filter_liquidity=not args.no_filter,
    )
    print(f"\nWatchlist: {len(result)} mã")
    print(result[:20], "...")

    # Dọn OHLCV cũ sau khi update watchlist
    from scanner.config import LOOKBACK_DAYS
    from scanner.database import db_cursor
    with db_cursor() as cur:
        cur.execute("DELETE FROM ohlcv WHERE date < CURRENT_DATE - INTERVAL '%s days'", (LOOKBACK_DAYS,))
        deleted = cur.rowcount
    print(f"Cleanup OHLCV: xóa {deleted} rows cũ hơn {LOOKBACK_DAYS} ngày")
