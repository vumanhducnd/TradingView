"""
Fetch tin tức chứng khoán VN từ RSS feeds.
Dùng stdlib (xml.etree + requests) — không cần dependency thêm.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.utils import logger

_FEEDS = [
    ("VnEconomy",  "https://vneconomy.vn/chung-khoan.rss"),
    ("BaoDauTu",   "https://baodautu.vn/chung-khoan.rss"),
    ("CafeF",      "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("Vietstock",  "https://vietstock.vn/rss/tin-tuc-chung-khoan.rss"),
]


def fetch_news(tickers: list[str] | None = None, max_items: int = 20) -> list[dict]:
    """
    Lấy tin từ RSS feeds.
    Nếu có tickers → chỉ giữ bài nhắc đến ít nhất 1 mã trong danh sách.
    Trả về list[{title, summary, source}].
    """
    results: list[dict] = []
    ticker_set = {t.upper() for t in tickers} if tickers else set()

    for source_name, url in _FEEDS:
        try:
            resp = requests.get(url, timeout=10, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root    = ET.fromstring(resp.content)
            channel = root.find("channel") or root
            for item in channel.findall("item"):
                title   = (item.findtext("title")       or "").strip()
                desc    = (item.findtext("description") or "").strip()
                # Loại bỏ HTML tags đơn giản
                import re
                desc = re.sub(r"<[^>]+>", " ", desc).strip()
                desc = " ".join(desc.split())[:300]

                if ticker_set:
                    combined = (title + " " + desc).upper()
                    if not any(t in combined for t in ticker_set):
                        continue

                results.append({
                    "title":   title,
                    "summary": desc,
                    "source":  source_name,
                })
        except Exception as e:
            logger.warning(f"RSS fetch failed [{source_name}]: {e}")

    return results[:max_items]
