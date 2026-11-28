import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from scanner.config import FETCH_DELAY, LOOKBACK_DAYS, MAX_WORKERS, VNSTOCK_API_KEY, WATCHLIST_FILE
from scanner.utils import logger


def _set_api_key() -> None:
    """vnstock thế hệ 6 tự đọc VNSTOCK_API_KEY từ môi trường; ở đây chỉ áp dụng nguồn dữ liệu."""
    from scanner.vnstock_sources import ensure_sources
    ensure_sources()


def get_vn100_tickers() -> list[str]:
    """Return top 100 VN-Index tickers. Tries vnstock API, falls back to CSV."""
    tickers = _fetch_from_api()
    if tickers:
        _save_watchlist(tickers)
        return tickers

    logger.warning("vnstock API failed, loading watchlist from CSV")
    return _load_watchlist_csv()


def refresh_watchlist() -> list[str]:
    """Force-refresh watchlist from API. Fallback sang CSV rồi DB nếu API down."""
    tickers = _fetch_from_api()
    if tickers:
        _save_watchlist(tickers)
        logger.info(f"Watchlist refreshed từ API: {len(tickers)} tickers")
        return tickers

    logger.warning("vnstock API không khả dụng — dùng watchlist hiện có")

    # Fallback 1: CSV local
    try:
        tickers = _load_watchlist_csv()
        if tickers:
            _save_watchlist(tickers)  # cập nhật rank trong DB
            logger.info(f"Watchlist giữ nguyên từ CSV: {len(tickers)} tickers")
            return tickers
    except FileNotFoundError:
        pass

    # Fallback 2: DB watchlist
    try:
        from scanner.database import get_watchlist
        tickers = get_watchlist()
        if tickers:
            logger.info(f"Watchlist giữ nguyên từ DB: {len(tickers)} tickers")
            return tickers
    except Exception:
        pass

    raise RuntimeError("Không có watchlist nào khả dụng (API down, không có CSV, DB trống)")


