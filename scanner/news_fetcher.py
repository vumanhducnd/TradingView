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
    ("CafeF", "https://cafef.vn/thi-truong-chung-khoan.rss"),
]


def fetch_hot_news(max_items: int = 10) -> list[dict]:
    """Lấy tin tức nổi bật chung (không lọc theo mã)."""
    return fetch_news(tickers=None, max_items=max_items)


def fetch_news(tickers: list[str] | None = None, max_items: int = 20) -> list[dict]:
    """
    Lấy tin từ RSS feeds, lấy đều từ mỗi nguồn (round-robin) để tránh 1 nguồn lấn át.
    Nếu có tickers → chỉ giữ bài nhắc đến ít nhất 1 mã trong danh sách.
    Trả về list[{title, summary, source}].
    """
    import re
    ticker_set = {t.upper() for t in tickers} if tickers else set()
    per_source = max(2, max_items // len(_FEEDS))

    buckets: list[list[dict]] = []
    for source_name, url in _FEEDS:
        bucket: list[dict] = []
        try:
            resp = requests.get(url, timeout=10, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root    = ET.fromstring(resp.content.strip())
            channel = root.find("channel") or root
            for item in channel.findall("item"):
                if len(bucket) >= per_source:
                    break
                title = (item.findtext("title")       or "").strip()
                desc  = (item.findtext("description") or "").strip()
                desc  = re.sub(r"<[^>]+>", " ", desc).strip()
                desc  = " ".join(desc.split())[:300]

                if ticker_set:
                    combined = (title + " " + desc).upper()
                    if not any(t in combined for t in ticker_set):
                        continue

                link = (item.findtext("link") or "").strip()
                bucket.append({
                    "title":   title,
                    "summary": desc,
                    "source":  source_name,
                    "link":    link,
                })
        except Exception as e:
            logger.warning(f"RSS fetch failed [{source_name}]: {e}")
        buckets.append(bucket)

    # Interleave: lấy lần lượt 1 tin từ mỗi nguồn, bỏ trùng theo title
    results: list[dict] = []
    seen_titles: set[str] = set()
    for items in __import__("itertools").zip_longest(*buckets):
        for item in items:
            if not item or len(results) >= max_items:
                continue
            # Normalize title để so sánh: lowercase, bỏ khoảng trắng thừa
            key = " ".join(item["title"].lower().split())
            if key in seen_titles:
                continue
            seen_titles.add(key)
            results.append(item)

    return results
