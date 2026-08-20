"""
Tạo nội dung bài đăng TikTok: N bài độc lập, mỗi bài 1 chủ đề (hook + caption
+ 1 ảnh), chọn từ tin tức chứng khoán, trong nước, quốc tế/chiến tranh trong ngày.

Chỉ tạo nội dung (file) để đăng thủ công — không tự động đăng lên TikTok.
Chạy: python -m scanner.tiktok_content
"""
from __future__ import annotations

import argparse
import json
import re
import textwrap
from datetime import date
from io import BytesIO
from pathlib import Path

import requests
import urllib3
from PIL import Image

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.config import REPORTS_DIR
from scanner.news_fetcher import fetch_category_news, fetch_hot_topic_news
from scanner.utils import logger

TIKTOK_OUTPUT_DIR = REPORTS_DIR / "tiktok"

# Mỗi bài đăng là 1 chủ đề riêng — thiên về tin "hot" (Trump/chiến sự/giá dầu/
# Trung Quốc/Đài Loan/Iran-Israel/Mỹ...) để viral hơn, xen chứng khoán/kinh doanh
# để đa dạng chủ đề trong ngày. Không dùng "domestic" (tin tổng hợp VnExpress
# không lọc) — dễ lẫn tin đời sống/sức khoẻ lạc chủ đề kênh tài chính.
_TOPIC_PLAN = ["hot", "stock", "hot", "business", "hot"]