def fetch_ohlcv(ticker: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame | None:
    """Fetch OHLCV data for one ticker via vnstock thế hệ 6 (Market.equity.ohlcv)."""
    end = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    # vnstock thế hệ 6 — nguồn khai báo tập trung trong scanner/config.py
    try:
        from scanner.vnstock_sources import fetch_daily_ohlcv
        df = fetch_daily_ohlcv(ticker, start, end)
        if df is not None and not df.empty:
            return _normalize_columns(df)
    except Exception as e:
        logger.debug(f"{ticker}: ohlcv failed — {e}")

    return None


def fetch_all_tickers(
    tickers: list[str], days: int = LOOKBACK_DAYS
) -> dict[str, pd.DataFrame]:
    """Parallel fetch for all tickers. Returns dict of {ticker: df}."""
    results: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    logger.info(f"Fetching {len(tickers)} tickers ({MAX_WORKERS} workers)...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_ohlcv, t, days): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                df = future.result()
                if df is not None and len(df) >= 50:
                    results[ticker] = df
                else:
                    failed.append(ticker)
            except Exception as e:
                logger.debug(f"{ticker}: unexpected error — {e}")
                failed.append(ticker)

    logger.info(f"Fetched {len(results)} OK, {len(failed)} failed")
    if failed:
        logger.warning(f"Failed tickers: {failed}")
    return results


def _fetch_from_api() -> list[str]:
    """Try to get VN100 component list from vnstock new API."""
    # vnstock thế hệ 6 — nguồn khai báo tập trung trong scanner/config.py (route reference.*)
    try:
        from scanner.vnstock_sources import ensure_sources
        ensure_sources()
        from vnstock import Reference
    except Exception as e:
        logger.debug(f"vnstock init failed: {e}")
        return []

    # Method A: thành phần rổ VN100
    try:
        df = Reference().index("VN100").members()
        if df is not None and not df.empty:
            col = _find_ticker_col(df)
            tickers = df[col].str.upper().tolist()
            if len(tickers) >= 50:
                logger.info(f"VN100 members: {len(tickers)} tickers")
                return tickers
    except Exception as e:
        logger.debug(f"VN100 members failed: {e}")

    # Đã bỏ fallback "100 mã HOSE đầu tiên theo thứ tự danh mục" (khi chuyển vnstock thế hệ 6):
    # đó không phải rổ VN100 và ghi đè vn100_rank trong DB. Trả [] để dùng watchlist.csv / DB.
    return []


def _load_watchlist_csv() -> list[str]:
    if not WATCHLIST_FILE.exists():
        raise FileNotFoundError(
            f"Watchlist file not found: {WATCHLIST_FILE}. "
            "Run with --refresh-watchlist or add data/watchlist.csv manually."
        )
    df = pd.read_csv(WATCHLIST_FILE)
    col = _find_ticker_col(df)
    return df[col].str.upper().tolist()


def _save_watchlist(tickers: list[str]) -> None:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": tickers}).to_csv(WATCHLIST_FILE, index=False)
    # Đồng bộ rank vào DB — chạy migration nếu cột chưa tồn tại
    try:
        from scanner.database import db_cursor, upsert_watchlist
        with db_cursor() as cur:
            cur.execute("ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS vn100_rank INT")
        upsert_watchlist(tickers)
    except Exception as e:
        logger.debug(f"upsert_watchlist bỏ qua: {e}")


def _find_ticker_col(df: pd.DataFrame) -> str:
    for name in ["ticker", "symbol", "code", "Ticker", "Symbol", "Code"]:
        if name in df.columns:
            return name
    return df.columns[0]


def _snap_to_tick(price: float) -> float:
    """
    Round giá về bội số 0.05 (50 VND, đơn vị nghìn VND).
    Số thứ 2 sau thập phân luôn là 0 hoặc 5 — khớp với định dạng TradingView.
    Ví dụ: 10.16 → 10.15 | 10.51 → 10.50 | 10.53 → 10.55
    """
    if not price or price != price or price <= 0:  # falsy / NaN / non-positive
        return price
    return round(round(price / 0.05) * 0.05, 2)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize column names to lowercase: open, high, low, close, volume."""
    rename = {}
    for col in df.columns:
        lower = col.lower()
        if lower in ("open", "high", "low", "close", "volume", "time", "date"):
            rename[col] = lower
    df = df.rename(columns=rename)

    if "time" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"time": "date"})

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        # vnstock thế hệ 6 trả thời gian có múi giờ (nến ngày lúc 07:00+07); DB dùng ngày VN
        # không múi giờ, 00:00 như bản cũ
        if df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None).dt.normalize()
        df = df.set_index("date").sort_index()
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df = df[required].dropna()

    return df


def load_all_from_db(
    days: int = LOOKBACK_DAYS,
    tickers: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Đọc OHLCV từ PostgreSQL dùng 1 bulk query thay vì N queries.
    tickers: danh sách cụ thể cần load (None = lấy toàn bộ từ DB).
    """
    from scanner.database import load_all_ohlcv_bulk, load_ohlcv, get_watchlist

    # Bulk load 1 query — dù có tickers cụ thể hay không
    if tickers is None:
        tickers_arg = None
        label = "toan bo watchlist"
    else:
        tickers_arg = tickers
        label = f"{len(tickers)} ticker"

    logger.info(f"Loading OHLCV bulk tu DB ({label})...")
    result = load_all_ohlcv_bulk(tickers=tickers_arg, days=days)
    if result:
        logger.info(f"Loaded {len(result)} tickers tu DB")
        return result

    logger.warning("Bulk load rong, fallback sang load tung ticker")
    if tickers is None:
        tickers = get_watchlist() or _load_watchlist_csv()

    out = {}
    for ticker in tickers:
        df = load_ohlcv(ticker, days=days)
        if not df.empty and len(df) >= 50:
            out[ticker] = df
    logger.info(f"Loaded {len(out)}/{len(tickers)} tickers tu DB")
    return out


if __name__ == "__main__":
    import sys
    if "--refresh-watchlist" in sys.argv:
        refresh_watchlist()
    else:
        tickers = get_vn100_tickers()
        print(f"Tickers ({len(tickers)}): {tickers[:10]}...")
        df = fetch_ohlcv("VIC")
        if df is not None:
            print(df.tail(3))
