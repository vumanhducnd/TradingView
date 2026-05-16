"""
Báo cáo giữa phiên — chạy lúc 12:00 ICT (sau phiên sáng 9:00-11:30).

Luồng:
  1. Lấy top mã biến động >=2% sáng nay, sắp xếp theo thanh khoản
  2. Crawl tin tức RSS có nhắc đến các mã đó
  3. Groq AI viết bài tổng hợp ~150 từ
  4. Gửi cả 2 bot (dài hạn + ngắn hạn)
"""
from __future__ import annotations

from datetime import date, timedelta

from scanner.utils import fmt_price, is_trading_day, logger


# ─── Lấy top mã biến động ─────────────────────────────────────────────────────

def _get_top_movers(min_pct: float = 2.0, top_n: int = 20) -> list[dict]:
    """
    So sánh giá đóng cửa hôm nay vs hôm qua.
    Chỉ lấy mã biến động >=min_pct%, sắp xếp theo thanh khoản (volume*close) cao nhất.
    """
    from scanner.database import db_cursor

    today = date.today()
    prev  = today - timedelta(days=1)
    for _ in range(7):          # lùi về ngày giao dịch gần nhất
        if is_trading_day(prev):
            break
        prev -= timedelta(days=1)

    try:
        with db_cursor(commit=False) as cur:
            cur.execute(
                """
                WITH today_bar AS (
                    SELECT ticker, close, volume * close AS turnover
                    FROM ohlcv
                    WHERE date = %s AND close > 0 AND volume > 0
                ),
                prev_bar AS (
                    SELECT ticker, close AS prev_close
                    FROM ohlcv
                    WHERE date = %s AND close > 0
                )
                SELECT t.ticker,
                       t.close,
                       p.prev_close,
                       ROUND(((t.close - p.prev_close) / p.prev_close * 100)::numeric, 2) AS pct_chg,
                       t.turnover
                FROM today_bar t
                JOIN prev_bar  p ON p.ticker = t.ticker
                WHERE ABS((t.close - p.prev_close) / p.prev_close * 100) >= %s
                ORDER BY t.turnover DESC
                LIMIT %s
                """,
                (today, prev, min_pct, top_n),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"_get_top_movers failed: {e}")
        return []


# ─── AI tổng hợp ──────────────────────────────────────────────────────────────

def _build_prompt(movers: list[dict], news: list[dict]) -> str:
    today_str = date.today().strftime("%d/%m/%Y")

    mover_lines = []
    for m in movers[:15]:
        pct = float(m["pct_chg"])
        arrow = "▲" if pct > 0 else "▼"
        tk = float(m["turnover"] or 0) / 1e9
        mover_lines.append(
            f"- {m['ticker']}: {arrow}{abs(pct):.1f}% | giá {fmt_price(float(m['close']))} | TK {tk:.1f} tỷ"
        )

    news_lines = [f"- [{n['source']}] {n['title']}" for n in news[:10]]

    return f"""Bạn là chuyên gia phân tích chứng khoán Việt Nam. Viết 1 bài tổng hợp giữa phiên ngày {today_str}, ngắn gọn khoảng 150 từ, bằng tiếng Việt, dành cho nhà đầu tư cá nhân.

Các mã có biến động mạnh (≥2%) sáng nay, sắp xếp theo thanh khoản:
{chr(10).join(mover_lines)}

Tin tức liên quan:
{chr(10).join(news_lines) if news_lines else "Chưa có tin tức nổi bật được tìm thấy."}

Yêu cầu:
- Nêu xu hướng chung thị trường phiên sáng
- Đề cập 3-5 mã nổi bật và lý do biến động (dựa vào tin tức nếu có, nếu không thì nhận định kỹ thuật)
- Kết luận: nhà đầu tư cần lưu ý gì khi bước vào phiên chiều
- Giọng văn chuyên nghiệp, súc tích, không dùng markdown"""


def _call_ai(prompt: str) -> str:
    try:
        from scanner.ai_analyst import _get_client
        client = _get_client()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        if "429" in str(e):
            logger.warning("Groq 429 → fallback Gemini")
            try:
                from scanner.ai_analyst import _call_gemini
                return _call_gemini(prompt, max_tokens=500)
            except Exception as e2:
                logger.warning(f"Gemini fallback failed: {e2}")
        else:
            logger.warning(f"AI midday failed: {e}")
        return ""


# ─── Entry point ──────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    today = date.today()
    if not force and not is_trading_day(today):
        logger.info("Hom nay khong phai ngay giao dich — bo qua midday report.")
        return

    logger.info("Midday report: bat dau...")

    movers = _get_top_movers(min_pct=2.0, top_n=20)
    if not movers:
        logger.info("Khong co ma nao bien dong >=2% — bo qua midday report.")
        return

    logger.info(f"Top movers: {len(movers)} ma")

    tickers = [m["ticker"] for m in movers]
    from scanner.news_fetcher import fetch_news
    news = fetch_news(tickers=tickers)
    logger.info(f"Tin tuc lien quan: {len(news)} bai")

    summary = _call_ai(_build_prompt(movers, news))
    if not summary:
        summary = "Không thể tải phân tích AI lúc này."

    # Danh sách mã tăng/giảm
    up   = [m for m in movers if float(m["pct_chg"]) > 0]
    down = [m for m in movers if float(m["pct_chg"]) < 0]
    up_str   = "  ".join(f"{m['ticker']}(+{float(m['pct_chg']):.1f}%)" for m in up)[:250]
    down_str = "  ".join(f"{m['ticker']}({float(m['pct_chg']):.1f}%)"  for m in down)[:250]

    today_str = today.strftime("%d/%m/%Y")
    message = "\n".join([
        f"<b>📰 Tổng hợp giữa phiên — {today_str}</b>",
        f"🟢 Tăng mạnh ({len(up)} mã): {up_str or '–'}",
        f"🔴 Giảm mạnh ({len(down)} mã): {down_str or '–'}",
        "",
        summary,
        "",
        "<i>📌 Phân tích AI để tham khảo — bạn vẫn là người ra quyết định nhé!</i>",
    ])

    from scanner.telegram_bot import send_message_both
    send_message_both(message)
    logger.info("Midday report: da gui Telegram.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Bỏ qua kiểm tra ngày giao dịch")
    args = parser.parse_args()
    run(force=args.force)
