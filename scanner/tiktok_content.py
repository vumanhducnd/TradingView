"""
Tạo nội dung bài đăng TikTok: N bài độc lập, mỗi bài random 1 trong nhiều KIỂU NỘI DUNG
(POST_STYLES) — tin đơn, carousel nhiều ảnh, top mã, chart TradingView, recap nhiều tin —
để nội dung phong phú, không lặp 1 kiểu mỗi ngày.

Chỉ tạo nội dung (file) để đăng thủ công — không tự động đăng lên TikTok.
Chạy: python -m scanner.tiktok_content
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import textwrap
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import requests
import urllib3
from PIL import Image, ImageDraw, ImageFont

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.config import REPORTS_DIR
from scanner.news_fetcher import fetch_article_images, fetch_category_news, fetch_hot_topic_news
from scanner.utils import fmt_date, fmt_price, logger

TIKTOK_OUTPUT_DIR = REPORTS_DIR / "tiktok"

# Font hỗ trợ tiếng Việt (font mặc định của Pillow không có dấu) — bundle sẵn trong repo
# thay vì phụ thuộc font hệ thống (server Linux thường không có font tiếng Việt cài sẵn).
_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_FONT_REGULAR = _FONT_DIR / "DejaVuSans.ttf"
_FONT_BOLD = _FONT_DIR / "DejaVuSans-Bold.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_BOLD if bold else _FONT_REGULAR), size)


def _val(row, *names, default=None):
    """Lấy giá trị đầu tiên không-NaN theo tên cột — fallback giữa dual mode
    (long_/short_) và single mode, giống quy ước _val trong bot_interactive.py."""
    for name in names:
        v = row.get(name) if isinstance(row, dict) else (row[name] if name in row.index else None)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return default


def _to_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

# Số ảnh tối đa tải về cho mỗi bài "carousel" (ảnh đại diện RSS + ảnh trong nội dung bài báo)
_IMAGES_PER_POST = 3

# Các kiểu nội dung — mỗi bài trong batch random 1 kiểu (hoặc ép cứng qua tham số `style`)
POST_STYLES: dict[str, str] = {
    "single":      "📰 Tin đơn",
    "carousel":    "🖼 Carousel nhiều ảnh",
    "top":         "🏆 Top mã vùng xanh",
    "chart":       "📈 Chart TradingView",
    "recap":       "📋 Recap nhiều tin",
    "buy_signal":  "🟢 Tín hiệu MUA trong ngày",
    "sell_signal": "🔴 Tín hiệu BÁN trong ngày",
}

# Style nào cần rút tin tức từ pool (và tốn bao nhiêu tin/bài) — "top"/"chart"/"buy_signal"/
# "sell_signal" lấy dữ liệu từ scanner DB nên không tốn tin tức.
_NEWS_STYLES_COST = {"single": 1, "carousel": 1, "recap": 3}

# Mỗi bài đăng là 1 chủ đề riêng — thiên về tin "hot" (Trump/chiến sự/giá dầu/
# Trung Quốc/Đài Loan/Iran-Israel/Mỹ...) để viral hơn, xen chứng khoán/kinh doanh
# để đa dạng chủ đề trong ngày. Không dùng "domestic" (tin tổng hợp VnExpress
# không lọc) — dễ lẫn tin đời sống/sức khoẻ lạc chủ đề kênh tài chính.
_TOPIC_PLAN = ["hot", "stock", "hot", "business", "hot"]


def gather_topic_pool(size: int) -> list[dict]:
    """Chuẩn bị 1 pool `size` tin độc lập, không trùng nhau — các style cần tin
    (single/carousel/recap) rút dần từ đầu pool theo số lượng mỗi bài cần."""
    plan = (_TOPIC_PLAN * ((size // len(_TOPIC_PLAN)) + 1))[:size]

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
    if len(topics) < size:
        for s in _pool("hot"):
            if len(topics) >= size:
                break
            if s["link"] in seen_links:
                continue
            topics.append({**s, "category": "hot"})
            seen_links.add(s["link"])

    return topics[:size]


def gather_topics(n: int = 5) -> list[dict]:
    """Giữ tương thích ngược — n tin riêng biệt cho n bài kiểu tin đơn/carousel."""
    return gather_topic_pool(n)


def download_images(stories: list[dict], out_dir: Path, max_per_story: int = _IMAGES_PER_POST) -> list[Path]:
    """Tải ảnh minh họa từ link báo gốc — ảnh đại diện RSS + ảnh trong nội dung bài báo
    (scrape thêm qua fetch_article_images), chuẩn hóa về JPEG, lưu vào out_dir/1.jpg, 2.jpg, ..."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    idx = 1
    for story in stories:
        urls: list[str] = []
        if story.get("image"):
            urls.append(story["image"])
        if len(urls) < max_per_story:
            for u in fetch_article_images(story.get("link", ""), max_images=max_per_story):
                if u not in urls:
                    urls.append(u)
        urls = urls[:max_per_story]

        story_paths: list[Path] = []
        for url in urls:
            try:
                resp = requests.get(url, timeout=15, verify=False, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                path = out_dir / f"{idx}.jpg"
                img.save(path, "JPEG", quality=90)
                story_paths.append(path)
                idx += 1
            except Exception as e:
                logger.warning(f"Tải ảnh thất bại [{story['title'][:40]}]: {e}")
        story["image_paths"] = [str(p) for p in story_paths]
        paths.extend(story_paths)
    return paths


def _strip_md(text: str) -> str:
    return re.sub(r"[*_#`]+", "", text).strip()


def _call_ai_sections(prompt: str, n_images: int) -> tuple[str, str, list[str]]:
    """Gọi AI với prompt đã yêu cầu định dạng HOOK/CAPTION/IMG1..N, parse ra
    (hook, caption, [caption ảnh 1, ...])."""
    from scanner.ai_analyst import _call, _get_client
    try:
        client = _get_client()
        raw = _call(client, prompt, max_tokens=700)
    except Exception as e:
        logger.warning(f"AI không khả dụng: {e}")
        raw = ""

    sections: dict[str, str] = {}
    parts = re.split(r"(?im)^\s*(HOOK|CAPTION|IMG\d+)\s*:\s*", raw)
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].upper()] = _strip_md(parts[i + 1])

    hook = sections.get("HOOK", "")
    caption = sections.get("CAPTION", "")
    image_captions = [sections.get(f"IMG{i}", "") for i in range(1, n_images + 1)]
    # Ảnh thiếu caption riêng (AI không trả đủ) → dùng lại caption tổng
    image_captions = [c or caption for c in image_captions]
    return hook, caption, image_captions


