"""
Foreign flow daily report — chụp ảnh Vietstock, viết caption AI, gửi Telegram.
Chạy sau 22:00 ICT mỗi ngày giao dịch.

Usage:
  python -m scanner.foreign_flow [--force]
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from scanner.utils import logger

ICT = timezone(timedelta(hours=7))
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
        screenshot, sell_rows, buy_rows = _scrape()
    except Exception as e:
        logger.error(f"Scrape Vietstock thất bại: {e}")
        _send_text(f"⚠️ <b>Foreign flow</b>: không scrape được Vietstock\n<code>{e}</code>")
        return

    caption = _gen_caption(sell_rows, buy_rows)
    logger.info(f"Caption: {caption[:80]}...")

    from scanner.telegram_bot import send_photo
    send_photo(screenshot, caption, style="long")
    send_photo(screenshot, caption, style="short")
    logger.info("Foreign flow report đã gửi")


# ─── Scraping ────────────────────────────────────────────────────────────────

def _scrape() -> tuple[bytes, list[dict], list[dict]]:
    """
    Trả về (screenshot_bytes, sell_rows, buy_rows).
    sell_rows/buy_rows: [{"ticker": "VHM", "value": 817.77}, ...]
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 860},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
        )
        page = ctx.new_page()

        logger.info("Mở Vietstock...")
        page.goto(VIETSTOCK_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3_000)

        # Click tab "Nước ngoài" trong sub-navigation
        _click_foreign_tab(page)
        page.wait_for_timeout(4_000)   # chờ chart + bảng render

        # Chụp phần content (bỏ top nav)
        screenshot = _screenshot_content(page)

        # Extract dữ liệu bảng
        sell_rows, buy_rows = _extract_table_data(page)
        logger.info(f"Bán ròng: {len(sell_rows)} mã | Mua ròng: {len(buy_rows)} mã")

        browser.close()

    return screenshot, sell_rows, buy_rows


def _click_foreign_tab(page) -> None:
    """Click tab 'Nước ngoài' trong sub-nav của Vietstock."""
    # Thử các selector khác nhau
    candidates = [
        "text=Nước ngoài",
        "a:has-text('Nước ngoài')",
        "li:has-text('Nước ngoài') a",
        "[href*='nuoc-ngoai']",
        "[href*='foreign']",
        "a[href*='ndtnn']",
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2_000):
                el.click()
                logger.info(f"Clicked tab: {sel}")
                return
        except Exception:
            pass
    logger.warning("Không tìm thấy tab 'Nước ngoài' — dùng trang hiện tại")


def _screenshot_content(page) -> bytes:
    """Chụp container nước ngoài: .foreign-row.chart-box-surround"""
    container_sels = [
        ".foreign-row.chart-box-surround",
        ".foreign-row",
        ".chart-box-surround.pos-relative",
    ]
    for sel in container_sels:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=3_000):
                el.scroll_into_view_if_needed()
                page.wait_for_timeout(800)
                img = el.screenshot()
                logger.info(f"Screenshot: {sel} ({len(img)//1024} KB)")
                return img
        except Exception:
            pass

    logger.warning("Không tìm được .foreign-row, crop viewport")
    full = page.screenshot(full_page=False)
    return _crop_top(full, crop_px=80)


def _crop_top(png_bytes: bytes, crop_px: int = 80) -> bytes:
    """Crop bỏ crop_px pixel từ trên xuống (loại nav bar)."""
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(png_bytes))
        w, h = img.size
        cropped = img.crop((0, crop_px, w, h))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        logger.debug("Pillow chưa cài — bỏ qua crop")
        return png_bytes


