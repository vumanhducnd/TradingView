"""
AI Analyst — Groq (free: 14,400 req/ngay, 30 RPM).
Phan tich chi tiet tung tin hieu MUA/BAN + tong quan thi truong.
"""

from __future__ import annotations

import re
import time
import textwrap
from datetime import date

import pandas as pd

from scanner.utils import logger

_BATCH_SIZE = 5      # 5 ma/call de co du token cho phan tich sau
_RPM_DELAY  = 3.0    # giay giua cac call (30 RPM = 1 call/2s)


# ─── Client ───────────────────────────────────────────────────────────────────

def _parse_retry_wait(err: str) -> int:
    """Parse 'Please try again in 45m12.9s' hoặc '30.5s' → giây (int)."""
    m = re.search(r"Please try again in (?:(\d+)m)?(?:([\d.]+)s)?", err)
    if not m:
        return 35
    minutes = int(m.group(1) or 0)
    seconds = float(m.group(2) or 0)
    return minutes * 60 + int(seconds) + 2


def _get_client():
    try:
        import httpx
        from groq import Groq
        from scanner.config import GROQ_API_KEY, GROQ_MODEL
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY chua set trong .env")
        return Groq(api_key=GROQ_API_KEY, http_client=httpx.Client(verify=False))
    except ImportError:
        raise ImportError("pip install groq httpx")


def _call_openai(prompt: str, max_tokens: int = 2048) -> str:
    """Fallback sang OpenAI ChatGPT khi Groq hết quota."""
    try:
        import openai
        from scanner.config import OPENAI_API_KEY
        if not OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY chua set — thu Gemini fallback...")
            return _call_gemini(prompt, max_tokens)
        import httpx
        client = openai.OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client(verify=False))
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.35,
            max_tokens=max_tokens,
        )
        logger.info("OpenAI fallback OK [gpt-4o-mini]")
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"OpenAI fallback loi: {e} — thu Gemini...")
        return _call_gemini(prompt, max_tokens)


def _call_gemini(prompt: str, max_tokens: int = 2048) -> str:
    """Fallback cuối: Gemini (yêu cầu billing từ Google)."""
    try:
        from google import genai
        from google.genai import types
        from scanner.config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY chua set — het fallback")
            return ""
        client = genai.Client(api_key=GEMINI_API_KEY)
        for model_name in ("gemini-2.0-flash", "gemini-1.5-flash"):
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.35,
                        max_output_tokens=max_tokens,
                    ),
                )
                logger.info(f"Gemini fallback OK [{model_name}]")
                return resp.text.strip()
            except Exception as e:
                logger.warning(f"Gemini [{model_name}] loi: {e}")
        return ""
    except Exception as e:
        logger.warning(f"Gemini fallback loi: {e}")
        return ""


def _call(client, prompt: str, max_tokens: int = 2048, _retry: int = 0) -> str:
    from scanner.config import GROQ_MODEL
    try:
        kwargs = dict(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.35,
            max_tokens=max_tokens,
        )
        try:
            # GROQ_MODEL mac dinh la reasoning model (gpt-oss) — tat chain-of-thought
            # de tra loi ngay, tranh nuot het token vao reasoning roi bi cat giua chung
            resp = client.chat.completions.create(
                **kwargs, reasoning_format="hidden", reasoning_effort="none",
            )
        except Exception:
            resp = client.chat.completions.create(**kwargs)
        body = resp.choices[0].message.content or ""
        body = re.sub(r"<think>.*?</think>", "", body, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r"<think>.*", "", body, flags=re.DOTALL | re.IGNORECASE).strip()
        if not body:
            raise ValueError("response rong sau khi loai bo reasoning")
        return body
    except Exception as e:
        err = str(e)
        if "429" in err:
            wait = _parse_retry_wait(err)
            if wait > 300 or _retry >= 2:
                logger.warning(f"Groq 429 (wait={wait}s) — chuyen sang OpenAI fallback...")
                return _call_openai(prompt, max_tokens)
            logger.warning(f"Groq 429 - cho {wait}s ({_retry+1}/2)...")
            time.sleep(wait)
            return _call(client, prompt, max_tokens, _retry + 1)
        logger.warning(f"Groq loi: {e}")
        return ""


# ─── Data context ─────────────────────────────────────────────────────────────

