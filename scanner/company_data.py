"""Fetch company-level and market-level investor statistics via vnstock."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from scanner.utils import logger

_MAX_WORKERS = 5

_MARKET_FALLBACK = {
    "total":    9_200_000,
    "domestic": 9_110_000,
    "foreign":     90_000,
    "as_of":   "Q4/2025",
    "source":  "VSD (ước tính)",
}


def fetch_company_overviews(tickers: list[str]) -> dict[str, dict]:
    """
    Bulk fetch foreign ownership via Trading.price_board() — 1 API call cho tất cả tickers.
    Tính: foreign_pct, foreign_max_pct từ current_room / total_room / listed_share.
    """
    if not tickers:
        return {}
    try:
        from vnstock.api.trading import Trading
        t = Trading(symbol=tickers[0], source="VCI")
        df = t.price_board(symbols_list=tickers)
        if df is None or df.empty:
            return {}

        # Flatten MultiIndex columns → "listing_symbol", "match_current_room", ...
        df.columns = ["_".join(c).strip("_") for c in df.columns]

        results: dict[str, dict] = {}
        for _, row in df.iterrows():
            sym          = str(row.get("listing_symbol", "")).upper()
            listed       = float(row.get("listing_listed_share") or 0)
            total_room   = float(row.get("match_total_room")     or 0)
            current_room = float(row.get("match_current_room")   or 0)
            if not sym or listed <= 0:
                continue
            used_foreign    = max(total_room - current_room, 0)
            foreign_pct     = round(used_foreign   / listed * 100, 2)
            foreign_max_pct = round(total_room     / listed * 100, 2)
            results[sym] = {
                "foreign_pct":     foreign_pct,
                "foreign_max_pct": foreign_max_pct,
                "free_float_pct":  "",   # không có trong price_board
            }
        logger.info(f"fetch_company_overviews (bulk): {len(results)}/{len(tickers)} OK")
        return results
    except Exception as e:
        logger.warning(f"fetch_company_overviews bulk failed — {e}")
        return {}


def _one_shareholders(ticker: str, top_n: int) -> tuple[str, pd.DataFrame]:
    try:
        from vnstock.api.company import Company
        c = Company(symbol=ticker, source="VCI")
        df = c.shareholders()
        if df is None or df.empty:
            return ticker, pd.DataFrame()
        df = df[["share_holder", "quantity", "share_own_percent"]].head(top_n).copy()
        df["ticker"] = ticker
        return ticker, df
    except Exception as e:
        logger.debug(f"{ticker}: shareholders failed — {e}")
        return ticker, pd.DataFrame()


def fetch_shareholders_batch(tickers: list[str], top_n: int = 10) -> dict[str, pd.DataFrame]:
    """Parallel fetch top shareholders cho signal tickers. Returns {ticker: DataFrame}."""
    if not tickers:
        return {}
    results: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = {ex.submit(_one_shareholders, t, top_n): t for t in tickers}
        for fut in as_completed(futs):
            t, df = fut.result()
            if not df.empty:
                results[t] = df
    logger.info(f"fetch_shareholders_batch: {len(results)}/{len(tickers)} OK")
    return results


def fetch_market_investor_count() -> dict:
    """
    Get total market investor accounts from VSD or public sources.
    Falls back to latest known figure if live fetch fails.
    """
    try:
        import requests
        resp = requests.get(
            "https://api.vsd.org.vn/v1/statistics/investor-account",
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.ok:
            data = resp.json()
            if "total" in data:
                return {
                    "total":    int(data["total"]),
                    "domestic": int(data.get("domestic", 0)),
                    "foreign":  int(data.get("foreign", 0)),
                    "as_of":    data.get("period", ""),
                    "source":   "VSD API",
                }
    except Exception:
        pass
    return _MARKET_FALLBACK


def build_company_data(results: pd.DataFrame, signals: dict) -> dict:
    """
    Fetch all company data needed for Excel report.
    - Overviews: bulk price_board cho tất cả tickers (1 API call)
    - Shareholders: per-ticker chỉ cho signal tickers (~15 calls)
    """
    all_tickers: list[str] = results["ticker"].tolist() if "ticker" in results.columns else []

    signal_tickers: list[str] = []
    for df in signals.values():
        if not df.empty and "ticker" in df.columns:
            signal_tickers.extend(df["ticker"].tolist())
    signal_tickers = list(set(signal_tickers))

    logger.info(f"Fetching company data: bulk overviews ({len(all_tickers)} tickers), shareholders ({len(signal_tickers)} signal tickers)")
    overviews    = fetch_company_overviews(all_tickers)
    shareholders = fetch_shareholders_batch(signal_tickers)
    market       = fetch_market_investor_count()

    return {"overviews": overviews, "shareholders": shareholders, "market": market}