def _extract_table_data(page) -> tuple[list[dict], list[dict]]:
    """
    Trích top bán ròng và mua ròng từ DOM Vietstock.
    Trả về (sell_rows, buy_rows) dạng [{"ticker": "VHM", "value": 817.77}]
    """
    sell_rows: list[dict] = []
    buy_rows:  list[dict] = []

    try:
        data = page.evaluate("""
        () => {
            const result = { sell: [], buy: [] };

            // Tìm trong container .foreign-row trước
            const root = document.querySelector('.foreign-row') || document;

            // SVG text: Vietstock dùng Highcharts/D3 — text elements chứa mã + số
            const svgTexts = Array.from(root.querySelectorAll('svg text'));
            let i = 0;
            while (i < svgTexts.length) {
                const t = svgTexts[i].textContent.trim();
                if (/^[A-Z]{2,4}$/.test(t)) {
                    // Tìm số gần nhất (trước hoặc sau)
                    const prev = svgTexts[i - 1] ? parseFloat(svgTexts[i - 1].textContent.replace(/,/g, '')) : NaN;
                    const next = svgTexts[i + 1] ? parseFloat(svgTexts[i + 1].textContent.replace(/,/g, '')) : NaN;
                    const val = !isNaN(prev) ? prev : (!isNaN(next) ? next : NaN);
                    if (!isNaN(val)) {
                        result.sell.push({ ticker: t, value: Math.abs(val) });
                    }
                }
                i++;
            }

            // Table rows fallback
            if (result.sell.length === 0) {
                root.querySelectorAll('table tr').forEach(row => {
                    const cells = Array.from(row.querySelectorAll('td'));
                    if (cells.length >= 2) {
                        const t = cells[0].innerText.trim();
                        const v = parseFloat(cells[cells.length - 1].innerText.replace(/,/g, ''));
                        if (/^[A-Z]{2,4}$/.test(t) && !isNaN(v))
                            result.sell.push({ ticker: t, value: Math.abs(v) });
                    }
                });
            }

            // Tách sell/buy: Vietstock xếp bán trái, mua phải
            // Nếu có 2 SVG riêng → phân biệt qua vị trí x
            const svgs = root.querySelectorAll('svg');
            if (svgs.length >= 2 && result.sell.length > 0) {
                // Đã merge cả 2 — dùng mid split
                const mid = Math.ceil(result.sell.length / 2);
                result.buy  = result.sell.slice(mid);
                result.sell = result.sell.slice(0, mid);
            }

            return result;
        }
        """)

        sell_rows = data.get("sell", [])
        buy_rows  = data.get("buy",  [])

    except Exception as e:
        logger.debug(f"_extract_table_data JS: {e}")

    return sell_rows, buy_rows


# ─── Caption generation ───────────────────────────────────────────────────────

def _gen_caption(sell_rows: list[dict], buy_rows: list[dict]) -> str:
    """Groq → viết caption phân tích dòng vốn nước ngoài."""
    today = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"🌍 <b>Dòng vốn nước ngoài — {today}</b>"

    if not sell_rows and not buy_rows:
        return header

    sell_text = ", ".join(
        f"{r['ticker']} (-{r['value']:.0f} tỷ)" for r in sell_rows[:6]
    )
    buy_text = ", ".join(
        f"{r['ticker']} (+{r['value']:.0f} tỷ)" for r in buy_rows[:6]
    )

    prompt = (
        f"Dữ liệu giao dịch ròng nhà đầu tư nước ngoài ngày {today}:\n\n"
        f"Top BÁN RÒNG: {sell_text or '(không có)'}\n"
        f"Top MUA RÒNG: {buy_text or '(không có)'}\n\n"
        "Viết đúng 3-4 câu phân tích bằng tiếng Việt tự nhiên, không dùng gạch đầu dòng, "
        "không in đậm tên chỉ báo. Nêu cụ thể mã và số tỷ đồng, nhận xét ảnh hưởng đến "
        "VN-Index và dòng tiền nội."
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
        lines = [header]
        if sell_text:
            lines.append(f"📉 Bán ròng: {sell_text}")
        if buy_text:
            lines.append(f"📈 Mua ròng: {buy_text}")
        return "\n".join(lines)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _send_text(msg: str) -> None:
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