def generate_single_hook_caption(story: dict, n_images: int = 1) -> dict:
    """Sinh hook (câu mở đầu gây chú ý) + caption tổng (kèm hashtag) + caption riêng
    cho từng ảnh (n_images ảnh) cho 1 bài TikTok xoay quanh đúng 1 chủ đề/tin —
    không pha trộn nhiều tin vào 1 bài."""
    today = date.today().strftime("%d/%m/%Y")
    news_block = f"[{story.get('category')}] {story['title']} — {story['summary']}"
    n_images = max(1, n_images)

    img_spec = "\n".join(
        f"IMG{i}: [caption ngắn 1 câu cho ảnh thứ {i} trong bài, nêu góc nhìn/khía cạnh khác nhau"
        f" của cùng tin trên, không lặp y nguyên caption tổng, không markdown]"
        for i in range(1, n_images + 1)
    )

    prompt = textwrap.dedent(f"""
        Bạn là biên tập nội dung TikTok viral về thời sự - tài chính - chứng khoán cho khán giả
        Việt Nam, phong cách như các kênh tin tức giật tít nhưng đúng sự thật.
        Ngày: {today}

        TIN CỦA BÀI NÀY (chỉ viết về đúng tin này, không pha thêm tin khác):
        {news_block}

        Bài đăng này có {n_images} ảnh minh họa. Hãy viết nội dung cho 1 video TikTok riêng về
        tin trên, theo đúng định dạng:

        HOOK: [1 câu mở đầu SIÊU giật gân, gây sốc/tò mò ngay từ giây đầu, dưới 15 từ, văn phong
        mạng xã hội, không markdown, không emoji]
        CAPTION: [đoạn caption 3-4 câu, giọng dồn dập kiểu bản tin nóng, nêu rõ chuyện gì đang xảy
        ra và tác động tới túi tiền/thị trường chứng khoán Việt Nam nếu hợp lý, kết thúc bằng 6-8
        hashtag tiếng Việt viral liên quan trực tiếp tới tin này, không markdown]
        {img_spec}

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ
        - KHÔNG bịa thêm số liệu/sự kiện ngoài tin đã cho — chỉ được giật tít bằng cách diễn đạt,
          không phóng đại sai sự thật
        - Không mở đầu bằng "Dưới đây là"
    """).strip()

    hook, caption, image_captions = _call_ai_sections(prompt, n_images)
    if not hook and not caption:
        # Fallback khi AI không khả dụng hoặc không parse được
        hook = story["title"]
        caption = story["title"]
        image_captions = [caption] * n_images

    return {"hook": hook, "caption": caption, "image_captions": image_captions}


