"""Fetch company-level and market-level investor statistics via vnstock."""
from __future__ import annotations

import pandas as pd

from scanner.config import configure_vnstock
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
    Fetch foreign room and listed-share totals for the existing ownership ratios.
    """
    if not tickers:
        return {}
    market = configure_vnstock("market.equity.foreign_room")
    reference = configure_vnstock("reference.equity.list")
    rooms = market.Market().equity.foreign_room(symbols=tickers)
    listed = reference.Reference().equity.list()
    if rooms is None or rooms.empty or listed is None or listed.empty:
        return {}

    required_rooms = {"symbol", "foreign_room", "foreign_room_total"}
    required_listed = {"symbol", "listed_shares"}
    if not required_rooms.issubset(rooms.columns):
        raise ValueError(f"foreign_room response missing columns: {required_rooms - set(rooms.columns)}")
    if not required_listed.issubset(listed.columns):
        raise ValueError(f"equity list response missing columns: {required_listed - set(listed.columns)}")

    listed_by_symbol = listed.set_index("symbol")["listed_shares"]
    results: dict[str, dict] = {}
    for _, row in rooms.iterrows():
        sym = str(row["symbol"]).upper()
        shares = listed_by_symbol.get(sym)
        if pd.isna(shares) or float(shares) <= 0:
            continue
        total_value = row["foreign_room_total"]
        current_value = row["foreign_room"]
        if pd.isna(total_value) or pd.isna(current_value):
            continue
        total_room = float(total_value)
        current_room = float(current_value)
        owned = row.get("foreign_owned_volume")
        used_foreign = float(owned) if pd.notna(owned) else max(total_room - current_room, 0)
        results[sym] = {
            "foreign_pct": round(used_foreign / float(shares) * 100, 2),
            "foreign_max_pct": round(total_room / float(shares) * 100, 2),
        }
    logger.info(f"fetch_company_overviews (foreign_room): {len(results)}/{len(tickers)} OK")
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
    Overviews: fetch bulk equity quote/foreign-room data for all tickers.
    """
    all_tickers: list[str] = results["ticker"].tolist() if "ticker" in results.columns else []
    logger.info(f"Fetching company data: equity quote/foreign room ({len(all_tickers)} tickers)")
    overviews = fetch_company_overviews(all_tickers)
    market    = fetch_market_investor_count()
    return {"overviews": overviews, "market": market}
