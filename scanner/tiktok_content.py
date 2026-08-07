"""
Tạo nội dung bài đăng TikTok: hook + caption + 2-3 ảnh, tổng hợp từ tin tức
chứng khoán, trong nước, quốc tế/chiến tranh.

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
from scanner.news_fetcher import fetch_category_news, fetch_war_news
from scanner.utils import logger

TIKTOK_OUTPUT_DIR = REPORTS_DIR / "tiktok"

# Mỗi story ứng với 1 category, theo đúng thứ tự xuất hiện trong bài đăng
_STORY_CATEGORIES = ["stock", "domestic", "war_or_international"]


def _pick_story(category: str) -> dict | None:
    """Chọn tin đầu tiên có ảnh hợp lệ trong category (ưu tiên tin chiến tranh nếu có)."""
    if category == "war_or_international":
        pool = fetch_war_news(max_items=10) or fetch_category_news("international", max_items=10)
    else:
        pool = fetch_category_news(category, max_items=10)
    for item in pool:
        if item.get("image"):
            return item
    return pool[0] if pool else None


def gather_stories(n: int = 3) -> list[dict]:
    """Chọn n tin, mỗi tin từ 1 category khác nhau (chứng khoán / trong nước / quốc tế-chiến tranh)."""
    stories = []
    seen_links = set()
    for cat in _STORY_CATEGORIES[:n]:
        story = _pick_story(cat)
        if story and story["link"] not in seen_links:
            story["category"] = cat
            stories.append(story)
            seen_links.add(story["link"])
    return stories


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


def generate_hook_and_caption(stories: list[dict]) -> dict:
    """Sinh hook (câu mở đầu gây chú ý) + caption (kèm hashtag) từ danh sách tin đã chọn."""
    today = date.today().strftime("%d/%m/%Y")
    news_block = "\n".join(
        f"{i}. [{s.get('category')}] {s['title']} — {s['summary']}"
        for i, s in enumerate(stories, 1)
    )

    prompt = textwrap.dedent(f"""
        Bạn là người biên tập nội dung TikTok về tài chính, chứng khoán, thời sự cho khán giả Việt Nam.
        Ngày: {today}

        TIN TỨC ĐÃ CHỌN (chứng khoán / trong nước / quốc tế-chiến tranh):
        {news_block}

        Hãy viết nội dung cho 1 video TikTok tổng hợp các tin trên, theo đúng định dạng:

        HOOK: [1 câu mở đầu cực gây chú ý, tò mò, dưới 15 từ, không markdown, không emoji]
        CAPTION: [đoạn caption đầy đủ 3-4 câu tóm tắt các tin theo thứ tự, giọng gần gũi dễ hiểu,
        kết thúc bằng 5-7 hashtag tiếng Việt liên quan chứng khoán/thời sự/tài chính, không markdown]

        Yêu cầu: tiếng Việt có dấu đầy đủ, không bịa thêm số liệu ngoài tin đã cho, không mở đầu bằng "Dưới đây là".
    """).strip()

    from scanner.ai_analyst import _call, _get_client
    try:
        client = _get_client()
        raw = _call(client, prompt, max_tokens=700)
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
        hook = stories[0]["title"] if stories else ""
        caption = " ".join(s["title"] for s in stories)

    return {"hook": hook, "caption": caption, "raw": raw}


def create_tiktok_post(n_stories: int = 3) -> Path:
    """Tạo 1 bài đăng TikTok: gom tin, tải ảnh, sinh hook/caption, lưu vào reports/tiktok/<ngày>/."""
    stories = gather_stories(n=n_stories)
    if not stories:
        raise RuntimeError("Không tìm được tin tức nào để tạo bài đăng")

    today_str = date.today().strftime("%Y-%m-%d")
    out_dir = TIKTOK_OUTPUT_DIR / today_str
    suffix = 1
    while out_dir.exists():
        suffix += 1
        out_dir = TIKTOK_OUTPUT_DIR / f"{today_str}_{suffix}"

    image_paths = download_images(stories, out_dir)
    content = generate_hook_and_caption(stories)

    (out_dir / "hook.txt").write_text(content["hook"], encoding="utf-8")
    (out_dir / "caption.txt").write_text(content["caption"], encoding="utf-8")

    meta = {
        "date": today_str,
        "hook": content["hook"],
        "caption": content["caption"],
        "images": [str(p) for p in image_paths],
        "sources": [
            {"category": s.get("category"), "title": s["title"], "link": s["link"], "source": s["source"]}
            for s in stories
        ],
    }
    (out_dir / "post_info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"Tạo bài TikTok xong: {out_dir} ({len(image_paths)} ảnh)")
    return out_dir


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tạo nội dung bài đăng TikTok từ tin tức")
    parser.add_argument("--n", type=int, default=3, help="Số tin tổng hợp (mặc định 3: chứng khoán/trong nước/quốc tế)")
    args = parser.parse_args()

    result_dir = create_tiktok_post(n_stories=args.n)
    print(f"\nĐã tạo bài đăng tại: {result_dir}")
    print("\n--- HOOK ---")
    print((result_dir / "hook.txt").read_text(encoding="utf-8"))
    print("\n--- CAPTION ---")
    print((result_dir / "caption.txt").read_text(encoding="utf-8"))
