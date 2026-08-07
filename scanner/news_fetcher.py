"""
Fetch tin tức từ RSS feeds — chứng khoán, trong nước, quốc tế.
Dùng stdlib (xml.etree + requests) — không cần dependency thêm.
"""
from __future__ import annotations

import itertools
import re
import xml.etree.ElementTree as ET

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.utils import logger

# category -> [(source_name, rss_url), ...]
_FEEDS_BY_CATEGORY: dict[str, list[tuple[str, str]]] = {
    "stock": [
        ("CafeF", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ],
    "domestic": [
        ("VnExpress", "https://vnexpress.net/rss/tin-moi-nhat.rss"),
    ],
    "international": [
        ("VnExpress", "https://vnexpress.net/rss/the-gioi.rss"),
    ],
    "business": [
        ("VnExpress", "https://vnexpress.net/rss/kinh-doanh.rss"),
    ],
}

# Giữ tương thích ngược: _FEEDS = feed chứng khoán, dùng cho fetch_news/fetch_hot_news
_FEEDS = _FEEDS_BY_CATEGORY["stock"]

# Từ khóa tin "hot" — chiến sự, nhân vật/quốc gia hay gây chú ý, ảnh hưởng thị trường toàn cầu.
# Dùng để chọn tin viral thay vì tin thời sự khô khan.
# Lưu ý: tránh từ khóa đơn âm quá rộng như "mỹ"/"nga" (dễ khớp nhầm, VD "một người Mỹ" trong
# tin không liên quan) — ưu tiên cụm từ đặc trưng.
_HOT_KEYWORDS = [
    "trump", "putin", "tập cận bình",
    "tổng thống mỹ", "nhà trắng", "washington", "phố wall", "chứng khoán mỹ", "quân đội mỹ",
    "chiến tranh", "chiến sự", "xung đột", "không kích", "tên lửa",
    "đình chiến", "ngừng bắn",
    "giá dầu", "dầu thô", "opec",
    "trung quốc", "bắc kinh", "đài loan",
    "iran", "israel", "gaza", "trung đông", "houthi", "hezbollah",
    "fed", "lãi suất fed", "cục dự trữ liên bang",
    "ukraine", "moscow", "điện kremlin", "quân nga",
    "nato", "triều tiên", "biển đông",
]

_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)


def _extract_image(item: ET.Element, description_raw: str) -> str:
    """Lấy URL ảnh đầu tiên từ <enclosure> hoặc thẻ <img> trong description."""
    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        return enclosure.get("url").strip()
    m = _IMG_RE.search(description_raw)
    return m.group(1).strip() if m else ""


def _parse_feed(source_name: str, url: str, limit: int) -> list[dict]:
    """Parse 1 RSS feed, trả về tối đa `limit` tin dạng {title, summary, source, link, image}."""
    items: list[dict] = []
    try:
        resp = requests.get(url, timeout=10, verify=False, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content.strip())
        channel = root.find("channel") or root
        for item in channel.findall("item"):
            if len(items) >= limit:
                break
            title = (item.findtext("title") or "").strip()
            desc_raw = (item.findtext("description") or "").strip()
            image = _extract_image(item, desc_raw)
            desc = re.sub(r"<[^>]+>", " ", desc_raw).strip()
            desc = " ".join(desc.split())[:300]
            link = (item.findtext("link") or "").strip()
            items.append({
                "title": title,
                "summary": desc,
                "source": source_name,
                "link": link,
                "image": image,
            })
    except Exception as e:
        logger.warning(f"RSS fetch failed [{source_name}]: {e}")
    return items


def _interleave(buckets: list[list[dict]], max_items: int) -> list[dict]:
    """Lấy lần lượt 1 tin từ mỗi nguồn (round-robin), bỏ trùng theo title."""
    results: list[dict] = []
    seen_titles: set[str] = set()
    for items in itertools.zip_longest(*buckets):
        for item in items:
            if not item or len(results) >= max_items:
                continue
            key = " ".join(item["title"].lower().split())
            if key in seen_titles:
                continue
            seen_titles.add(key)
            results.append(item)
    return results


def fetch_hot_news(max_items: int = 10) -> list[dict]:
    """Lấy tin tức nổi bật chung (chứng khoán, không lọc theo mã)."""
    return fetch_news(tickers=None, max_items=max_items)


def fetch_news(tickers: list[str] | None = None, max_items: int = 20) -> list[dict]:
    """
    Lấy tin chứng khoán từ RSS feeds, lấy đều từ mỗi nguồn (round-robin) để tránh 1 nguồn lấn át.
    Nếu có tickers → chỉ giữ bài nhắc đến ít nhất 1 mã trong danh sách.
    Trả về list[{title, summary, source, link, image}].
    """
    ticker_set = {t.upper() for t in tickers} if tickers else set()
    per_source = max(2, max_items // len(_FEEDS))

    buckets = [_parse_feed(name, url, per_source) for name, url in _FEEDS]

    if ticker_set:
        for bucket in buckets:
            bucket[:] = [
                it for it in bucket
                if any(t in (it["title"] + " " + it["summary"]).upper() for t in ticker_set)
            ]

    return _interleave(buckets, max_items)


def fetch_category_news(category: str, max_items: int = 10) -> list[dict]:
    """
    Lấy tin theo category: 'stock' | 'domestic' | 'international' | 'business'.
    Trả về list[{title, summary, source, link, image}], mới nhất trước.
    """
    feeds = _FEEDS_BY_CATEGORY.get(category, [])
    if not feeds:
        logger.warning(f"Category '{category}' không tồn tại")
        return []

    per_source = max(3, max_items // len(feeds))
    buckets = [_parse_feed(name, url, per_source) for name, url in feeds]
    return _interleave(buckets, max_items)


def fetch_hot_topic_news(max_items: int = 15) -> list[dict]:
    """
    Lọc tin "hot" — Trump, chiến sự, giá dầu, Trung Quốc, Đài Loan, Iran-Israel, Mỹ...
    từ nguồn quốc tế + kinh doanh, dùng để tạo nội dung viral thay vì tin thời sự khô khan.
    """
    pool = fetch_category_news("international", max_items=25) + fetch_category_news("business", max_items=15)
    filtered = []
    seen_titles: set[str] = set()
    for item in pool:
        key = " ".join(item["title"].lower().split())
        if key in seen_titles:
            continue
        combined = (item["title"] + " " + item["summary"]).lower()
        if any(kw in combined for kw in _HOT_KEYWORDS):
            filtered.append(item)
            seen_titles.add(key)
        if len(filtered) >= max_items:
            break
    return filtered
