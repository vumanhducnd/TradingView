"""
Foreign flow daily report — chụp ảnh Vietstock, viết caption AI, gửi Telegram.
Chạy sau 22:00 ICT mỗi ngày giao dịch.

Usage:
  python -m scanner.foreign_flow
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

from scanner.utils import logger

ICT = timezone(timedelta(hours=7))

# Sections cần chụp (tên + selector để thử)
_SECTIONS = [
    ("ndtnn",   "#tab-ndtnn,#ndtnn-container,.ndtnn-wrapper,[id*='foreign'],[class*='ndtnn']"),
    ("top10",   "#top-10-container,.top10-wrapper,[id*='top10'],[id*='top-10']"),
]

_FALLBACK_SELECTOR = "body"   # nếu không tìm thấy gì thì chụp viewport

VIETSTOCK_URL = "https://finance.vietstock.vn/"


# ─── Public entry point ───────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    """Scrape → caption → gửi Telegram cả 2 bot."""
    from scanner.utils import is_trading_day
    from datetime import date
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua foreign flow")
        return

    logger.info("=== Foreign Flow Report ===")
    try:
        screenshots, rows = _scrape()
    except Exception as e:
        logger.error(f"Scrape Vietstock thất bại: {e}")
        _send_text(f"⚠️ <b>Foreign flow</b>: không scrape được Vietstock\n<code>{e}</code>")
        return

    caption = _gen_caption(rows)

    from scanner.telegram_bot import send_photo
    # Gửi từng ảnh (mỗi section 1 ảnh), caption chỉ gắn vào ảnh cuối
    for i, (name, img_bytes) in enumerate(screenshots):
        cap = caption if i == len(screenshots) - 1 else ""
        send_photo(img_bytes, cap, style="long")
        send_photo(img_bytes, cap, style="short")
        if i < len(screenshots) - 1:
            time.sleep(0.5)

    logger.info("Foreign flow report đã gửi")


# ─── Scraping ────────────────────────────────────────────────────────────────

def _scrape() -> tuple[list[tuple[str, bytes]], list[dict]]:
    """
    Trả về:
      screenshots: [(section_name, png_bytes), ...]
      rows: dữ liệu bảng top mua/bán ròng
    """
    from playwright.sync_api import sync_playwright

    screenshots: list[tuple[str, bytes]] = []
    rows: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
        )
        page = ctx.new_page()

        logger.info(f"Navigating to {VIETSTOCK_URL} ...")
        page.goto(VIETSTOCK_URL, wait_until="domcontentloaded", timeout=60_000)

        # Chờ JS render charts (canvas/SVG thường mất 3-5s)
        page.wait_for_timeout(5_000)

        # Thử click tab "Đầu tư nước ngoài" nếu có
        _try_click_foreign_tab(page)

        page.wait_for_timeout(2_000)

        # Chụp từng section
        for name, sel_list in _SECTIONS:
            img = _screenshot_first(page, sel_list)
            if img:
                screenshots.append((name, img))
                logger.info(f"  [{name}] screenshot OK ({len(img)//1024} KB)")
            else:
                logger.warning(f"  [{name}] không tìm thấy element, bỏ qua")

        # Nếu không chụp được gì → chụp viewport
        if not screenshots:
            logger.warning("Không tìm thấy section, chụp viewport")
            img = page.screenshot(full_page=False)
            screenshots.append(("viewport", img))

        # Trích dữ liệu bảng top mua/bán ròng
        rows = _extract_rows(page)
        logger.info(f"  Trích được {len(rows)} dòng từ bảng")

        browser.close()

    return screenshots, rows


def _try_click_foreign_tab(page) -> None:
    """Click tab 'Đầu tư nước ngoài' hoặc 'NĐTNN' nếu tồn tại."""
    for text in ["Đầu tư nước ngoài", "NĐTNN", "Nước ngoài"]:
        try:
            el = page.get_by_text(text, exact=False).first
            if el and el.is_visible():
                el.click()
                logger.info(f"  Clicked tab: '{text}'")
                return
        except Exception:
            pass


def _screenshot_first(page, selectors_csv: str) -> bytes | None:
    """Thử từng selector trong chuỗi CSV, trả về screenshot của element đầu tiên tìm thấy."""
    for sel in selectors_csv.split(","):
        sel = sel.strip()
        if not sel:
            continue
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                # Scroll element vào view
                el.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                return el.screenshot()
        except Exception:
            pass
    return None


def _extract_rows(page) -> list[dict]:
    """Trích dữ liệu bảng top mua/bán ròng từ DOM."""
    rows: list[dict] = []
    try:
        # Tìm tất cả table rows có ticker + giá trị
        trs = page.query_selector_all("table tbody tr, .table-body tr, [class*='row']")
        for tr in trs[:30]:
            cells = tr.query_selector_all("td, [class*='cell']")
            if len(cells) < 2:
                continue
            ticker = cells[0].inner_text().strip().upper()
            value  = cells[-1].inner_text().strip()
            # Lọc: ticker hợp lệ (2-4 chữ in hoa), value có số
            if 2 <= len(ticker) <= 4 and ticker.isalpha() and any(c.isdigit() for c in value):
                rows.append({"ticker": ticker, "value": value})
    except Exception as e:
        logger.debug(f"_extract_rows: {e}")
    return rows


# ─── Caption generation ───────────────────────────────────────────────────────

def _gen_caption(rows: list[dict]) -> str:
    """Groq → viết caption phân tích dòng vốn nước ngoài."""
    today = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"🌍 <b>Dòng vốn nước ngoài — {today}</b>"

    if not rows:
        return header

    # Phân loại mua/bán từ dấu giá trị
    buy_rows  = [r for r in rows if "-" not in r["value"]]
    sell_rows = [r for r in rows if "-"     in r["value"]]

    data_lines = ["Top BÁN RÒNG:"] + [f"  {r['ticker']}: {r['value']} tỷ" for r in sell_rows[:6]]
    data_lines += ["Top MUA RÒNG:"] + [f"  {r['ticker']}: {r['value']} tỷ" for r in buy_rows[:6]]
    data_text = "\n".join(data_lines)

    prompt = (
        f"Dữ liệu giao dịch ròng nhà đầu tư nước ngoài ngày {today} (tỷ đồng):\n\n"
        f"{data_text}\n\n"
        "Viết đúng 3-4 câu phân tích bằng tiếng Việt tự nhiên (không dùng gạch đầu dòng, không in đậm):\n"
        "1. Tổng quan xu hướng mua hay bán ròng hôm nay\n"
        "2. Nêu cụ thể 2-3 mã bị tác động lớn nhất và ảnh hưởng đến VN-Index\n"
        "3. Nhận định ngắn về dòng tiền nội phản ứng ra sao"
    )

    try:
        from groq import Groq
        from scanner.config import GROQ_API_KEY
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            temperature=0.45,
        )
        ai_text = resp.choices[0].message.content.strip()
        return f"{header}\n\n{ai_text}"
    except Exception as e:
        logger.warning(f"Groq caption failed: {e}")
        return header


# ─── Helper ──────────────────────────────────────────────────────────────────

def _send_text(msg: str) -> None:
    """Gửi text fallback khi scrape thất bại."""
    try:
        from scanner.telegram_bot import send_message
        send_message(msg, style="long")
        send_message(msg, style="short")
    except Exception as e:
        logger.error(f"send_text failed: {e}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    run(force=force)
