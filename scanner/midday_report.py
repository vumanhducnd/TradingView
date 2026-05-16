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

import scanner.config  # noqa: F401 — bắt buộc để load_dotenv() chạy trước DB
from scanner.utils import fmt_price, is_trading_day, logger


# ─── Lấy top mã biến động ─────────────────────────────────────────────────────

def _get_top_movers(min_pct: float = 2.0, top_n: int = 20, force: bool = False) -> list[dict]:
    """
    So sánh giá đóng cửa hôm nay vs hôm qua.
    Chỉ lấy mã biến động >=min_pct%, sắp xếp theo thanh khoản (volume*close) cao nhất.
    force=True → dùng 2 ngày giao dịch gần nhất trong DB thay vì today/yesterday.
    """
    from scanner.database import db_cursor

    if force:
        # Lấy 2 ngày giao dịch gần nhất thực sự có data trong DB
        try:
            with db_cursor(commit=False) as cur:
                cur.execute("SELECT DISTINCT date FROM ohlcv ORDER BY date DESC LIMIT 2")
                dates = [r["date"] for r in cur.fetchall()]
            if len(dates) < 2:
                return []
            today, prev = dates[0], dates[1]
        except Exception as e:
            logger.warning(f"_get_top_movers: lay dates tu DB failed: {e}")
            return []
    else:
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


# ─── Nhóm ngành ───────────────────────────────────────────────────────────────

_SECTORS: dict[str, str] = {
    # Ngân hàng
    "VCB":"Ngân hàng","BID":"Ngân hàng","CTG":"Ngân hàng","MBB":"Ngân hàng",
    "VPB":"Ngân hàng","TCB":"Ngân hàng","ACB":"Ngân hàng","STB":"Ngân hàng",
    "HDB":"Ngân hàng","VIB":"Ngân hàng","MSB":"Ngân hàng","OCB":"Ngân hàng",
    "LPB":"Ngân hàng","TPB":"Ngân hàng","SHB":"Ngân hàng","SSB":"Ngân hàng",
    # Bất động sản
    "VHM":"Bất động sản","VIC":"Bất động sản","NVL":"Bất động sản",
    "DIG":"Bất động sản","KDH":"Bất động sản","PDR":"Bất động sản",
    "BCM":"Bất động sản","NLG":"Bất động sản","HDG":"Bất động sản",
    # Thép & Tài nguyên
    "HPG":"Thép","HSG":"Thép","NKG":"Thép","SMC":"Thép",
    # Dầu khí
    "GAS":"Dầu khí","PLX":"Dầu khí","BSR":"Dầu khí","OIL":"Dầu khí",
    "PVS":"Dầu khí","PVD":"Dầu khí","PVC":"Dầu khí",
    # Thực phẩm & Tiêu dùng
    "VNM":"Thực phẩm","SAB":"Thực phẩm","MCH":"Thực phẩm","MSN":"Tiêu dùng",
    # Bán lẻ & Công nghệ
    "MWG":"Bán lẻ","FRT":"Bán lẻ","DGW":"Bán lẻ","FPT":"Công nghệ",
    # Điện & Hạ tầng
    "REE":"Điện","GEX":"Điện","POW":"Điện","PC1":"Xây dựng",
    "HBC":"Xây dựng","CTD":"Xây dựng",
    # Hàng không & Vận tải
    "HVN":"Hàng không","VJC":"Hàng không","GMD":"Vận tải","VSC":"Vận tải",
    # Chứng khoán
    "SSI":"Chứng khoán","VND":"Chứng khoán","HCM":"Chứng khoán","MBS":"Chứng khoán",
}


def _group_by_sector(movers: list[dict]) -> str:
    """Gom mã biến động vào nhóm ngành, trả về text mô tả."""
    from collections import defaultdict
    groups: dict[str, list[str]] = defaultdict(list)
    for m in movers:
        sector = _SECTORS.get(m["ticker"], "Khác")
        pct = float(m["pct_chg"])
        arrow = "▲" if pct > 0 else "▼"
        groups[sector].append(f"{m['ticker']}{arrow}{abs(pct):.1f}%")
    return "  |  ".join(f"{s}: {', '.join(t)}" for s, t in sorted(groups.items()))