# ─── Market overview ──────────────────────────────────────────────────────────

def generate_market_overview(results: pd.DataFrame) -> str:
    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"AI khong kha dung: {e}")
        return ""

    today     = date.today().strftime("%d/%m/%Y")
    avg_bias  = results["bias_norm"].mean() if "bias_norm" in results.columns else 0

    buy_col  = "long_buy_signal"  if "long_buy_signal"  in results.columns else "buy_signal"
    sell_col = "long_sell_signal" if "long_sell_signal" in results.columns else "sell_signal"
    n_buy    = int(results[buy_col].sum())  if buy_col  in results.columns else 0
    n_sell   = int(results[sell_col].sum()) if sell_col in results.columns else 0
    n_bull   = int((results["bias_norm"] >= 55).sum()) if "bias_norm" in results.columns else 0
    n_bear   = int((results["bias_norm"] <= 45).sum()) if "bias_norm" in results.columns else 0

    buy_list  = results[results[buy_col].astype(bool)]["ticker"].tolist()  if buy_col  in results.columns else []
    sell_list = results[results[sell_col].astype(bool)]["ticker"].tolist() if sell_col in results.columns else []

    if "bias_norm" in results.columns and not results.empty:
        _r = results.dropna(subset=["bias_norm"]).copy()
        _r["bias_norm"] = pd.to_numeric(_r["bias_norm"], errors="coerce")
        top10_bull = _r.nlargest(10, "bias_norm")[["ticker", "bias_norm"]].to_string(index=False)
        top10_bear = _r.nsmallest(10, "bias_norm")[["ticker", "bias_norm"]].to_string(index=False)
    else:
        top10_bull = top10_bear = "Khong co du lieu"

    prompt = textwrap.dedent(f"""
        Ban la chuyen gia phan tich ky thuat chung khoan Viet Nam (VN100).
        Ngay phan tich: {today}

        THONG KE:
        - Tin hieu MUA: {n_buy} ma ({buy_list})
        - Tin hieu BAN: {n_sell} ma ({sell_list})
        - So ma bullish (bias>=55): {n_bull}/100
        - So ma bearish (bias<=45): {n_bear}/100
        - BiasNorm trung binh: {avg_bias:.1f}/100

        TOP 10 TICH CUC NHAT:
        {top10_bull}

        TOP 10 TIEU CUC NHAT:
        {top10_bear}

        Hay viet nhan dinh tong quan thi truong theo cau truc sau (bang tieng Viet):

        XU HUONG CHUNG: [1-2 cau nhan dinh xu huong tong the hom nay]

        DIEM NHAN: [Nhom nganh / ma noi bat can chu y, ly do]

        RUI RO: [Nhung rui ro / diem yeu can luu y]

        KHUYEN NGHI: [Chien luoc ngay hom nay cho nha dau tu ngan han]

        Viet bang tieng Viet thuan, khong dung markdown, khong lap lai so lieu.
    """).strip()

    logger.info("AI: tong quan thi truong...")
    return _call(client, prompt, max_tokens=1024)


# ─── End-of-session AI overview ──────────────────────────────────────────────

