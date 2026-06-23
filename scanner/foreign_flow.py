"""
Vietstock market report — chụp 6 tab + phân tích AI, gửi Telegram.
Chạy 15:30 ICT mỗi ngày giao dịch.

Usage:
  python -m scanner.foreign_flow [--force]
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta

from scanner.utils import logger

ICT           = timezone(timedelta(hours=7))
VIETSTOCK_URL = "https://finance.vietstock.vn/"
_NAV_HEIGHT   = 115   # px — cắt nav khi fallback viewport


# ─── Style suffix dùng chung cho mọi section ─────────────────────────────────

STYLE_SUFFIX = (
    "Chỉ viết đúng 3-4 câu liên tiếp, không chia thành nhiều đoạn, không viết thêm dù còn ý. "
    "Văn phong tự nhiên như người viết bản tin chứng khoán, không liệt kê máy móc, "
    "không lặp cấu trúc kiểu 'có thể thấy... cho thấy... đáng chú ý là...'. "
    "Không dùng gạch đầu dòng, không in đậm. "
    "Đảm bảo câu kết luận khớp với toàn bộ số liệu đã nêu ở trên, không mâu thuẫn. "
    "Ví dụ giọng văn mong muốn: 'Khối ngoại tiếp tục bán ròng trên 3 sàn, tập trung mạnh vào "
    "nhóm trụ như FPT (-501 tỷ), VHM (-182 tỷ), TCB (-159 tỷ) – đây là các mã vốn hóa lớn nên "
    "áp lực bán có thể ảnh hưởng trực tiếp đến diễn biến VN-Index. Ở chiều ngược lại, dòng tiền "
    "ngoại vẫn mua ròng chọn lọc ở VIC (+74 tỷ), PVS (+34 tỷ), cho thấy động thái này nghiêng về "
    "cơ cấu danh mục hơn là rút vốn đồng loạt.'"
)


# ─── Section dataclass ────────────────────────────────────────────────────────

@dataclass
class Section:
    tab:           str   # text tab để click
    icon:          str   # emoji cho header
    selector:      str   # CSS selector(s), phân cách bằng ","
    layout:        str   # mô tả cấu trúc ảnh
    focus:         str   # nội dung cần phân tích
    extra_wait_ms:   int = 0   # chờ thêm sau networkidle nếu cần
    ready_selector: str = ""  # chờ element này visible trước khi chụp


# ─── 6 Sections ──────────────────────────────────────────────────────────────

SECTIONS = [

    Section(
        "Bản đồ thị trường", "🗺️", "#heatmap-wrapper-right",
        ready_selector="#heatmap-container",
        layout=(
            "Ảnh là bản đồ nhiệt (heatmap) toàn thị trường, chia theo nhóm ngành (Tài chính, "
            "Bất động sản, Nguyên vật liệu, Công nghệ thông tin, Tiêu dùng, Công nghiệp, Năng "
            "lượng...). Diện tích mỗi ô tỷ lệ theo vốn hóa, màu xanh = tăng giá, màu đỏ = giảm giá. "
            "Lưu ý: trong mỗi ngành thường có 1-2 mã vốn hóa lớn nhất chiếm ô to nhất, nhưng các "
            "mã này có thể tăng/giảm ngược hướng với phần lớn các mã nhỏ hơn còn lại trong cùng "
            "ngành — không lấy riêng 1-2 ô lớn nhất để đại diện cho cả ngành. Hãy đếm tỷ lệ ô "
            "đỏ/xanh trên toàn bộ bản đồ (không chỉ các ô lớn) để xác định sắc thái chủ đạo thực "
            "sự, rồi mới chọn ra ngành/mã nổi bật để liệt kê chi tiết."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: sắc thái chủ đạo của toàn bản đồ (đỏ hay xanh "
            "áp đảo về số lượng ô), độ phân hóa trong từng ngành lớn (nêu rõ nếu mã đầu ngành tăng "
            "nhưng các mã khác trong ngành lại giảm, hoặc ngược lại), và ngành nào đồng thuận "
            "giảm/tăng rõ nhất. Nêu cụ thể tên ngành, tên mã và tỷ lệ %."
        ),
    ),

    Section(
        "Tổng hợp thị trường", "📊", "#general-markets-container",
        layout=(
            "Ảnh gồm 2 phần. Trái là biểu đồ đường VN-Index trong phiên giao dịch hiện tại (theo "
            "khung giờ trong ngày, không phải nhiều ngày), có đường tham chiếu đứt nét màu vàng là "
            "mức tham chiếu. Phải là bảng so sánh nhiều chỉ số (VN-Index, HNX, UPCOM, VN30, HNX30, "
            "VNMidcap, VNSmallcap...), mỗi chỉ số có % thay đổi theo nhiều mốc khác nhau: D (so với "
            "phiên trước), W (so với 1 tuần trước), M (1 tháng), Q (1 quý), YTD (từ đầu năm) — đây "
            "là các mốc so sánh của CÙNG một thời điểm hiện tại, không phải diễn biến nhiều ngày "
            "liên tiếp."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: diễn biến VN-Index trong phiên hôm nay "
            "(tăng/giảm bao nhiêu điểm và %, so với mức tham chiếu), so sánh xu hướng ngắn hạn (D, "
            "W) với xu hướng dài hơn (M, Q, YTD) để thấy thị trường đang đảo chiều hay tiếp diễn, "
            "và điểm đáng chú ý nhất giữa các chỉ số (ví dụ chỉ số nào phân kỳ mạnh so với "
            "VN-Index). Nêu cụ thể con số điểm và %."
        ),
    ),

    Section(
        "Thanh khoản", "💧", "#liquidity-container",
        layout=(
            "Ảnh gồm 2 phần. Trái là biểu đồ vùng (area chart) thể hiện giá trị giao dịch tích lũy "
            "(GTGD) của VN-Index theo khung giờ trong phiên hôm nay (màu xanh đậm) so với cùng giờ "
            "phiên hôm qua (màu xám), kèm % thay đổi tổng thanh khoản so với hôm qua. Phải là bảng "
            "Top 10 mã có giá trị giao dịch (GTGD) lớn nhất trong phiên, gồm giá, % thay đổi giá, "
            "khối lượng giao dịch (KLGD) và GTGD tính bằng tỷ đồng cùng % đóng góp vào tổng GTGD."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: tổng giá trị giao dịch hôm nay là bao nhiêu và "
            "tăng/giảm bao nhiêu % so với hôm qua, mã nào có thanh khoản (GTGD) cao nhất và chiếm "
            "bao nhiêu % tổng GTGD, nhận xét dòng tiền đang tập trung vào nhóm ngành nào dựa trên "
            "các mã đứng đầu danh sách. Nêu cụ thể số liệu tỷ đồng, % và tên mã."
        ),
    ),

    Section(
        "Ảnh hưởng Index", "📈", ".top-influence-box",
        layout=(
            "Ảnh gồm 3 biểu đồ riêng biệt cho 3 chỉ số: VN-Index, VN30-Index, HNX-Index (mỗi chỉ "
            "số có mức điểm và % thay đổi riêng). Mỗi biểu đồ liệt kê các mã kéo chỉ số đó tăng "
            "(thanh xanh, phía trên) và các mã kéo chỉ số đó giảm (thanh đỏ, phía dưới), đơn vị là "
            "điểm ảnh hưởng (không phải %). Dưới mỗi biểu đồ có 2 số tổng: tổng điểm kéo tăng "
            "(xanh) và tổng điểm kéo giảm (đỏ) của toàn bộ thị trường thuộc chỉ số đó — so sánh 2 "
            "số này cho biết lực kéo giảm hay tăng đang chiếm ưu thế. Ba chỉ số có danh sách mã ảnh "
            "hưởng khác nhau, không dùng chung số liệu."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt, tập trung chủ yếu vào VN-Index: mã nào kéo "
            "VN-Index giảm/tăng nhiều điểm nhất, so sánh tổng điểm kéo tăng và tổng điểm kéo giảm "
            "để thấy lực nào đang chiếm ưu thế, và một so sánh ngắn với VN30 hoặc HNX nếu có sự "
            "khác biệt đáng chú ý (ví dụ mã ảnh hưởng khác nhau giữa 2 chỉ số). Nêu cụ thể tên mã "
            "và điểm ảnh hưởng."
        ),
    ),

    Section(
        "Nước ngoài", "🌍", ".foreign-row",
        ready_selector="#toast-foreign-section",
        layout=(
            "Ảnh gồm 2 phần. Trái là biểu đồ giá trị mua (xanh)/bán (đỏ) của NĐTNN trên 3 sàn theo "
            "ngày, đường vàng là giá trị mua ròng (mua trừ bán) — đường này âm khi bán ròng, dương "
            "khi mua ròng. Phải là bảng top 10 mã có giá trị giao dịch ròng lớn nhất riêng trong "
            "ngày gần nhất: cột trái (thanh đỏ) là top bán ròng, cột phải (thanh xanh) là top mua "
            "ròng — đây là 2 danh sách độc lập, mã ở cùng hàng bên trái và bên phải KHÔNG liên quan "
            "đến nhau."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: khối ngoại mua ròng hay bán ròng trong ngày "
            "gần nhất (dựa vào đường giá trị mua ròng), mã nào bị bán ròng mạnh nhất và mã nào "
            "được mua ròng mạnh nhất, xu hướng vài phiên gần đây đang nghiêng về mua hay bán. Nêu "
            "cụ thể số liệu tỷ đồng và tên mã, không gộp chung 2 danh sách bán ròng/mua ròng thành "
            "1 cặp đối ứng."
        ),
    ),

    Section(
        "Tự doanh", "🏦", ".proprietary-row",
        ready_selector="#toast-proprietary-section",
        layout=(
            "Ảnh gồm 2 phần, cấu trúc tương tự khối ngoại. Trái là biểu đồ giá trị mua (xanh)/bán "
            "(đỏ) của khối tự doanh công ty chứng khoán trên 3 sàn theo ngày, đường vàng là giá trị "
            "mua ròng. Phải là bảng top 10 mã giao dịch ròng lớn nhất trong ngày gần nhất: cột trái "
            "(thanh đỏ) là top bán ròng, cột phải (thanh xanh) là top mua ròng — 2 danh sách độc "
            "lập, mã cùng hàng trái/phải KHÔNG liên quan đến nhau."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: tự doanh mua ròng hay bán ròng trong ngày gần "
            "nhất, mã nào bị bán ròng mạnh nhất và mã nào được mua ròng mạnh nhất, nhận xét xu "
            "hướng so với vài phiên trước. Nêu cụ thể số liệu tỷ đồng và tên mã, không gộp chung 2 "
            "danh sách thành 1 cặp đối ứng."
        ),
    ),

]

_TIMEOUT_MS = 12_000


def build_prompt(section: Section) -> str:
    return f"{section.layout} {section.focus} {STYLE_SUFFIX}"


# ─── Entry point ─────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    from scanner.utils import is_trading_day
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua")
        return

    logger.info("=== Vietstock Market Report ===")
    try:
        results = _scrape_all()
    except Exception as e:
        logger.error(f"Scrape thất bại: {e}")
        _send_both(f"⚠️ <b>Market Report</b>: không scrape được\n<code>{e}</code>")
        return

    from scanner.telegram_bot import send_photo
    for section, img in results:
        caption = _gen_caption(section, img)
        send_photo(img, caption, style="long")
        send_photo(img, caption, style="short")
        logger.info(f"  Sent: {section.tab}")
        time.sleep(1)   # tránh flood Telegram

    logger.info(f"=== Xong {len(results)}/{len(SECTIONS)} sections ===")


# ─── Scraping ────────────────────────────────────────────────────────────────

def _scrape_all(sections: list[Section] | None = None) -> list[tuple[Section, bytes]]:
    from playwright.sync_api import sync_playwright

    sections = sections or SECTIONS
    results: list[tuple[Section, bytes]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        page = browser.new_context(
            viewport={"width": 1440, "height": 860},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
        ).new_page()

        page.goto(VIETSTOCK_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_000)

        for section in sections:
            try:
                img = _scrape_section(page, section)
                results.append((section, img))
                logger.info(f"[OK] {section.tab}: {len(img)//1024} KB")
            except Exception as e:
                logger.warning(f"[SKIP] {section.tab}: {e}")

        browser.close()

    return results


def _dismiss_ads(page) -> None:
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        btn = page.locator(".ats-overlay-bottom-close-button").first
        if btn.is_visible(timeout=500):
            btn.click()
            page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        page.evaluate("""() => {
            document.querySelectorAll(
                'iframe, [class*="adsby"], [id*="google_ads"], '
                '[class*="ad-overlay"], [class*="popup"], .modal-backdrop'
            ).forEach(el => { el.style.display = 'none'; });
        }""")
    except Exception:
        pass


def _scrape_section(page, section: Section) -> bytes:
    for sel in [f"a:has-text('{section.tab}')", f"text={section.tab}"]:
        try:
            page.locator(sel).first.click(timeout=3_000)
            break
        except Exception:
            pass

    for sel in (s.strip() for s in section.selector.split(",")):
        try:
            page.wait_for_selector(sel, state="visible", timeout=_TIMEOUT_MS)
            el = page.locator(sel).first
            el.scroll_into_view_if_needed()
            if section.ready_selector:
                try:
                    page.wait_for_selector(section.ready_selector, state="visible", timeout=12_000)
                except Exception:
                    page.wait_for_timeout(3_000)
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    page.wait_for_timeout(3_000)
            if section.extra_wait_ms:
                page.wait_for_timeout(section.extra_wait_ms)
            _dismiss_ads(page)
            return el.screenshot()
        except Exception:
            pass

    logger.debug(f"[{section.tab}] fallback viewport clip")
    page.wait_for_timeout(2_500)
    return page.screenshot(clip={
        "x": 0, "y": _NAV_HEIGHT, "width": 1440, "height": 860 - _NAV_HEIGHT
    })


# ─── Caption (vision AI) ─────────────────────────────────────────────────────

def _gen_caption(section: Section, img_bytes: bytes) -> str:
    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"{section.icon} <b>{section.tab} — {today}</b>"

    try:
        from groq import Groq
        from scanner.config import GROQ_API_KEY
        b64 = base64.b64encode(img_bytes).decode()
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {
                        "type": "text",
                        "text": build_prompt(section),
                    },
                ],
            }],
            max_tokens=300,
            temperature=0.4,
        )
        body    = resp.choices[0].message.content.strip()
        caption = f"{header}\n\n{body}"
        if len(caption) > 1024:
            caption = caption[:1021] + "..."
        return caption
    except Exception as e:
        logger.warning(f"Vision caption [{section.tab}]: {e}")
        return header


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _send_both(msg: str) -> None:
    try:
        from scanner.telegram_bot import send_message
        send_message(msg, style="long")
        send_message(msg, style="short")
    except Exception as e:
        logger.error(f"_send_both: {e}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    run(force="--force" in sys.argv)