# ─── AI tổng hợp ──────────────────────────────────────────────────────────────

def _build_prompt(movers: list[dict], news_ticker: list[dict], news_hot: list[dict]) -> str:
    today_str = date.today().strftime("%d/%m/%Y")

    mover_lines = []
    for m in movers[:15]:
        pct = float(m["pct_chg"])
        arrow = "▲" if pct > 0 else "▼"
        tk = float(m["turnover"] or 0) / 1e9
        sector = _SECTORS.get(m["ticker"], "")
        sector_tag = f" [{sector}]" if sector else ""
        mover_lines.append(
            f"- {m['ticker']}{sector_tag}: {arrow}{abs(pct):.1f}% | giá {fmt_price(float(m['close']))} | TK {tk:.1f} tỷ"
        )

    sector_summary = _group_by_sector(movers)
    news_ticker_lines = [f"- [{n['source']}] {n['title']}" for n in news_ticker[:6]]
    news_hot_lines    = [f"- [{n['source']}] {n['title']}" for n in news_hot[:5]]

    return f"""Bạn là chuyên gia phân tích chứng khoán Việt Nam. Viết 1 bài tổng hợp giữa phiên ngày {today_str}, ngắn gọn khoảng 150 từ, bằng tiếng Việt, dành cho nhà đầu tư cá nhân.

Các mã biến động mạnh (≥2%) sáng nay, xếp theo thanh khoản:
{chr(10).join(mover_lines)}

Phân nhóm ngành: {sector_summary}

Tin tức liên quan đến các mã biến động:
{chr(10).join(news_ticker_lines) if news_ticker_lines else "Chưa tìm thấy."}

Tin tức thị trường nổi bật:
{chr(10).join(news_hot_lines) if news_hot_lines else "Chưa tìm thấy."}

Yêu cầu:
- Nêu nhóm ngành nào đang dẫn dắt / kéo lùi thị trường phiên sáng
- Đề cập 3-5 mã nổi bật, liên kết với tin tức hot nếu có
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

    movers = _get_top_movers(min_pct=2.0, top_n=20, force=force)
    if not movers:
        logger.info("Khong co ma nao bien dong >=2% — bo qua midday report.")
        return

    logger.info(f"Top movers: {len(movers)} ma")

    tickers = [m["ticker"] for m in movers]
    from scanner.news_fetcher import fetch_news, fetch_hot_news
    news_ticker = fetch_news(tickers=tickers)
    news_hot    = fetch_hot_news()
    logger.info(f"Tin tuc: {len(news_ticker)} theo ma, {len(news_hot)} tin hot")

    summary = _call_ai(_build_prompt(movers, news_ticker, news_hot))
    if not summary:
        summary = "Không thể tải phân tích AI lúc này."

    # Danh sách mã tăng/giảm — hiện top 5, còn lại gộp "+X mã khác"
    up   = [m for m in movers if float(m["pct_chg"]) > 0]
    down = [m for m in movers if float(m["pct_chg"]) < 0]

    def _ticker_list(lst: list[dict], show: int = 8) -> str:
        if not lst:
            return "–"
        sign = "+" if float(lst[0]["pct_chg"]) > 0 else ""
        top  = "  ".join(f"{m['ticker']}({sign}{float(m['pct_chg']):.1f}%)" for m in lst[:show])
        rest = len(lst) - show
        return f"{top}  (+{rest} mã khác)" if rest > 0 else top

    today_str = today.strftime("%d/%m/%Y")
    message = "\n".join([
        f"<b>📰 Tổng hợp giữa phiên — {today_str}</b>",
        f"🟢 Tăng mạnh ({len(up)} mã): {_ticker_list(up)}",
        f"🔴 Giảm mạnh ({len(down)} mã): {_ticker_list(down)}",
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
