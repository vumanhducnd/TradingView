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

_S = "Ngân hàng"
_R = "Bất động sản"
_ST = "Thép"
_O = "Dầu khí"
_F = "Thực phẩm"
_C = "Tiêu dùng"
_RT = "Bán lẻ"
_T = "Công nghệ"
_E = "Điện & Năng lượng"
_B = "Xây dựng"
_A = "Hàng không & Vận tải"
_SC = "Chứng khoán"
_CH = "Hóa chất & Phân bón"
_PH = "Dược phẩm"
_TX = "Dệt may"
_SF = "Thủy sản"
_RB = "Cao su"
_IN = "Bảo hiểm"
_MN = "Khai khoáng"

_SECTORS: dict[str, str] = {
    # Ngân hàng (16)
    "VCB":_S,"BID":_S,"CTG":_S,"MBB":_S,"VPB":_S,"TCB":_S,"ACB":_S,"STB":_S,
    "HDB":_S,"VIB":_S,"MSB":_S,"OCB":_S,"LPB":_S,"TPB":_S,"SHB":_S,"SSB":_S,
    "NAB":_S,"BAB":_S,"KLB":_S,"ABB":_S,"NVB":_S,"VAB":_S,"BVB":_S,"EIB":_S,
    # Bất động sản (20)
    "VHM":_R,"VIC":_R,"NVL":_R,"DIG":_R,"KDH":_R,"PDR":_R,"BCM":_R,"NLG":_R,
    "HDG":_R,"DXG":_R,"AGG":_R,"CEO":_R,"DRH":_R,"HQC":_R,"DXS":_R,"VRE":_R,
    "SCR":_R,"TDH":_R,"LDG":_R,"IDC":_R,"KBC":_R,"SZC":_R,"D2D":_R,"IJC":_R,
    "NHA":_R,"CII":_R,"SGR":_R,"DPG":_R,"TIP":_R,"SJS":_R,
    # Thép & Kim loại (10)
    "HPG":_ST,"HSG":_ST,"NKG":_ST,"SMC":_ST,"TLH":_ST,"VGS":_ST,
    "POM":_ST,"VIS":_ST,"TNA":_ST,"TVN":_ST,
    # Dầu khí (10)
    "GAS":_O,"PLX":_O,"BSR":_O,"OIL":_O,"PVS":_O,"PVD":_O,
    "PVC":_O,"CNG":_O,"PGD":_O,"PVT":_O,
    # Thực phẩm (10)
    "VNM":_F,"SAB":_F,"MCH":_F,"QNS":_F,"VHC":_F,"ANV":_F,
    "IDI":_F,"FMC":_F,"ABT":_F,"ACL":_F,
    # Tiêu dùng & Bán lẻ (8)
    "MSN":_C,"MWG":_RT,"FRT":_RT,"DGW":_RT,"PNJ":_RT,"HAX":_RT,"SVC":_RT,"VGI":_C,
    # Công nghệ (6)
    "FPT":_T,"CMG":_T,"ELC":_T,"ITD":_T,"SAM":_T,"VGI":_T,
    # Điện & Năng lượng (12)
    "REE":_E,"GEX":_E,"POW":_E,"PC1":_E,"PPC":_E,"CHP":_E,
    "SHP":_E,"TBC":_E,"VSH":_E,"NT2":_E,"HND":_E,"QTP":_E,
    # Xây dựng & Vật liệu (12)
    "HBC":_B,"CTD":_B,"VCG":_B,"FCN":_B,"HHV":_B,"C4G":_B,
    "PHC":_B,"SC5":_B,"L14":_B,"TV2":_B,"HT1":_B,"BCC":_B,
    # Hàng không & Vận tải (8)
    "HVN":_A,"VJC":_A,"GMD":_A,"VSC":_A,"HAH":_A,"PVT":_A,"VTO":_A,"SGP":_A,
    # Chứng khoán (10)
    "SSI":_SC,"VND":_SC,"HCM":_SC,"MBS":_SC,"BSI":_SC,
    "VCI":_SC,"ORS":_SC,"SHS":_SC,"AGR":_SC,"FTS":_SC,
    # Hóa chất & Phân bón (8)
    "DCM":_CH,"DPM":_CH,"BMP":_CH,"AAA":_CH,"NTP":_CH,"CSV":_CH,"DDV":_CH,"LAS":_CH,
    # Dược phẩm (6)
    "DHG":_PH,"IMP":_PH,"DMC":_PH,"OPC":_PH,"TRA":_PH,"DBD":_PH,
    # Dệt may (6)
    "TNG":_TX,"MSH":_TX,"TCM":_TX,"STK":_TX,"GMC":_TX,"VGT":_TX,
    # Cao su (5)
    "PHR":_RB,"DPR":_RB,"CSV":_RB,"TRC":_RB,"BRC":_RB,
    # Bảo hiểm (5)
    "BVH":_IN,"PVI":_IN,"BIC":_IN,"MIG":_IN,"PTI":_IN,
    # Khai khoáng (4)
    "KSB":_MN,"DHA":_MN,"NNC":_MN,"VPG":_MN,
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