def generate_recap_hook_caption(stories: list[dict]) -> dict:
    """Sinh hook + caption tổng hợp cho bài 'điểm tin nhanh' gộp nhiều tin khác nhau
    (mỗi ảnh trong bài ứng với 1 tin riêng, theo đúng thứ tự `stories`)."""
    today = date.today().strftime("%d/%m/%Y")
    n = len(stories)
    news_blocks = "\n".join(
        f"Tin {i}: [{s.get('category')}] {s['title']} — {s['summary']}"
        for i, s in enumerate(stories, 1)
    )
    img_spec = "\n".join(
        f"IMG{i}: [caption ngắn 1 câu tóm tắt đúng Tin {i} ở trên, không markdown]"
        for i in range(1, n + 1)
    )

    prompt = textwrap.dedent(f"""
        Bạn là biên tập nội dung TikTok viral về thời sự - tài chính - chứng khoán cho khán giả
        Việt Nam, phong cách như các kênh tin tức giật tít nhưng đúng sự thật.
        Ngày: {today}

        {n} TIN NÓNG TRONG NGÀY (viết 1 bài điểm tin gộp cả {n} tin, không bịa thêm sự kiện
        ngoài các tin dưới đây):
        {news_blocks}

        Hãy viết nội dung cho 1 video TikTok dạng "điểm tin nhanh", theo đúng định dạng:

        HOOK: [1 câu mở đầu SIÊU giật gân, báo hiệu sắp có {n} tin nóng liên tiếp, dưới 15 từ,
        văn phong mạng xã hội, không markdown, không emoji]
        CAPTION: [đoạn tóm tắt cả {n} tin, giọng dồn dập kiểu bản tin nóng, 4-6 câu, kết thúc
        bằng 6-8 hashtag tiếng Việt viral liên quan tới các tin trên, không markdown]
        {img_spec}

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ
        - KHÔNG bịa thêm số liệu/sự kiện ngoài các tin đã cho
        - Không mở đầu bằng "Dưới đây là"
    """).strip()

    hook, caption, image_captions = _call_ai_sections(prompt, n)
    if not hook and not caption:
        hook = stories[0]["title"]
        caption = " | ".join(s["title"] for s in stories)
        image_captions = [s["title"] for s in stories]

    return {"hook": hook, "caption": caption, "image_captions": image_captions}


def _draw_info_card(path: Path, badge: str, ticker: str, accent: tuple[int, int, int],
                     rows: list[tuple[str, str]], highlight_label: str | None = None) -> Path:
    """Vẽ 1 ảnh 'card' thông tin tiếng Việt dùng chung cho style 'top'/'buy_signal'/
    'sell_signal' — header màu accent + badge/mã CK, thân card là danh sách (nhãn, giá
    trị). Dùng font DejaVu Sans bundle sẵn (scanner/assets/fonts) nên hiển thị đúng dấu
    trên mọi server."""
    W, H = 720, 1280
    bg      = (17, 20, 28)
    card_bg = (26, 30, 41)
    muted   = (150, 158, 176)
    divider = (45, 50, 64)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 220], fill=accent)
    draw.text((48, 36), badge, font=_font(34, bold=True), fill=(255, 255, 255))
    draw.text((48, 88), ticker, font=_font(110, bold=True), fill=(255, 255, 255))

    draw.rounded_rectangle([32, 256, W - 32, H - 48], radius=24, fill=card_bg)

    n = len(rows)
    row_h = (H - 48 - 300) // n
    y = 300
    label_font = _font(32)
    value_font = _font(50, bold=True)
    for idx, (label, value) in enumerate(rows):
        draw.text((64, y), label, font=label_font, fill=muted)
        color = accent if label == highlight_label else (255, 255, 255)
        draw.text((64, y + 42), value, font=value_font, fill=color)
        if idx < n - 1:
            draw.line([64, y + row_h - 24, W - 64, y + row_h - 24], fill=divider, width=2)
        y += row_h

    img.save(path, "JPEG", quality=92)
    return path