def generate_end_of_session_ai(
    results: "pd.DataFrame",
    buy_tickers: list[str],
    sell_tickers: list[str],
    style_label: str,
    top_gainers: list[str] | None = None,
    top_losers: list[str]  | None = None,
    intraday_reversals: dict | None = None,
) -> str:
    """
    Nhận định cuối phiên: đề cập top biến động, tín hiệu, theo nhân cách từng ngày.
    """
    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"AI không khả dụng: {e}")
        return ""

    today = date.today().strftime("%d/%m/%Y")
    weekday = date.today().weekday()

    personas = {
        0: "Bạn là dân văn phòng vừa tan ca, mở app chứng khoán xem hôm nay lãi lỗ thế nào trước khi tắt máy.",
        1: "Bạn là bà nội trợ vừa đi chợ về, nhẩm tính hôm nay mua bán cổ phiếu có lời không như tính tiền chợ.",
        2: "Bạn là tiểu thương vừa đóng cửa hàng, tổng kết cuối ngày xem hàng bán có lời không, thị trường hôm nay thế nào.",
        3: "Bạn là Gen Z vừa xong ca làm, tổng kết ngày giao dịch kiểu 'ae ơi hôm nay thị trường có toang không'.",
        4: "Bạn là nông dân nhìn lại vụ mùa hôm nay, lúa có được giá không, thu hoạch có khá không.",
    }
    persona = personas.get(weekday, personas[0])

    top_buy_str  = ", ".join(buy_tickers[:5])  if buy_tickers  else "Không có"
    top_sell_str = ", ".join(sell_tickers[:5]) if sell_tickers else "Không có"

    n_bull = int((results["bias_norm"] >= 55).sum()) if "bias_norm" in results.columns else 0
    n_bear = int((results["bias_norm"] <= 45).sum()) if "bias_norm" in results.columns else 0

    # Top tăng/giảm theo bias_norm
    top_strong = results.nlargest(3, "bias_norm")["ticker"].tolist() if "bias_norm" in results.columns else []
    top_weak   = results.nsmallest(3, "bias_norm")["ticker"].tolist() if "bias_norm" in results.columns else []

    # Intraday reversal context
    style_key = "long" if "Dài" in style_label else "short"
    rev = (intraday_reversals or {}).get(style_key, {})
    fake_breakout  = rev.get("fake_breakout",  [])
    fake_breakdown = rev.get("fake_breakdown", [])
    reversal_ctx = ""
    if fake_breakout:
        reversal_ctx += f"\n        - Bứt phá không giữ được: {', '.join(fake_breakout[:5])} (mua trong phiên nhưng cuối ngày thủng hỗ trợ)"
    if fake_breakdown:
        reversal_ctx += f"\n        - Rút chân giả: {', '.join(fake_breakdown[:5])} (bán trong phiên nhưng cuối ngày giá hồi)"

    prompt = textwrap.dedent(f"""
        {persona}
        Hôm nay {today} — Tổng kết phiên giao dịch | Khung: {style_label}

        KẾT QUẢ NGÀY:
        - Mã tăng mạnh nhất: {", ".join(top_gainers[:5]) if top_gainers else "–"}
        - Mã giảm mạnh nhất: {", ".join(top_losers[:5])  if top_losers  else "–"}
        - Bứt phá (tín hiệu mua mới): {top_buy_str}
        - Đảo chiều (tín hiệu bán mới): {top_sell_str}
        - Xu hướng mạnh: {", ".join(top_strong)}
        - Xu hướng tăng: {n_bull} mã | Xu hướng giảm: {n_bear} mã{reversal_ctx}

        Yêu cầu:
        - Tiếng Việt có dấu đầy đủ, không emoji, không bullet, không markdown
        - Đúng nhân cách được giao, tự nhiên như đang tổng kết cuối ngày
        - Đề cập cụ thể các mã có biến động nổi bật hôm nay
        - Nếu có mã "bứt phá không giữ được": dùng đúng cụm từ "bứt phá thất bại" hoặc "bứt lên rồi rớt lại", KHÔNG dùng "đảo chiều"
        - Nếu có mã "rút chân giả": dùng đúng cụm từ "rút chân giả" hoặc "giả vờ giảm rồi hồi lại", KHÔNG dùng "đảo chiều"
        - Tối đa 4-5 câu
        - Câu cuối: nhìn về ngày mai — nên làm gì
    """).strip()

    logger.info(f"AI: nhận định cuối phiên [{style_label}]...")
    return _call(client, prompt, max_tokens=1200)


# ─── Pre-session AI overview ─────────────────────────────────────────────────

def _fetch_market_context() -> str:
    """Lấy dữ liệu thị trường thực từ internet: VNINDEX, VN30, tin tức."""
    lines = []
    try:
        from vnstock import Trading
        t = Trading(source="VCI")
        df = t.price_board(symbols_list=["VNINDEX", "VN30", "HNX"])
        if df is not None and not df.empty:
            if isinstance(df.columns, __import__("pandas").MultiIndex):
                df.columns = [f"{a}.{b}".lower() for a, b in df.columns]
            for col_t in ["listing.symbol", "symbol"]:
                if col_t in df.columns:
                    ticker_col = col_t
                    break
            else:
                ticker_col = df.columns[0]
            for _, row in df.iterrows():
                sym = str(row.get(ticker_col, "")).strip()
                close = row.get("match.match_price") or row.get("match.close_price", 0)
                chg = row.get("match.price_change_ratio", 0)
                if sym and close:
                    lines.append(f"{sym}: {float(close):,.0f} ({float(chg or 0):+.2f}%)")
    except Exception as e:
        logger.debug(f"fetch_market_context index: {e}")

    try:
        from scanner.news_fetcher import fetch_hot_news
        news_items = fetch_hot_news(max_items=8)
        if news_items:
            lines.append("\nTin tức nổi bật:")
            for n in news_items:
                lines.append(f"  - [{n['source']}] {n['title']}")
    except Exception as e:
        logger.debug(f"fetch_market_context news: {e}")

    return "\n".join(lines) if lines else "Không có dữ liệu bổ sung."


