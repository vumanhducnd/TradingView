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


def _one_overview(ticker: str) -> tuple[str, dict | None]:
    try:
        from vnstock.api.company import Company
        c = Company(symbol=ticker, source="VCI")
        df = c.overview()
        if df is None or df.empty:
            return ticker, None
        r = df.iloc[0]
        return ticker, {
            "foreign_pct":     round(float(r.get("foreigner_percentage")       or 0) * 100, 2),
            "foreign_max_pct": round(float(r.get("maximum_foreign_percentage") or 0) * 100, 2),
            "free_float_pct":  round(float(r.get("free_float_percentage")      or 0) * 100, 2),
            "state_pct":       round(float(r.get("state_percentage")           or 0) * 100, 2),
        }
    except Exception as e:
        logger.debug(f"{ticker}: overview failed — {e}")
        return ticker, None


def fetch_company_overviews(tickers: list[str]) -> dict[str, dict]:
    """Parallel fetch foreign ownership data. Returns {ticker: {foreign_pct, ...}}."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = {ex.submit(_one_overview, t): t for t in tickers}
        for fut in as_completed(futs):
            t, data = fut.result()
            if data:
                results[t] = data
    logger.info(f"fetch_company_overviews: {len(results)}/{len(tickers)} OK")
    return results


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
    """Parallel fetch top shareholders for given tickers. Returns {ticker: DataFrame}."""
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


def build_company_data(signals: dict) -> dict:
    """
    Fetch all company data needed for Excel report.
    Chỉ fetch overviews + shareholders cho signal tickers (không cả watchlist)
    để tránh vượt rate limit 60 req/phút của vnstock community.
    """
    signal_tickers: list[str] = []
    for df in signals.values():
        if not df.empty and "ticker" in df.columns:
            signal_tickers.extend(df["ticker"].tolist())
    signal_tickers = list(set(signal_tickers))

    logger.info(f"Fetching company data: {len(signal_tickers)} signal tickers (overviews + shareholders)")
    overviews    = fetch_company_overviews(signal_tickers)
    shareholders = fetch_shareholders_batch(signal_tickers)
    market       = fetch_market_investor_count()

    return {"overviews": overviews, "shareholders": shareholders, "market": market}