def _draw_stock_card(path: Path, rank: int, s: dict) -> Path:
    """Card style 'top': giá hiện tại, ngày mua, giá mua, số ngày giữ lệnh, lãi/lỗ,
    thanh khoản — header màu theo lãi/lỗ."""
    pnl = s.get("pnl")
    accent = (34, 197, 94) if (pnl is None or pnl >= 0) else (239, 68, 68)
    rows = [
        ("Giá hiện tại", fmt_price(s["price"])),
        ("Ngày mua",     s.get("buy_date") or "—"),
        ("Giá mua",      fmt_price(s["buy_price"]) if s.get("buy_price") else "—"),
        ("Giữ lệnh",     f"{s['hold_days']} ngày" if s.get("hold_days") is not None else "—"),
        ("Lãi / lỗ",     f"{pnl:+.1f}%" if pnl is not None else "—"),
        ("Thanh khoản",  f"{s['turnover_ty']:.1f} tỷ"),
    ]
    return _draw_info_card(path, f"TOP {rank}", s["ticker"], accent, rows, highlight_label="Lãi / lỗ")


def _draw_signal_card(path: Path, rank: int, s: dict) -> Path:
    """Card style 'buy_signal'/'sell_signal': giá tín hiệu, BiasNorm, hỗ trợ/kháng cự,
    thanh khoản, ngày — header màu xanh (MUA) / đỏ (BÁN) theo loại tín hiệu."""
    is_buy = s["signal_type"] == "MUA"
    accent = (34, 197, 94) if is_buy else (239, 68, 68)
    sr = (f"{fmt_price(s['support'])} / {fmt_price(s['resistance'])}"
          if s.get("support") and s.get("resistance") else "—")
    rows = [
        ("Giá tín hiệu",      fmt_price(s["price"])),
        ("BiasNorm",          f"{s['bias']:.0f}/100"),
        ("Hỗ trợ / Kháng cự", sr),
        ("Thanh khoản",       f"{s['turnover_ty']:.1f} tỷ"),
        ("Ngày tín hiệu",     s["date_str"]),
    ]
    return _draw_info_card(path, f"#{rank} · TÍN HIỆU {s['signal_type']}", s["ticker"], accent, rows)


def _stock_summary_line(i: int, s: dict) -> str:
    """Dòng số liệu thật (không qua AI) — dùng làm ngữ cảnh cho AI và làm fallback
    caption khi AI không trả về hoặc đổi sai mã CK."""
    buy_info  = f", mua {s['buy_date']} @ {fmt_price(s['buy_price'])}" if s.get("buy_date") and s.get("buy_price") else ""
    hold_info = f", giữ {s['hold_days']} ngày" if s.get("hold_days") is not None else ""
    pnl_info  = f", lãi/lỗ {s['pnl']:+.1f}%" if s.get("pnl") is not None else ""
    return (
        f"{i}. {s['ticker']} — giá {fmt_price(s['price'])}{buy_info}{hold_info}{pnl_info}, "
        f"thanh khoản {s['turnover_ty']:.1f} tỷ"
    )


