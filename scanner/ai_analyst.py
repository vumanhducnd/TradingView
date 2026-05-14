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

_MODEL      = "llama-3.3-70b-versatile"
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
        from scanner.config import GROQ_API_KEY
        if not GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY chua set trong .env")
        return Groq(api_key=GROQ_API_KEY, http_client=httpx.Client(verify=False))
    except ImportError:
        raise ImportError("pip install groq httpx")


def _call_gemini(prompt: str, max_tokens: int = 2048) -> str:
    """Fallback sang Gemini khi Groq 429. Model: gemini-2.0-flash (free tier)."""
    try:
        from google import genai
        from google.genai import types
        from scanner.config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY chua set — bo qua fallback")
            return ""
        client = genai.Client(api_key=GEMINI_API_KEY)
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.35,
                max_output_tokens=max_tokens,
            ),
        )
        return resp.text.strip()
    except Exception as e:
        logger.warning(f"Gemini fallback loi: {e}")
        return ""


def _call(client, prompt: str, max_tokens: int = 2048, _retry: int = 0) -> str:
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.35,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        err = str(e)
        if "429" in err:
            wait = _parse_retry_wait(err)
            if wait > 300 or _retry >= 2:
                # Hết quota ngày hoặc đã retry đủ → Gemini fallback ngay
                logger.warning(f"Groq 429 (wait={wait}s) — chuyen sang Gemini fallback...")
                return _call_gemini(prompt, max_tokens)
            logger.warning(f"Groq 429 - cho {wait}s ({_retry+1}/2)...")
            time.sleep(wait)
            return _call(client, prompt, max_tokens, _retry + 1)
        logger.warning(f"Groq loi: {e}")
        return ""


# ─── Data context ─────────────────────────────────────────────────────────────

