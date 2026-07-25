"""
Vietstock market report — chụp 6 tab + phân tích AI, gửi Telegram.
Chạy 15:30 ICT mỗi ngày giao dịch.

Usage:
  python -m scanner.foreign_flow [--force]
"""

from __future__ import annotations

import base64
import re
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
    "Tuyệt đối không dùng ngôn ngữ mô tả màu sắc bản đồ như 'ô đỏ', 'ô xanh', 'ô màu', "
    "'số lượng ô' — thay bằng 'phần lớn cổ phiếu', 'đa số mã', 'nhóm ngành', 'dòng tiền'. "
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
    fullscreen:     bool = False  # click toolbar-fullscreen trước khi chụp, restore sau


# ─── 6 Sections ──────────────────────────────────────────────────────────────

SECTIONS = [

    Section(
        "Bản đồ thị trường", "🗺️", "#heatmap-container,#heatmap-wrapper-right",
        ready_selector="#heatmap-container",
        fullscreen=True,
        layout=(
            "Ảnh là bản đồ nhiệt (heatmap) toàn thị trường, chia theo nhóm ngành (Tài chính, "
            "Bất động sản, Nguyên vật liệu, Công nghệ thông tin, Tiêu dùng, Công nghiệp, Năng "
            "lượng...). Diện tích mỗi mã tỷ lệ theo vốn hóa, màu xanh = tăng giá, màu đỏ = giảm giá. "
            "Lưu ý: trong mỗi ngành thường có 1-2 mã vốn hóa lớn chiếm phần lớn diện tích, nhưng "
            "các mã này có thể tăng/giảm ngược hướng với phần lớn các mã nhỏ hơn trong cùng ngành — "
            "không dùng 1-2 mã đầu ngành để đại diện cho cả ngành. Hãy quan sát tỷ lệ mã tăng/giảm "
            "trên toàn bộ bản đồ để xác định sắc thái thị trường, rồi chọn ngành/mã nổi bật nhất."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt theo giọng bản tin chứng khoán tự nhiên: "
            "thị trường hôm nay nghiêng về bên nào (phần lớn cổ phiếu tăng hay giảm), "
            "ngành nào có sự phân hóa rõ (trụ ngành tăng nhưng phần lớn mã còn lại giảm, hoặc ngược lại), "
            "và ngành nào giảm/tăng đồng thuận nhất. "
            "Tuyệt đối không dùng cụm từ 'ô đỏ', 'ô xanh', 'ô màu', 'số lượng ô' — "
            "thay bằng 'phần lớn cổ phiếu', 'đa số mã', 'nhóm ngành', 'dòng tiền'. "
            "Nêu cụ thể tên ngành, tên mã và % thay đổi."
        ),
    ),

    Section(
        "Tổng hợp thị trường", "📊", "#general-markets",
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
        "Nước ngoài", "🌍", "#foreign-container",
        ready_selector="#foreign-bar-chart-buy",
        layout=(
            "Ảnh gồm 2 phần. Trái là biểu đồ giá trị mua (xanh)/bán (đỏ) của NĐTNN trên 3 sàn theo "
            "ngày, đường vàng là giá trị mua ròng (mua trừ bán) — đường này âm khi bán ròng, dương "
            "khi mua ròng. Phải là bảng top 10 mã giao dịch ròng lớn nhất trong ngày gần nhất, chia "
            "thành 2 cột độc lập: CỘT TRÁI (thanh đỏ, nhãn 'Top bán ròng') liệt kê các mã bị bán "
            "ròng kèm giá trị tỷ đồng ngay cạnh thanh đó; CỘT PHẢI (thanh xanh, nhãn 'Top mua ròng') "
            "liệt kê các mã được mua ròng kèm giá trị tỷ đồng ngay cạnh thanh đó. Mã ở cùng hàng "
            "trong 2 cột KHÔNG liên quan đến nhau — đây là 2 danh sách hoàn toàn riêng biệt. Giá trị "
            "tỷ đồng của mỗi mã CHỈ thuộc về cột chứa mã đó, không được lấy số từ cột này gán cho "
            "tên mã ở cột kia."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: khối ngoại mua ròng hay bán ròng trong ngày "
            "gần nhất (dựa vào đường giá trị mua ròng), mã nào bị bán ròng mạnh nhất (từ cột trái) "
            "và mã nào được mua ròng mạnh nhất (từ cột phải), xu hướng vài phiên gần đây đang nghiêng "
            "về mua hay bán. Nêu cụ thể số liệu tỷ đồng và tên mã. "
            "QUY TẮC BẮT BUỘC: mỗi mã chỉ đọc giá trị từ ĐÚNG cột chứa mã đó — mã ở cột trái (bán "
            "ròng) đi kèm số tỷ đồng của cột trái, mã ở cột phải (mua ròng) đi kèm số tỷ đồng của "
            "cột phải. Tuyệt đối không hoán đổi giá trị giữa 2 cột."
        ),
    ),

    Section(
        "Tự doanh", "🏦", "#proprietary-container",
        ready_selector="#bar-chart-buy",
        layout=(
            "Ảnh gồm 2 phần, cấu trúc tương tự khối ngoại. Trái là biểu đồ giá trị mua (xanh)/bán "
            "(đỏ) của khối tự doanh công ty chứng khoán trên 3 sàn theo ngày, đường vàng là giá trị "
            "mua ròng. Phải là bảng top 10 mã giao dịch ròng lớn nhất trong ngày gần nhất, chia 2 "
            "cột độc lập: CỘT TRÁI (thanh đỏ, nhãn 'Top bán ròng') liệt kê các mã bị bán ròng kèm "
            "giá trị tỷ đồng ngay cạnh thanh đó; CỘT PHẢI (thanh xanh, nhãn 'Top mua ròng') liệt kê "
            "các mã được mua ròng kèm giá trị tỷ đồng ngay cạnh thanh đó. Mã cùng hàng trong 2 cột "
            "KHÔNG liên quan đến nhau. Giá trị tỷ đồng của mỗi mã CHỈ thuộc về cột chứa mã đó."
        ),
        focus=(
            "Viết 3-4 câu phân tích bằng tiếng Việt: tự doanh mua ròng hay bán ròng trong ngày gần "
            "nhất, mã nào bị bán ròng mạnh nhất (từ cột trái) và mã nào được mua ròng mạnh nhất (từ "
            "cột phải), nhận xét xu hướng so với vài phiên trước. Nêu cụ thể số liệu tỷ đồng và tên "
            "mã. QUY TẮC BẮT BUỘC: mỗi mã chỉ đọc giá trị từ ĐÚNG cột chứa mã đó — tuyệt đối không "
            "hoán đổi giá trị giữa cột trái (bán ròng) và cột phải (mua ròng)."
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

def _scrape_all(
    sections: list[Section] | None = None,
    *,
    debug: bool = False,
) -> list[tuple[Section, bytes]]:
    from playwright.sync_api import sync_playwright

    sections = sections or SECTIONS
    results: list[tuple[Section, bytes]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not debug,
            slow_mo=600 if debug else 0,
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
        _kill_chatbot(page)

        for section in sections:
            try:
                img = _scrape_section(page, section, debug=debug)
                results.append((section, img))
                logger.info(f"[OK] {section.tab}: {len(img)//1024} KB")
            except Exception as e:
                logger.warning(f"[SKIP] {section.tab}: {e}")

        if debug:
            logger.info("[debug] Giữ browser 5s để quan sát...")
            page.wait_for_timeout(5_000)
        browser.close()

    return results


def _kill_chatbot(page) -> None:
    """Inject CSS persistent + MutationObserver — chạy 1 lần sau page load."""
    try:
        page.add_style_tag(content="""
            #iconVietstockMate,
            [id*="VietstockMate"], [class*="VietstockMate"],
            [id*="vietstockmate"], [class*="vietstockmate"],
            [id*="chatMate"], [class*="chatMate"],
            [id*="foamtree"], [class*="foamtree"] {
                display: none !important;
                visibility: hidden !important;
                pointer-events: none !important;
            }
        """)
    except Exception:
        pass
    try:
        page.evaluate("""() => {
            document.querySelectorAll(
                '#iconVietstockMate, [id*="VietstockMate"], [class*="VietstockMate"]'
            ).forEach(el => el.remove());

            new MutationObserver(mutations => {
                mutations.forEach(m => m.addedNodes.forEach(node => {
                    if (node.nodeType !== 1) return;
                    const id  = node.id || '';
                    const cls = typeof node.className === 'string' ? node.className : '';
                    if (id.toLowerCase().includes('vietstockmate') ||
                        cls.toLowerCase().includes('vietstockmate') ||
                        id === 'iconVietstockMate') {
                        node.remove();
                    }
                }));
            }).observe(document.body, { childList: true, subtree: true });
        }""")
    except Exception:
        pass


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
    # Xóa bot VietstockMate khỏi DOM hẳn — không dùng display:none vì bị override
    try:
        page.evaluate("""() => {
            ['#iconVietstockMate', '#btn-close-vietstockmate'].forEach(sel => {
                const el = document.querySelector(sel);
                if (el) el.remove();
            });
        }""")
    except Exception:
        pass
    try:
        page.evaluate("""() => {
            document.querySelectorAll(
                'iframe, [class*="adsby"], [id*="google_ads"], '
                '[class*="ad-overlay"], [class*="popup"], .modal-backdrop, '
                '[class*="foamtree"], [id*="foamtree"], '
                '[class*="chatbox"], [class*="chat-box"], [class*="chat-widget"], '
                '[class*="live-chat"], [id*="chat-widget"], '
                '[class*="zalo-chat"], [class*="tawk"], '
                '.vs-chat, #vs-chat, [class*="vs-chat"]'
            ).forEach(el => { el.style.display = 'none'; });
        }""")
    except Exception:
        pass


_FULLSCREEN_BTN = "[data-name='toolbar-fullscreen']"


def _screenshot_fullscreen(page, el, *, debug: bool = False) -> bytes:
    """Click fullscreen, chụp, rồi restore về normal."""
    try:
        btn = page.locator(_FULLSCREEN_BTN).first
        btn.click(timeout=3_000)
        page.wait_for_timeout(800)
        if debug:
            bb = el.bounding_box()
            logger.info(f"[debug] Fullscreen bounding box: {bb}")
    except Exception as exc:
        if debug:
            logger.warning(f"[debug] Fullscreen click failed: {exc} — chụp không fullscreen")
        return el.screenshot()

    _dismiss_ads(page)
    # Chờ thêm 500ms rồi remove lại lần nữa — bot có thể được tạo lại sau fullscreen transition
    page.wait_for_timeout(500)
    try:
        page.evaluate("""() => {
            const el = document.querySelector('#iconVietstockMate');
            if (el) el.remove();
        }""")
    except Exception:
        pass
    img = el.screenshot()
    if debug:
        logger.info(f"[debug] Fullscreen screenshot: {len(img)//1024} KB")

    try:
        page.locator(_FULLSCREEN_BTN).first.click(timeout=3_000)
        page.wait_for_timeout(600)
        if debug:
            logger.info("[debug] Restored từ fullscreen")
    except Exception:
        pass

    return img


def _scrape_section(page, section: Section, *, debug: bool = False) -> bytes:
    for sel in [f"a:has-text('{section.tab}')", f"text={section.tab}"]:
        try:
            page.locator(sel).first.click(timeout=3_000)
            if debug:
                logger.info(f"[debug] Clicked tab: {section.tab!r} via {sel!r}")
            break
        except Exception:
            pass

    for sel in (s.strip() for s in section.selector.split(",")):
        try:
            page.wait_for_selector(sel, state="visible", timeout=_TIMEOUT_MS)
            el = page.locator(sel).first
            el.scroll_into_view_if_needed()
            if debug:
                logger.info(f"[debug] Container visible: {sel!r}")

            if section.ready_selector:
                if debug:
                    logger.info(f"[debug] Waiting ready_selector: {section.ready_selector!r}")
                t0 = time.time()
                try:
                    page.wait_for_selector(section.ready_selector, state="visible", timeout=12_000)
                    elapsed = time.time() - t0
                    if debug:
                        logger.info(f"[debug] ready_selector visible after {elapsed:.2f}s")
                        # Log số SVG path/rect bên trong chart — nếu = 0 thì bars chưa render
                        bars = page.evaluate(f"""() => {{
                            const el = document.querySelector('{section.ready_selector}');
                            if (!el) return {{paths: 0, rects: 0, texts: 0}};
                            return {{
                                paths: el.querySelectorAll('path').length,
                                rects: el.querySelectorAll('rect').length,
                                texts: el.querySelectorAll('text').length,
                            }};
                        }}""")
                        logger.info(f"[debug] SVG elements inside chart: {bars}")
                except Exception:
                    if debug:
                        logger.warning(f"[debug] ready_selector timeout — fallback 3s wait")
                    page.wait_for_timeout(3_000)
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    page.wait_for_timeout(3_000)

            if section.extra_wait_ms:
                page.wait_for_timeout(section.extra_wait_ms)
            _dismiss_ads(page)

            if section.fullscreen:
                img = _screenshot_fullscreen(page, el, debug=debug)
            else:
                if debug:
                    bb = el.bounding_box()
                    logger.info(f"[debug] Element bounding box: {bb}")
                img = el.screenshot()
                if debug:
                    logger.info(f"[debug] Screenshot taken: {len(img)//1024} KB")
            return img
        except Exception as exc:
            if debug:
                logger.warning(f"[debug] Selector {sel!r} failed: {exc}")

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
        from scanner.config import GROQ_API_KEY, GROQ_VISION_MODEL
        b64 = base64.b64encode(img_bytes).decode()
        client = Groq(api_key=GROQ_API_KEY)
        kwargs = dict(
            model=GROQ_VISION_MODEL,
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
            max_tokens=1500,
            temperature=0.4,
        )
        try:
            # Model có thể là reasoning model (vd Qwen3) — tắt hẳn chain-of-thought
            # (reasoning_effort="none") để trả lời ngay, tránh nuốt hết token vào <think>
            # rồi bị cắt giữa chừng. reasoning_format="hidden" phòng khi model vẫn suy luận.
            # Một số model không hỗ trợ 2 tham số này.
            resp = client.chat.completions.create(
                **kwargs, reasoning_format="hidden", reasoning_effort="none",
            )
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        body = resp.choices[0].message.content or ""
        # Fallback phòng khi model vẫn lộ block <think> dù đã set reasoning_format
        body = re.sub(r"<think>.*?</think>", "", body, flags=re.DOTALL | re.IGNORECASE).strip()
        if not body:
            raise ValueError("caption rỗng sau khi loại bỏ reasoning")
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
    args = sys.argv[1:]
    force  = "--force" in args
    save   = "--save"  in args   # lưu ảnh ra screenshots/ để debug local
    debug  = "--debug" in args   # browser hiện, slow_mo, log SVG elements

    # --section "Nước ngoài" --section "Tự doanh"
    selected: list[str] = []
    for i, a in enumerate(args):
        if a == "--section" and i + 1 < len(args):
            selected.append(args[i + 1])
    sections_to_run = (
        [s for s in SECTIONS if s.tab in selected] if selected else None
    )
    if selected and not sections_to_run:
        logger.warning(f"Không tìm thấy section: {selected}. Dùng tên đúng: {[s.tab for s in SECTIONS]}")
        sys.exit(1)

    if save or debug:
        import pathlib
        out = pathlib.Path("screenshots")
        out.mkdir(exist_ok=True)
        from scanner.utils import is_trading_day
        if not force and not is_trading_day(date.today()):
            logger.info("Không phải ngày giao dịch — bỏ qua (dùng --force để ép)")
        else:
            mode_label = "[debug+save]" if debug else "[save]"
            logger.info(f"=== Vietstock Market Report {mode_label} ===")
            results = _scrape_all(sections_to_run, debug=debug)
            for section, img in results:
                fname = out / f"{section.tab.replace(' ', '_')}.png"
                fname.write_bytes(img)
                logger.info(f"  Saved: {fname} ({len(img)//1024} KB)")
            logger.info(f"=== Xong {len(results)} section(s) — ảnh trong screenshots/ ===")
    else:
        run(force=force)