def _build_top_stocks_post(out_dir: Path, n_stocks: int = 5) -> tuple[list[Path], dict]:
    """Style 'top': lấy top N mã vùng xanh (thanh khoản/BiasNorm) từ scan_results —
    không tốn tin tức, mỗi mã 1 ảnh card tiếng Việt đầy đủ số liệu + 1 caption riêng."""
    from scanner.database import load_scan_results
    df = load_scan_results()
    if df.empty:
        raise RuntimeError("Chưa có dữ liệu scan để tạo bài Top mã")

    trend_col = "short_trend" if "short_trend" in df.columns else "trend"
    p = "short_" if trend_col == "short_trend" else ""
    subset = df[df[trend_col] == 1] if trend_col in df.columns else df
    if subset.empty:
        raise RuntimeError("Không có mã nào trong vùng xanh để tạo bài Top mã")

    top = subset.nlargest(min(n_stocks, len(subset)), "turnover") if "turnover" in subset.columns \
          else subset.nlargest(min(n_stocks, len(subset)), "bias_norm")

    out_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    stock_lines: list[str] = []
    stocks: list[dict] = []
    today_date = date.today()

    for i, (_, row) in enumerate(top.iterrows(), 1):
        buy_date_raw = _val(row, f"{p}last_signal_date", "last_signal_date")
        buy_date     = _to_date(buy_date_raw)
        buy_price    = _val(row, f"{p}last_signal_price", "last_signal_price")
        pnl          = _val(row, f"{p}signal_pnl_pct", "signal_pnl_pct")

        stock = {
            "ticker":      str(row["ticker"]),
            "price":       float(_val(row, "close") or 0),
            "turnover_ty": float(_val(row, "turnover") or 0) / 1e9,
            "buy_date":    fmt_date(buy_date_raw) if buy_date_raw else None,
            "buy_price":   float(buy_price) if buy_price else None,
            "hold_days":   (today_date - buy_date).days if buy_date else None,
            "pnl":         float(pnl) if pnl is not None else None,
        }
        path = _draw_stock_card(out_dir / f"{i}.jpg", i, stock)
        image_paths.append(path)
        stocks.append(stock)
        stock_lines.append(_stock_summary_line(i, stock))

    today = today_date.strftime("%d/%m/%Y")
    n = len(stocks)
    img_spec = "\n".join(
        f"IMG{i}: [1 câu mô tả đúng mã #{i} bằng CHÍNH XÁC số liệu đã cho, không markdown]"
        for i in range(1, n + 1)
    )
    prompt = textwrap.dedent(f"""
        Bạn là biên tập nội dung TikTok viral về chứng khoán Việt Nam.
        Ngày: {today}

        DANH SÁCH {n} MÃ ĐANG MẠNH NHẤT VÙNG XANH HÔM NAY (chỉ dùng đúng số liệu này,
        không bịa thêm):
        {chr(10).join(stock_lines)}

        Viết nội dung video TikTok dạng bảng xếp hạng, theo đúng định dạng:

        HOOK: [1 câu mở đầu SIÊU giật gân về top mã hôm nay, dưới 15 từ, không markdown,
        không emoji]
        CAPTION: [đoạn giới thiệu bảng xếp hạng, 3-4 câu, kết thúc bằng 6-8 hashtag tiếng
        Việt viral về chứng khoán, không markdown]
        {img_spec}

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ
        - KHÔNG bịa thêm số liệu ngoài danh sách đã cho
        - Không mở đầu bằng "Dưới đây là"
    """).strip()

    hook, caption, image_captions = _call_ai_sections(prompt, n)
    # Bảo vệ: nếu AI đổi/thiếu mã CK trong caption riêng → thay bằng dòng số liệu thật
    image_captions = [
        cap if stocks[i]["ticker"] in cap else stock_lines[i]
        for i, cap in enumerate(image_captions)
    ]
    if not hook and not caption:
        hook = f"Top {n} mã vùng xanh mạnh nhất hôm nay"
        caption = " | ".join(stock_lines)

    return image_paths, {"hook": hook, "caption": caption, "image_captions": image_captions}


def _signal_summary_line(i: int, s: dict) -> str:
    """Dòng số liệu thật (không qua AI) — dùng làm ngữ cảnh cho AI và làm fallback
    caption khi AI không trả về hoặc đổi sai mã CK."""
    sr_info = (f", hỗ trợ {fmt_price(s['support'])} / kháng cự {fmt_price(s['resistance'])}"
               if s.get("support") and s.get("resistance") else "")
    return (
        f"{i}. {s['ticker']} — tín hiệu {s['signal_type']} @ {fmt_price(s['price'])}, "
        f"BiasNorm {s['bias']:.0f}/100{sr_info}, thanh khoản {s['turnover_ty']:.1f} tỷ"
    )