def _build_ticker_block(row: pd.Series, signal_type: str) -> str:
    """Tao block du lieu day du cho 1 ticker de AI phan tich."""
    close     = row.get("close", 0)
    bias      = row.get("bias_norm", 0)
    b_score   = row.get("b_score", 0)
    r_score   = row.get("r_score", 0)
    support   = row.get("support") or row.get("long_support") or 0
    resist    = row.get("resistance") or row.get("long_resistance") or 0
    atr       = row.get("atr") or row.get("long_atr") or 0
    turnover  = row.get("turnover", 0)

    # Trend
    long_trend  = "TANG" if row.get("long_trend",  0) == 1 else "GIAM"
    short_trend = "TANG" if row.get("short_trend", 0) == 1 else "GIAM"

    # SuperTrend
    st_long  = row.get("long_supertrend")  or row.get("supertrend") or 0
    st_short = row.get("short_supertrend") or 0

    # Indicators
    ind_map = {
        "EMA9>21":   row.get("bull_ema",    False),
        "Gia>VWAP":  row.get("bull_vwap",   False),
        "RSI>52":    row.get("bull_rsi",    False),
        "MACD tang": row.get("bull_macd",   False),
        "ADX>20":    row.get("bull_adx",    False),
        "OBV tang":  row.get("bull_obv",    False),
        "Stoch tang":row.get("bull_stoch",  False),
        "Nen xanh":  row.get("bull_candle", False),
        "Volume tang":row.get("bull_vol",   False),
    }
    bull_list = [k for k, v in ind_map.items() if v]
    bear_list = [k for k, v in ind_map.items() if not v]

    pnl = row.get("pnl_pct") or row.get("long_signal_pnl_pct")
    pnl_str = f"{pnl:+.1f}%" if pnl is not None else "N/A"

    dist_sup = round((close - support) / close * 100, 1) if close and support else 0
    dist_res = round((resist - close)  / close * 100, 1) if close and resist  else 0
    tk_ty = round(turnover / 1e9, 1) if turnover else 0

    return textwrap.dedent(f"""
        MA: {row.get('ticker', '')} | TIN HIEU: {signal_type}
        Gia dong cua: {close:.1f} | SuperTrend DH: {st_long:.1f} | ST NH: {st_short:.1f}
        Xu huong: Dai han={long_trend}, Ngan han={short_trend}
        BiasNorm: {bias:.1f}/100 (Bull {b_score}/9, Bear {r_score}/9)
        Ho tro (stop): {support:.1f} ({dist_sup:+.1f}% tu gia HT)
        Khang cu (target): {resist:.1f} ({dist_res:+.1f}% tu gia HT)
        ATR: {atr:.2f} | TK: {tk_ty} ty VND
        P&L tu tin hieu: {pnl_str}
        Chi bao BULLISH ({len(bull_list)}/9): {', '.join(bull_list) if bull_list else 'Khong co'}
        Chi bao BEARISH ({len(bear_list)}/9): {', '.join(bear_list) if bear_list else 'Khong co'}
    """).strip()


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

    top10_bull = results.nlargest(10, "bias_norm")[["ticker", "bias_norm"]].to_string(index=False)
    top10_bear = results.nsmallest(10, "bias_norm")[["ticker", "bias_norm"]].to_string(index=False)

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

    # Top biến động từ results
    import pandas as _pd
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
    return _call(client, prompt, max_tokens=400)


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
        import requests
        resp = requests.get(
            "https://cafef.vn/thi-truong-chung-khoan.chn",
            timeout=5, verify=False,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        import re
        titles = re.findall(r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>', resp.text[:8000])
        news = [re.sub(r'<[^>]+>', '', t).strip() for t in titles[:3] if t.strip()]
        if news:
            lines.append("Tin tức nổi bật: " + " | ".join(news))
    except Exception:
        pass

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

        DỮ LIỆU THỊ TRƯỜNG:
        {market_ctx}

        BỨC TRANH KỸ THUẬT:
        - Tổng mã: {n_total} | Tăng: {n_bull} | Giảm: {n_bear} | Cảm nhận: {sentiment}
        - Mã gần điểm mua: {_fmt_list(near_buy)}
        - Mã gần điểm bán: {_fmt_list(near_sell)}

        Yêu cầu bắt buộc:
        - Tiếng Việt có dấu đầy đủ, không emoji, không bullet, không markdown
        - Đúng nhân cách được giao, tự nhiên không gượng gạo
        - Tối đa 4-5 câu
        - Câu cuối là lời khuyên thực tế theo nhân cách đó
    """).strip()

    logger.info(f"AI: nhận định trước phiên [{style_label}]...")
    return _call(client, prompt, max_tokens=400)


# ─── Signal analysis ──────────────────────────────────────────────────────────

def analyze_signals(
    results: pd.DataFrame,
    signal_col: str = "buy_signal",
    top_n: int = 20,
) -> list[dict]:
    """
    Phan tich chi tiet tung tin hieu.
    Returns list[{ticker, analysis, telegram_text}]
    """
    if signal_col not in results.columns:
        return []

    signal_type = "MUA" if "buy" in signal_col else "BAN"
    df = results[results[signal_col].astype(bool)].copy()
    if df.empty:
        return []

    if "bias_norm" in df.columns:
        df = df.nlargest(top_n, "bias_norm")
    else:
        df = df.head(top_n)

    try:
        client = _get_client()
    except Exception as e:
        logger.warning(f"AI khong kha dung: {e}")
        return []

    all_results: list[dict] = []
    batches = [df.iloc[i:i + _BATCH_SIZE] for i in range(0, len(df), _BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches):
        ticker_blocks = "\n\n".join(
            _build_ticker_block(row, signal_type)
            for _, row in batch.iterrows()
        )

        action_word = "mua vào" if signal_type == "MUA" else "chốt lời / cắt vị thế"
        prompt = textwrap.dedent(f"""
            Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam với hơn 15 năm kinh nghiệm,
            từng làm việc tại các công ty chứng khoán lớn. Phong cách viết tự nhiên, chuyên nghiệp,
            gần gũi — như đang nói chuyện trực tiếp với nhà đầu tư, không dùng khuôn mẫu cứng nhắc.

            Dưới đây là dữ liệu kỹ thuật của {len(batch)} mã có tín hiệu {signal_type}:

            {ticker_blocks}

            Với TỪNG mã, viết nhận định liền mạch 3-4 câu theo phong cách chuyên gia thực thụ:
            — Câu đầu: nhận xét tổng thể về xu hướng và sức mạnh hiện tại (có thể đề cập thanh khoản, momentum)
            — Câu giữa: phân tích điểm mạnh/yếu nổi bật nhất từ các chỉ báo (không liệt kê, nói bằng lời)
            — Câu cuối: khuyến nghị cụ thể — {action_word} ở vùng nào, cắt lỗ / mục tiêu ở đâu

            Quy tắc bắt buộc:
            - Tiếng Việt có dấu đầy đủ, văn xuôi liền mạch, KHÔNG dùng tiêu đề XU HUONG / DONG LUC / RUI RO
            - KHÔNG liệt kê chỉ báo theo dạng danh sách — phân tích bằng lời tự nhiên
            - Mỗi mã có cá tính riêng, tránh viết na ná nhau
            - Đề cập số liệu cụ thể (giá, %, vùng hỗ trợ/kháng cự) ít nhất 1 lần
            - Tối đa 80 từ mỗi mã

            Format output (giữ nguyên dấu ===):
            === [TÊN MÃ] ===
            [Đoạn phân tích tự nhiên 3-4 câu]
            ===
        """).strip()

        logger.info(f"AI: batch {batch_idx+1}/{len(batches)} ({signal_type}, {len(batch)} ma)...")
        text = _call(client, prompt, max_tokens=2048)

        parsed = _parse_analysis(text, batch["ticker"].tolist())
        all_results.extend(parsed)

        if batch_idx < len(batches) - 1:
            time.sleep(_RPM_DELAY)

    return all_results


def _parse_analysis(text: str, tickers: list[str]) -> list[dict]:
    """Parse output dang === TICKER === ... === thanh list dict."""
    results = []
    # Split theo === MA === ... ===
    sections = re.split(r"===\s*([A-Z]{2,10})\s*===", text)

    # sections = [prefix, TICKER1, body1, TICKER2, body2, ...]
    i = 1
    while i + 1 < len(sections):
        ticker = sections[i].strip().upper()
        body   = sections[i + 1].strip()
        i += 2

        if ticker not in tickers:
            # Thu khop gan (bo dau cach)
            match = next((t for t in tickers if t in ticker or ticker in t), None)
            if not match:
                continue
            ticker = match

        # Format lai cho Telegram (bo === cuoi)
        body = re.sub(r"===\s*$", "", body).strip()
        telegram_text = _format_for_telegram(body)

        results.append({
            "ticker":        ticker,
            "analysis":      body,          # full text cho Excel
            "telegram_text": telegram_text, # condensed cho Telegram
        })

    return results


def _format_for_telegram(body: str) -> str:
    """Chuyen analysis text thanh HTML Telegram-friendly."""
    # Format moi: van xuoi lien mach — giu nguyen, chi escape HTML co ban
    body = body.strip()
    body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return body


# ─── Full pipeline ─────────────────────────────────────────────────────────────

def run_full_analysis(results: pd.DataFrame) -> dict:
    """
    Chay toan bo phan tich AI:
      overview, buy_signals, sell_signals
    Moi item trong signals: {ticker, analysis, telegram_text}
    """
    output = {"overview": "", "buy_signals": [], "sell_signals": []}

    try:
        _get_client()
    except Exception as e:
        logger.warning(f"AI bi bo qua: {e}")
        return output

    output["overview"] = generate_market_overview(results)
    time.sleep(_RPM_DELAY)

    buy_col  = "long_buy_signal"  if "long_buy_signal"  in results.columns else "buy_signal"
    sell_col = "long_sell_signal" if "long_sell_signal" in results.columns else "sell_signal"

    output["buy_signals"]  = analyze_signals(results, signal_col=buy_col,  top_n=20)
    time.sleep(_RPM_DELAY)
    output["sell_signals"] = analyze_signals(results, signal_col=sell_col, top_n=20)

    logger.info(
        f"AI: xong | overview + "
        f"{len(output['buy_signals'])} MUA + {len(output['sell_signals'])} BAN"
    )
    return output
