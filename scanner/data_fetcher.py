import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from scanner.config import FETCH_DELAY, LOOKBACK_DAYS, MAX_WORKERS, VNSTOCK_API_KEY, WATCHLIST_FILE
from scanner.utils import logger


def _set_api_key() -> None:
    """Set vnstock API key if provided in .env."""
    if VNSTOCK_API_KEY:
        try:
            import vnstock
            vnstock.api_key = VNSTOCK_API_KEY
        except Exception:
            pass


def get_vn100_tickers() -> list[str]:
    """Return top 100 VN-Index tickers. Tries vnstock API, falls back to CSV."""
    tickers = _fetch_from_api()
    if tickers:
        _save_watchlist(tickers)
        return tickers

    logger.warning("vnstock API failed, loading watchlist from CSV")
    return _load_watchlist_csv()


def refresh_watchlist() -> list[str]:
    """Force-refresh watchlist from API and save to CSV."""
    tickers = _fetch_from_api()
    if not tickers:
        raise RuntimeError("Cannot refresh watchlist: API unavailable")
    _save_watchlist(tickers)
    logger.info(f"Watchlist refreshed: {len(tickers)} tickers")
    return tickers


def fetch_ohlcv(ticker: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame | None:
    """Fetch OHLCV data for one ticker via vnstock.api (new API)."""
    end = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    # New API (vnstock >= 0.3.x after 31/08/2025 deprecation)
    try:
        from vnstock.api.quote import Quote
        q = Quote(symbol=ticker, source="VCI")
        df = q.history(start=start, end=end, interval="1D")
        if df is not None and not df.empty:
            return _normalize_columns(df)
    except Exception as e:
        logger.debug(f"{ticker}: VCI source failed — {e}")

    # Fallback: TCBS source
    try:
        from vnstock.api.quote import Quote
        q = Quote(symbol=ticker, source="TCBS")
        df = q.history(start=start, end=end, interval="1D")
        if df is not None and not df.empty:
            return _normalize_columns(df)
    except Exception as e:
        logger.debug(f"{ticker}: TCBS source failed — {e}")

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
    # Method A: VCI source — symbols by group VN100
    try:
        from vnstock.api.listing import Listing
        listing = Listing(source="VCI")
        df = listing.symbols_by_group("VN100")
        if df is not None and not df.empty:
            col = _find_ticker_col(df)
            tickers = df[col].str.upper().tolist()
            if len(tickers) >= 50:
                logger.info(f"VN100 from VCI: {len(tickers)} tickers")
                return tickers
    except Exception as e:
        logger.debug(f"VCI VN100 group failed: {e}")

    # Method B: VCI source — all HOSE symbols, take top 100
    try:
        from vnstock.api.listing import Listing
        listing = Listing(source="VCI")
        df = listing.symbols_by_exchange(exchange="HOSE")
        if df is not None and not df.empty:
            col = _find_ticker_col(df)
            tickers = df[col].str.upper().tolist()[:100]
            if len(tickers) >= 50:
                logger.info(f"HOSE top-100 from VCI: {len(tickers)} tickers")
                return tickers
    except Exception as e:
        logger.debug(f"VCI HOSE listing failed: {e}")

    # Method C: TCBS source fallback
    try:
        from vnstock.api.listing import Listing
        listing = Listing(source="TCBS")
        df = listing.symbols_by_exchange(exchange="HOSE")
        if df is not None and not df.empty:
            col = _find_ticker_col(df)
            tickers = df[col].str.upper().tolist()[:100]
            if len(tickers) >= 50:
                logger.info(f"HOSE top-100 from TCBS: {len(tickers)} tickers")
                return tickers
    except Exception as e:
        logger.debug(f"TCBS HOSE listing failed: {e}")

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


def _find_ticker_col(df: pd.DataFrame) -> str:
    for name in ["ticker", "symbol", "code", "Ticker", "Symbol", "Code"]:
        if name in df.columns:
            return name
    return df.columns[0]


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
        df = df.set_index("date").sort_index()
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    return df[required].dropna()


def load_all_from_db(days: int = LOOKBACK_DAYS) -> dict[str, pd.DataFrame]:
    """
    Đọc toàn bộ OHLCV từ PostgreSQL cho tất cả ticker trong watchlist.
    Đây là hàm chính cho scanner sau khi đã có DB.
    """
    from scanner.database import get_watchlist, load_ohlcv
    tickers = get_watchlist()
    if not tickers:
        logger.warning("Watchlist DB trống, fallback sang CSV")
        tickers = _load_watchlist_csv()

    result = {}
    for ticker in tickers:
        df = load_ohlcv(ticker, days=days)
        if not df.empty and len(df) >= 50:
            result[ticker] = df
        else:
            logger.debug(f"{ticker}: không đủ data trong DB ({len(df)} bars)")

    logger.info(f"Loaded {len(result)}/{len(tickers)} tickers từ DB")
    return result


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