def _build_signal_post(out_dir: Path, signal_type: str, n_signals: int = 5) -> tuple[list[Path], dict]:
    """Style 'buy_signal'/'sell_signal': lấy tối đa N mã vừa phát sinh ĐÚNG 1 loại tín
    hiệu (MUA hoặc BÁN, không gộp) trong ngày hôm nay từ scan_results — không tốn tin
    tức, mỗi mã 1 ảnh card + 1 caption riêng."""
    from scanner.database import load_scan_results
    df = load_scan_results()
    if df.empty:
        raise RuntimeError("Chưa có dữ liệu scan để tạo bài Tín hiệu")

    date_col  = "short_last_signal_date"  if "short_last_signal_date"  in df.columns else "last_signal_date"
    type_col  = "short_last_signal_type"  if "short_last_signal_type"  in df.columns else "last_signal_type"
    price_col = "short_last_signal_price" if "short_last_signal_price" in df.columns else "last_signal_price"

    today_date = date.today()
    sig_dates  = df[date_col].apply(_to_date)
    mask = (sig_dates == today_date) & (df[type_col] == signal_type)
    todays = df[mask]
    if todays.empty:
        raise RuntimeError(f"Hôm nay chưa có tín hiệu {signal_type} nào phát sinh")

    top = todays.nlargest(min(n_signals, len(todays)), "turnover") if "turnover" in todays.columns \
          else todays.head(n_signals)

    out_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    stock_lines: list[str] = []
    stocks: list[dict] = []
    today_str = today_date.strftime("%d/%m/%Y")

    for i, (_, row) in enumerate(top.iterrows(), 1):
        support    = _val(row, "support")
        resistance = _val(row, "resistance")
        stock = {
            "ticker":      str(row["ticker"]),
            "signal_type": str(_val(row, type_col) or ""),
            "price":       float(_val(row, price_col, "close") or 0),
            "bias":        float(_val(row, "bias_norm") or 0),
            "turnover_ty": float(_val(row, "turnover") or 0) / 1e9,
            "support":     float(support) if support else None,
            "resistance":  float(resistance) if resistance else None,
            "date_str":    today_str,
        }
        path = _draw_signal_card(out_dir / f"{i}.jpg", i, stock)
        image_paths.append(path)
        stocks.append(stock)
        stock_lines.append(_signal_summary_line(i, stock))

    n = len(stocks)
    img_spec = "\n".join(
        f"IMG{i}: [1 câu mô tả đúng mã #{i} bằng CHÍNH XÁC số liệu đã cho, không markdown]"
        for i in range(1, n + 1)
    )
    action_word = "mua vào" if signal_type == "MUA" else "chốt lời/cắt lỗ"
    prompt = textwrap.dedent(f"""
        Bạn là biên tập nội dung TikTok viral về chứng khoán Việt Nam.
        Ngày: {today_str}

        DANH SÁCH {n} MÃ VỪA CÓ TÍN HIỆU {signal_type} HÔM NAY (chỉ dùng đúng số liệu này,
        không bịa thêm):
        {chr(10).join(stock_lines)}

        Viết nội dung video TikTok điểm tín hiệu {signal_type} trong ngày (nhà đầu tư nên
        cân nhắc {action_word} các mã này), theo đúng định dạng:

        HOOK: [1 câu mở đầu SIÊU giật gân về các tín hiệu {signal_type} hôm nay, dưới 15 từ,
        không markdown, không emoji]
        CAPTION: [đoạn giới thiệu các tín hiệu, 3-4 câu, kết thúc bằng 6-8 hashtag tiếng
        Việt viral về chứng khoán, không markdown]
        {img_spec}

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ
        - KHÔNG bịa thêm số liệu ngoài danh sách đã cho
        - Không mở đầu bằng "Dưới đây là"
    """).strip()

    hook, caption, image_captions = _call_ai_sections(prompt, n)
    image_captions = [
        cap if stocks[i]["ticker"] in cap else stock_lines[i]
        for i, cap in enumerate(image_captions)
    ]
    if not hook and not caption:
        hook = f"{n} mã vừa có tín hiệu {signal_type} hôm nay"
        caption = " | ".join(stock_lines)

    return image_paths, {"hook": hook, "caption": caption, "image_captions": image_captions}