def gather_topics(n: int = 5) -> list[dict]:
    """Chọn n tin riêng biệt — mỗi tin là chủ đề cho 1 bài đăng TikTok độc lập,
    không tin nào lặp lại giữa các bài. Ưu tiên tin có ảnh trong từng category."""
    plan = (_TOPIC_PLAN * ((n // len(_TOPIC_PLAN)) + 1))[:n]

    pool_cache: dict[str, list[dict]] = {}
    topics: list[dict] = []
    seen_links: set[str] = set()

    def _pool(cat: str) -> list[dict]:
        if cat not in pool_cache:
            items = fetch_hot_topic_news(max_items=25) if cat == "hot" else fetch_category_news(cat, max_items=15)
            items.sort(key=lambda s: not s.get("image"))  # ưu tiên tin có ảnh lên đầu
            pool_cache[cat] = items
        return pool_cache[cat]

    for cat in plan:
        story = next((s for s in _pool(cat) if s["link"] not in seen_links), None)
        if story:
            story = {**story, "category": cat}
            topics.append(story)
            seen_links.add(story["link"])

    # Thiếu tin (1 category hết bài) → bù bằng tin hot còn dư
    if len(topics) < n:
        for s in _pool("hot"):
            if len(topics) >= n:
                break
            if s["link"] in seen_links:
                continue
            topics.append({**s, "category": "hot"})
            seen_links.add(s["link"])

    return topics[:n]


def download_images(stories: list[dict], out_dir: Path) -> list[Path]:
    """Tải ảnh minh họa từ link báo gốc, chuẩn hóa về JPEG, lưu vào out_dir/1.jpg, 2.jpg, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, story in enumerate(stories, 1):
        url = story.get("image")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=15, verify=False, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            path = out_dir / f"{i}.jpg"
            img.save(path, "JPEG", quality=90)
            paths.append(path)
            story["image_path"] = str(path)
        except Exception as e:
            logger.warning(f"Tải ảnh thất bại [{story['title'][:40]}]: {e}")
    return paths


def _strip_md(text: str) -> str:
    return re.sub(r"[*_#`]+", "", text).strip()


def generate_single_hook_caption(story: dict) -> dict:
    """Sinh hook (câu mở đầu gây chú ý) + caption (kèm hashtag) cho 1 bài TikTok
    xoay quanh đúng 1 chủ đề/tin — không pha trộn nhiều tin vào 1 bài."""
    today = date.today().strftime("%d/%m/%Y")
    news_block = f"[{story.get('category')}] {story['title']} — {story['summary']}"

    prompt = textwrap.dedent(f"""
        Bạn là biên tập nội dung TikTok viral về thời sự - tài chính - chứng khoán cho khán giả
        Việt Nam, phong cách như các kênh tin tức giật tít nhưng đúng sự thật.
        Ngày: {today}

        TIN CỦA BÀI NÀY (chỉ viết về đúng tin này, không pha thêm tin khác):
        {news_block}

        Hãy viết nội dung cho 1 video TikTok riêng về tin trên, theo đúng định dạng:

        HOOK: [1 câu mở đầu SIÊU giật gân, gây sốc/tò mò ngay từ giây đầu, dưới 15 từ, văn phong
        mạng xã hội, không markdown, không emoji]
        CAPTION: [đoạn caption 3-4 câu, giọng dồn dập kiểu bản tin nóng, nêu rõ chuyện gì đang xảy
        ra và tác động tới túi tiền/thị trường chứng khoán Việt Nam nếu hợp lý, kết thúc bằng 6-8
        hashtag tiếng Việt viral liên quan trực tiếp tới tin này, không markdown]

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ
        - KHÔNG bịa thêm số liệu/sự kiện ngoài tin đã cho — chỉ được giật tít bằng cách diễn đạt,
          không phóng đại sai sự thật
        - Không mở đầu bằng "Dưới đây là"
    """).strip()

    from scanner.ai_analyst import _call, _get_client
    try:
        client = _get_client()
        raw = _call(client, prompt, max_tokens=500)
    except Exception as e:
        logger.warning(f"AI không khả dụng: {e}")
        raw = ""

    hook, caption = "", ""
    parts = re.split(r"(?im)^\s*CAPTION\s*:\s*", raw, maxsplit=1)
    if len(parts) == 2:
        hook = _strip_md(re.sub(r"(?im)^\s*HOOK\s*:\s*", "", parts[0]))
        caption = _strip_md(parts[1])
    elif raw:
        hook = _strip_md(raw)

    if not hook and not caption:
        # Fallback khi AI không khả dụng hoặc không parse được
        hook = story["title"]
        caption = story["title"]

    return {"hook": hook, "caption": caption, "raw": raw}


def create_tiktok_posts(n: int = 5) -> list[Path]:
    """Tạo n bài đăng TikTok độc lập (mỗi bài 1 chủ đề): gom tin, tải ảnh, sinh
    hook/caption riêng cho từng bài, lưu vào reports/tiktok/<ngày>/post_<i>/."""
    topics = gather_topics(n=n)
    if not topics:
        raise RuntimeError("Không tìm được tin tức nào để tạo bài đăng")

    today_str = date.today().strftime("%Y-%m-%d")
    base_dir = TIKTOK_OUTPUT_DIR / today_str
    suffix = 1
    while base_dir.exists():
        suffix += 1
        base_dir = TIKTOK_OUTPUT_DIR / f"{today_str}_{suffix}"

    out_dirs: list[Path] = []
    for i, story in enumerate(topics, 1):
        out_dir = base_dir / f"post_{i}"
        image_paths = download_images([story], out_dir)
        content = generate_single_hook_caption(story)

        (out_dir / "hook.txt").write_text(content["hook"], encoding="utf-8")
        (out_dir / "caption.txt").write_text(content["caption"], encoding="utf-8")

        meta = {
            "date": today_str,
            "hook": content["hook"],
            "caption": content["caption"],
            "images": [str(p) for p in image_paths],
            "source": {
                "category": story.get("category"), "title": story["title"],
                "link": story["link"], "source": story["source"],
            },
        }
        (out_dir / "post_info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        out_dirs.append(out_dir)
        logger.info(f"Tạo bài TikTok {i}/{len(topics)} xong: {out_dir} ({len(image_paths)} ảnh)")

    return out_dirs


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tạo nội dung bài đăng TikTok từ tin tức")
    parser.add_argument("--n", type=int, default=5, help="Số bài đăng độc lập cần tạo, mỗi bài 1 chủ đề (mặc định 5)")
    args = parser.parse_args()

    result_dirs = create_tiktok_posts(n=args.n)
    for i, out_dir in enumerate(result_dirs, 1):
        print(f"\n=== Bài {i}/{len(result_dirs)}: {out_dir} ===")
        print("--- HOOK ---")
        print((out_dir / "hook.txt").read_text(encoding="utf-8"))
        print("--- CAPTION ---")
        print((out_dir / "caption.txt").read_text(encoding="utf-8"))