def generate_pre_session_ai(
    n_bull: int, n_bear: int, n_total: int,
    near_buy: list[dict], near_sell: list[dict],
    style_label: str,
) -> str:
    """Nhận định trước phiên theo phong cách broker Việt Nam, dùng data internet."""
    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"AI không khả dụng: {e}")
        return ""

    today = date.today().strftime("%d/%m/%Y")
    market_ctx = _fetch_market_context()

    def _fmt_list(lst: list[dict], n: int = 6) -> str:
        items = []
        for r in lst[:n]:
            exch = r.get("exchange", "")
            items.append(f"{exch}:{r['ticker']} (giá {r['close']:.1f})")
        return ", ".join(items) if items else "Không có"

    sentiment = "tích cực" if n_bull > n_bear else ("tiêu cực" if n_bear > n_bull else "trung tính")

    # Chọn nhân cách theo thứ trong tuần (0=Mon, 4=Fri)
    weekday = date.today().weekday()
    personas = {
        0: (  # Thứ 2 — Dân văn phòng
            "Bạn là dân văn phòng chính hiệu, sáng thứ Hai vừa nhấm cà phê vừa liếc app chứng khoán "
            "trước giờ họp. Viết kiểu tám với đồng nghiệp, so sánh thị trường với deadline, sếp, KPI, "
            "lương tháng, thưởng Tết. Hài hước tự nhiên, đầu tuần mà thị trường như thế này thì thật sự..."
        ),
        1: (  # Thứ 3 — Bà nội trợ đi chợ
            "Bạn là bà nội trợ sáng nào cũng ra chợ, tính toán chi li từng đồng. Viết nhận định "
            "thị trường kiểu so sánh với đi chợ: giá rau, mặc cả, hàng ế, hàng đắt, bà bán thịt, "
            "cân đong đo đếm. Hài hước kiểu người đi chợ lâu năm, biết giá biết người."
        ),
        2: (  # Thứ 4 — Tiểu thương buôn bán
            "Bạn là tiểu thương buôn bán nhỏ, hiểu rõ lời lỗ từng đồng, quen với rủi ro kinh doanh. "
            "Viết nhận định thị trường kiểu so sánh với chuyện buôn bán: hàng tồn kho, khách trả chậm, "
            "vốn xoay vòng, đối tác, mùa đắt mùa ế. Thực tế, dân dã, hài hước kiểu người buôn."
        ),
        3: (  # Thứ 5 — Gen Z / Sinh viên
            "Bạn là Gen Z, sinh viên hoặc đi làm vài năm, đang mò mẫm chứng khoán. Viết nhận định "
            "bằng ngôn ngữ Gen Z Việt Nam: toang, ngáo giá, FOMO, all-in, cắt lỗ đau lòng, "
            "xanh đỏ như đèn giao thông. Hài hước, tự trào, kiểu 'thôi nói thật nhé ae ơi'."
        ),
        4: (  # Thứ 6 — Dân quê / Nông dân
            "Bạn là người nông dân hoặc dân quê, thật thà chất phác, hay so sánh mọi thứ với ruộng lúa, "
            "con bò, mùa màng, thời tiết, con gà. Viết nhận định thị trường đơn giản, mộc mạc, "
            "hài hước kiểu ông nông dân nhìn thị trường bằng con mắt chân chất của mình."
        ),
    }
    persona = personas.get(weekday, personas[0])

    prompt = textwrap.dedent(f"""
        {persona}
        Ngày: {today} | Khung: {style_label}

        TIN TỨC VÀ THỊ TRƯỜNG HÔM QUA:
        {market_ctx}

        Dựa vào tin tức trên, hãy viết nhận định theo đúng nhân cách được giao, gồm 3 đoạn tách biệt:

        Đoạn 1 — Thị trường hôm qua: phân tích dựa vào tin tức từ báo và chỉ số, 2-3 câu chi tiết, không liệt kê mã cụ thể.
        Đoạn 2 — Hôm nay nên làm gì: chiến lược cụ thể (giữ/mua/chờ/phòng thủ), lý do rõ ràng, 2 câu.
        Đoạn 3 — Lời khuyên: hài hước gần gũi theo đúng nhân cách, khiến người đọc vừa cười vừa nhớ, 1-2 câu.

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ, không emoji, không bullet, không markdown
        - Đúng nhân cách được giao, tự nhiên không gượng gạo
        - Xuống dòng trống giữa mỗi đoạn để dễ đọc
        - Không ghi tiêu đề đoạn, viết thẳng vào nội dung
    """).strip()

    logger.info(f"AI: nhận định trước phiên [{style_label}]...")
    return _call(client, prompt, max_tokens=2000)


