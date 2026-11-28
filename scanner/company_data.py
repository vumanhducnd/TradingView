"""Fetch company-level and market-level investor statistics via vnstock."""
from __future__ import annotations

import pandas as pd

from scanner.utils import logger

_MARKET_FALLBACK = {
    "total":    9_200_000,
    "domestic": 9_110_000,
    "foreign":     90_000,
    "as_of":   "Q4/2025",
    "source":  "VSD (ước tính)",
}


def fetch_company_overviews(tickers: list[str]) -> dict[str, dict]:
    """
    Bulk fetch foreign ownership via vnstock Market.equity.foreign_room() + Reference.equity.list().
    Tính: foreign_pct, foreign_max_pct từ current_room / total_room / listed_share.
    """
    if not tickers:
        return {}
    try:
        # vnstock thế hệ 6: room ngoại từ Market.equity.foreign_room (foreign_room_total = tổng room,
        # foreign_room = room còn lại), số CP niêm yết từ Reference.equity.list (listed_shares).
        from scanner.vnstock_sources import ensure_sources
        ensure_sources()
        from vnstock import Market, Reference
        df = Market().equity.foreign_room(list(tickers))
        if df is None or df.empty:
            return {}
        listing = Reference().equity.list()
        listed_map = dict(zip(listing["symbol"].astype(str).str.upper(), listing["listed_shares"]))

        import pandas as pd

        def _num(x) -> float:
            # thế hệ 6 dùng kiểu nullable (Int64) — ô trống là pd.NA
            return 0.0 if x is None or pd.isna(x) else float(x)

        results: dict[str, dict] = {}
        for _, row in df.iterrows():
            sym          = str(row.get("symbol", "")).upper()
            listed       = _num(listed_map.get(sym))
            total_room   = _num(row.get("foreign_room_total"))
            current_room = _num(row.get("foreign_room"))
            if not sym or listed <= 0:
                continue
            used_foreign    = max(total_room - current_room, 0)
            results[sym] = {
                "foreign_pct":     round(used_foreign / listed * 100, 2),
                "foreign_max_pct": round(total_room   / listed * 100, 2),
            }
        logger.info(f"fetch_company_overviews (bulk): {len(results)}/{len(tickers)} OK")
        return results
    except Exception as e:
        logger.warning(f"fetch_company_overviews bulk failed — {e}")
        return {}


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
    Overviews: bulk price_board cho tất cả tickers (1 API call).
    """
    all_tickers: list[str] = results["ticker"].tolist() if "ticker" in results.columns else []
    logger.info(f"Fetching company data: bulk price_board ({len(all_tickers)} tickers)")
    overviews = fetch_company_overviews(all_tickers)
    market    = fetch_market_investor_count()
    return {"overviews": overviews, "market": market}