def _build_chart_post(out_dir: Path) -> tuple[list[Path], dict]:
    """Style 'chart': chụp chart mùa vụ + dự báo (tái dùng tv_screenshot — giống /tv)
    cho mã có thanh khoản cao nhất vùng xanh hôm nay."""
    from scanner.database import load_scan_results
    from scanner.tv_screenshot import _scrape_all, _exchange, _gen_caption_pair

    df = load_scan_results()
    if df.empty:
        raise RuntimeError("Chưa có dữ liệu scan để tạo bài Chart")
    trend_col = "short_trend" if "short_trend" in df.columns else "trend"
    subset = df[df[trend_col] == 1] if trend_col in df.columns else df
    if subset.empty:
        raise RuntimeError("Không có mã nào trong vùng xanh để tạo bài Chart")
    row = (subset.nlargest(1, "turnover") if "turnover" in subset.columns else subset.nlargest(1, "bias_norm")).iloc[0]
    ticker = str(row["ticker"])

    results = _scrape_all([(ticker, _exchange(ticker))])
    imgs = {("seasonals" if "Mùa vụ" in lbl else "forecast"): img for _, lbl, img in results}
    img_s, img_f = imgs.get("seasonals"), imgs.get("forecast")
    if not (img_s and img_f):
        raise RuntimeError(f"Không chụp được đủ ảnh chart cho {ticker}")

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, img in enumerate((img_s, img_f), 1):
        path = out_dir / f"{i}.jpg"
        path.write_bytes(img)
        paths.append(path)

    caption = _gen_caption_pair(ticker, img_s, img_f)
    hook = f"{ticker} sắp bùng nổ hay đã hết game?"
    image_captions = [f"{ticker} — Mùa vụ theo tháng", f"{ticker} — Dự báo giá"]
    return paths, {"hook": hook, "caption": caption, "image_captions": image_captions}


def _build_recap_post(stories: list[dict], out_dir: Path) -> tuple[list[Path], dict]:
    """Style 'recap': gộp nhiều tin khác nhau vào 1 bài điểm tin nhanh — mỗi ảnh
    (1 ảnh/tin, không scrape thêm) ứng với 1 tin riêng. Tin nào tải ảnh thất bại
    thì bỏ khỏi bài (giữ image_captions khớp 1-1 với image_paths thực tế)."""
    image_paths = download_images(stories, out_dir, max_per_story=1)
    ok_stories = [s for s in stories if s.get("image_paths")]
    if not ok_stories:
        raise RuntimeError("Không tải được ảnh nào cho bài Recap")
    content = generate_recap_hook_caption(ok_stories)
    return image_paths, content