# ─── Yesterday market review AI ──────────────────────────────────────────────

def generate_yesterday_review(market_ctx: str) -> str:
    """Phân tích nguyên nhân biến động TTCK VN phiên hôm qua dựa trên chỉ số + tin trong nước."""
    if not market_ctx.strip():
        return ""
    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"AI không khả dụng: {e}")
        return ""

    today = date.today().strftime("%d/%m/%Y")
    prompt = textwrap.dedent(f"""
        Bạn là chuyên gia phân tích thị trường chứng khoán Việt Nam.
        Ngày: {today}

        Dữ liệu thị trường và tin tức trong nước hôm qua:
        {market_ctx}

        Hãy phân tích ngắn gọn TẠI SAO thị trường VN biến động như vậy trong phiên hôm qua.
        Tập trung vào:
        - Nguyên nhân chính: tin tức trong nước, sự kiện vĩ mô VN, dòng tiền, tâm lý NĐT
        - Nhóm ngành hay yếu tố nổi bật dẫn dắt thị trường (nếu có trong tin tức)
        - 1 điểm cần chú ý cho phiên hôm nay

        Yêu cầu: 3-4 câu súc tích, tiếng Việt có dấu đầy đủ, không markdown, không bullet,
        không mở đầu bằng "Dưới đây là" hay "Theo phân tích". Bắt đầu thẳng vào nội dung.
    """).strip()

    logger.info("AI: phân tích nguyên nhân biến động hôm qua...")
    return _call(client, prompt, max_tokens=600)


# ─── Global events AI ─────────────────────────────────────────────────────────

def summarize_global_events(events_text: str) -> str:
    """
    Nhận danh sách sự kiện kinh tế quốc tế (plain text),
    trả về 2-3 câu ngắn gọn bằng tiếng Việt về tác động đến TTCK Việt Nam.
    """
    if not events_text.strip():
        return ""
    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"AI khong kha dung: {e}")
        return ""

    today = date.today().strftime("%d/%m/%Y")
    prompt = textwrap.dedent(f"""
        Bạn là chuyên gia phân tích thị trường chứng khoán Việt Nam.
        Ngày: {today}

        Dưới đây là các tin tức tài chính quốc tế 24h gần nhất:
        {events_text}

        Hãy viết đúng 2-3 câu ngắn gọn bằng tiếng Việt có dấu, giải thích:
        - Tin nào quan trọng nhất và tại sao
        - Khả năng tác động cụ thể đến TTCK Việt Nam hôm nay/ngày mai

        Yêu cầu: thẳng thắn, thực tế, không dùng markdown, không bullet, không mở đầu
        bằng "Dưới đây là" hay "Theo phân tích". Bắt đầu luôn vào nội dung chính.
    """).strip()

    logger.info("AI: tom tat su kien quoc te...")
    return _call(client, prompt, max_tokens=800)


# ─── Full pipeline ──────────────────────────────────────────────────────────

def run_full_analysis(results: pd.DataFrame) -> dict:
    """Chạy phân tích AI: chỉ market overview (analyze_signals đã bỏ)."""
    output = {"overview": ""}
    try:
        _get_client()
    except Exception as e:
        logger.warning(f"AI bi bo qua: {e}")
        return output

    output["overview"] = generate_market_overview(results)
    logger.info("AI: xong overview")
    return output
