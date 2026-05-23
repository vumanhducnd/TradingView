"""
Báo cáo phân tích tuần — chạy sáng Chủ nhật hàng tuần.

Luồng:
  1. Lấy VNINDEX hiệu suất tuần + bias thị trường từ DB
  2. Fetch tin tức RSS tuần qua
  3. AI phân tích vĩ mô tuần qua + chiến lược tuần tới
  4. Gửi Telegram (cả 2 bot)
"""
from __future__ import annotations

import textwrap
from datetime import date

import scanner.config  # noqa: F401 — load_dotenv trước DB
from scanner.database import db_cursor
from scanner.utils import logger


# ─── Week range ───────────────────────────────────────────────────────────────

def _get_week_dates() -> tuple[date, date]:
    """Trả về (ngày đầu tuần, ngày cuối tuần) thực tế có data trong DB."""
    with db_cursor(commit=False) as cur:
        cur.execute("""
            SELECT MIN(date) AS week_start, MAX(date) AS week_end
            FROM (
                SELECT DISTINCT date FROM ohlcv
                WHERE date > CURRENT_DATE - 8
                  AND date < CURRENT_DATE
            ) t
        """)
        row = cur.fetchone()
    if not row or not row["week_start"]:
        raise RuntimeError("Không có dữ liệu tuần trong DB (ohlcv trống hoặc chưa update)")
    return row["week_start"], row["week_end"]


# ─── VNINDEX weekly ───────────────────────────────────────────────────────────

def _fetch_vnindex_weekly(week_start: date) -> str:
    """Lấy hiệu suất tuần của VNINDEX qua vnstock API."""
    try:
        from scanner.data_fetcher import fetch_ohlcv
        df = fetch_ohlcv("VNINDEX", days=10)
        if df is None or df.empty:
            return ""
        import pandas as pd
        df = df.sort_index()
        week_df = df[df.index >= pd.Timestamp(week_start)]
        if week_df.empty:
            return ""
        s_close = float(week_df["close"].iloc[0])
        e_close = float(df["close"].iloc[-1])
        pct     = (e_close - s_close) / s_close * 100
        arrow   = "▲" if pct > 0 else "▼"
        return f"VNINDEX: {e_close:,.2f} điểm ({arrow}{abs(pct):.2f}% trong tuần)"
    except Exception as e:
        logger.warning(f"_fetch_vnindex_weekly: {e}")
        return ""


# ─── AI prompt & call ─────────────────────────────────────────────────────────

def _build_prompt(
    week_str: str,
    vnindex_line: str,
    news: list[dict],
) -> str:
    news_lines = [f"  - [{n['source']}] {n['title']}" for n in news[:10]]

    return textwrap.dedent(f"""
        Bạn là chuyên gia phân tích vĩ mô và chứng khoán Việt Nam, viết báo cáo tuần cho nhà đầu tư cá nhân.
        Tuần phân tích: {week_str}

        CHỈ SỐ THỊ TRƯỜNG:
        {vnindex_line or "Không lấy được dữ liệu VNINDEX."}

        TIN TỨC NỔI BẬT TUẦN QUA:
{chr(10).join(news_lines) if news_lines else "  Không có tin tức."}

        Viết báo cáo phân tích tuần theo 3 phần sau, tiếng Việt có dấu, KHÔNG dùng markdown hay bullet.
        Mỗi phần viết đủ ý, chi tiết, có chiều sâu — không viết chung chung.
        Mỗi phần BẮT ĐẦU BẰNG ĐÚNG tiêu đề sau trên 1 dòng riêng (viết hoa, không thêm ký tự khác),
        rồi xuống dòng trống, rồi viết nội dung. Giữa các phần xuống 2 dòng trống.

        ── TỔNG QUAN VĨ MÔ TUẦN QUA
        (5-6 câu) Phân tích diễn biến VNINDEX trong tuần, các yếu tố vĩ mô quốc tế (Fed, USD, hàng hóa, địa chính trị) và trong nước (chính sách, tín dụng, đầu tư công) tác động như thế nào. Nêu rõ nhóm ngành nào dẫn dắt, nhóm nào bị bán ra và lý do cụ thể.

        ── ĐIỂM NHẤN & RỦI RO
        (4-5 câu) Chọn 2-3 sự kiện hoặc diễn biến quan trọng nhất tuần qua (dựa vào tin tức đã cung cấp), phân tích tác động thực tế và tiềm năng đến TTCK Việt Nam. Nêu rõ rủi ro nào đang âm ỉ cần theo dõi.

        ── CHIẾN LƯỢC TUẦN TỚI
        (5-6 câu) Nhận định xu hướng ngắn hạn của VNINDEX (tích lũy/phục hồi/phân phối); nên giữ nguyên, tăng hay giảm tỷ trọng; nhóm ngành/cổ phiếu nên quan tâm và lý do; các sự kiện lịch kinh tế tuần tới cần theo dõi; ngưỡng hỗ trợ/kháng cự quan trọng của VNINDEX.
    """).strip()


def _call_ai(prompt: str) -> str:
    try:
        from scanner.ai_analyst import _call, _get_client
        client = _get_client()
        return _call(client, prompt, max_tokens=2000)
    except Exception as e:
        logger.warning(f"AI weekly failed: {e}")
        return "Không thể tải phân tích AI lúc này."


# ─── Entry point ──────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    today = date.today()
    if not force and today.weekday() != 6:  # 6 = Chủ nhật
        logger.info(
            f"Hôm nay không phải Chủ nhật (weekday={today.weekday()}) "
            "— bỏ qua weekly report."
        )
        return

    logger.info("Weekly report: bắt đầu...")

    try:
        week_start, week_end = _get_week_dates()
    except Exception as e:
        logger.error(f"Không lấy được dữ liệu tuần: {e}")
        return

    week_str     = f"{week_start.strftime('%d/%m')} – {week_end.strftime('%d/%m/%Y')}"
    vnindex_line = _fetch_vnindex_weekly(week_start)

    from scanner.news_fetcher import fetch_hot_news
    news = fetch_hot_news(max_items=10)

    logger.info(f"Tuần: {week_str} | News: {len(news)} | VNINDEX: {vnindex_line or 'N/A'}")

    prompt  = _build_prompt(week_str, vnindex_line, news)
    ai_text = _call_ai(prompt)

    formatted_ai = ai_text
    for header in ("── TỔNG QUAN VĨ MÔ TUẦN QUA", "── ĐIỂM NHẤN & RỦI RO", "── CHIẾN LƯỢC TUẦN TỚI"):
        formatted_ai = formatted_ai.replace(header, f"<b>{header}</b>")

    lines = [
        f"<b>BÁO CÁO TUẦN — {week_str}</b>",
        "",
    ]
    if vnindex_line:
        lines.append(f"▪ {vnindex_line}")
        lines.append("")

    lines += [
        formatted_ai,
        "",
    ]

    if news:
        lines.append("<b>── Tin hot tuần qua</b>")
        for n in news[:5]:
            title = n["title"]
            link  = n.get("link", "")
            src   = n["source"]
            label = f'<a href="{link}">{title}</a>' if link else title
            lines.append(f"  • {label} <i>({src})</i>")
        lines.append("")

    lines.append("<i>Phân tích AI để tham khảo — bạn vẫn là người ra quyết định nhé!</i>")
    message = "\n".join(lines)

    from scanner.telegram_bot import send_message_both
    send_message_both(message)
    logger.info("Weekly report: đã gửi Telegram.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bỏ qua kiểm tra ngày Chủ nhật")
    args = parser.parse_args()
    run(force=args.force)