def create_tiktok_posts(
    n: int = 5,
    style: str | None = None,
    images_per_post: int = _IMAGES_PER_POST,
) -> list[Path]:
    """Tạo n bài đăng TikTok độc lập. Mỗi bài random 1 kiểu trong POST_STYLES (hoặc ép
    cứng toàn bộ n bài theo `style` nếu truyền vào), lưu vào reports/tiktok/<ngày>/post_<i>/.
    post_info.json của mỗi bài luôn có {style, hook, caption, image_captions, images}."""
    if style is not None and style not in POST_STYLES:
        raise ValueError(f"Style không hợp lệ: '{style}'. Chọn 1 trong: {', '.join(POST_STYLES)}")

    styles = [style] * n if style else [random.choice(list(POST_STYLES)) for _ in range(n)]

    # Gom sẵn 1 pool tin đủ dùng cho các style cần tin tức (single/carousel/recap)
    news_needed = sum(_NEWS_STYLES_COST.get(s, 0) for s in styles)
    topic_pool = gather_topic_pool(news_needed + 2) if news_needed else []
    pool_idx = 0

    today_str = date.today().strftime("%Y-%m-%d")
    base_dir = TIKTOK_OUTPUT_DIR / today_str
    suffix = 1
    while base_dir.exists():
        suffix += 1
        base_dir = TIKTOK_OUTPUT_DIR / f"{today_str}_{suffix}"

    out_dirs: list[Path] = []
    for i, post_style in enumerate(styles, 1):
        out_dir = base_dir / f"post_{i}"
        try:
            source_meta: dict | None = None
            if post_style == "single":
                story = topic_pool[pool_idx]; pool_idx += 1
                image_paths = download_images([story], out_dir, max_per_story=1)
                content = generate_single_hook_caption(story, n_images=len(image_paths) or 1)
                source_meta = {"category": story.get("category"), "title": story["title"],
                                "link": story["link"], "source": story["source"]}
            elif post_style == "carousel":
                story = topic_pool[pool_idx]; pool_idx += 1
                image_paths = download_images([story], out_dir, max_per_story=images_per_post)
                content = generate_single_hook_caption(story, n_images=len(image_paths) or 1)
                source_meta = {"category": story.get("category"), "title": story["title"],
                                "link": story["link"], "source": story["source"]}
            elif post_style == "recap":
                cost = _NEWS_STYLES_COST["recap"]
                stories = topic_pool[pool_idx: pool_idx + cost]; pool_idx += cost
                image_paths, content = _build_recap_post(stories, out_dir)
                source_meta = {"stories": [{"title": s["title"], "link": s["link"]} for s in stories]}
            elif post_style == "top":
                image_paths, content = _build_top_stocks_post(out_dir)
            elif post_style == "chart":
                image_paths, content = _build_chart_post(out_dir)
            elif post_style == "buy_signal":
                image_paths, content = _build_signal_post(out_dir, signal_type="MUA")
            elif post_style == "sell_signal":
                image_paths, content = _build_signal_post(out_dir, signal_type="BÁN")
            else:
                raise ValueError(f"Style không hợp lệ: {post_style}")
        except Exception as e:
            logger.warning(f"Bài {i} style='{post_style}' thất bại, bỏ qua: {e}")
            continue

        (out_dir / "hook.txt").write_text(content["hook"], encoding="utf-8")
        (out_dir / "caption.txt").write_text(content["caption"], encoding="utf-8")
        for idx, cap in enumerate(content["image_captions"], 1):
            (out_dir / f"cap_{idx}.txt").write_text(cap, encoding="utf-8")

        meta = {
            "date": today_str,
            "style": post_style,
            "style_label": POST_STYLES[post_style],
            "hook": content["hook"],
            "caption": content["caption"],
            "image_captions": content["image_captions"],
            "images": [str(p) for p in image_paths],
            "source": source_meta,
        }
        (out_dir / "post_info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        out_dirs.append(out_dir)
        logger.info(f"Tạo bài TikTok {i}/{n} xong [{post_style}]: {out_dir} ({len(image_paths)} ảnh)")

    if not out_dirs:
        raise RuntimeError("Không tạo được bài đăng TikTok nào")

    return out_dirs


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tạo nội dung bài đăng TikTok từ tin tức")
    parser.add_argument("--n", type=int, default=5, help="Số bài đăng độc lập cần tạo (mặc định 5)")
    parser.add_argument("--style", choices=list(POST_STYLES), default=None,
                         help="Ép cứng 1 style cho toàn bộ batch (mặc định: random mỗi bài)")
    parser.add_argument("--images", type=int, default=_IMAGES_PER_POST, help="Số ảnh tối đa mỗi bài carousel (mặc định 3)")
    args = parser.parse_args()

    result_dirs = create_tiktok_posts(n=args.n, style=args.style, images_per_post=args.images)
    for i, out_dir in enumerate(result_dirs, 1):
        style_used = json.loads((out_dir / "post_info.json").read_text(encoding="utf-8"))["style_label"]
        print(f"\n=== Bài {i}/{len(result_dirs)} [{style_used}]: {out_dir} ===")
        print("--- HOOK ---")
        print((out_dir / "hook.txt").read_text(encoding="utf-8"))
        print("--- CAPTION ---")
        print((out_dir / "caption.txt").read_text(encoding="utf-8"))
        for cap_file in sorted(out_dir.glob("cap_*.txt"), key=lambda p: int(p.stem.split("_")[1])):
            print(f"--- {cap_file.stem.upper()} ---")
            print(cap_file.read_text(encoding="utf-8"))
