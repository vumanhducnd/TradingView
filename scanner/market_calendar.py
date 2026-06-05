"""
Lịch các ngày biến động mạnh thị trường VN.
Được gọi từ báo cáo sáng 7:00 để cảnh báo sớm.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple


class MarketEvent(NamedTuple):
    icon: str
    title: str
    desc: str
    level: str  # "high" | "medium"


# ─── Lịch cố định ────────────────────────────────────────────────────────────

# MSCI Quarterly Rebalancing — effective ngày cuối tháng 2, 5, 8, 11
# Semi-annual (tháng 5, 11) thay đổi lớn hơn quarterly (tháng 2, 8)
_MSCI_MONTHS = {
    2: "Quarterly",
    5: "Semi-Annual",
    8: "Quarterly",
    11: "Semi-Annual",
}

# Nghỉ lễ dài VN (tháng, ngày bắt đầu, số ngày nghỉ) — cập nhật hàng năm
# Format: (year, month, day_start, n_days)
_VN_HOLIDAYS: list[tuple[int, int, int, int]] = [
    # 2026
    (2026, 1,  1,  1),   # Tết Dương lịch
    (2026, 1, 26,  7),   # Tết Nguyên Đán (ước tính 26/1–1/2)
    (2026, 4, 30,  2),   # 30/4 – 1/5
    (2026, 9,  2,  1),   # Quốc khánh
    # 2027 — thêm khi có lịch chính thức
]

# Ngày ETF/VN30 rebalancing thêm thủ công khi HoSE công bố
# Format: (year, month, day, label)
_MANUAL_EVENTS: list[tuple[int, int, int, str, str]] = [
    # (year, month, day, title, desc)
    # VD: (2026, 6, 19, "VN30 Rebalancing", "HoSE điều chỉnh rổ VN30 có hiệu lực"),
]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Ngày thứ n (1-indexed) của weekday (0=Mon…6=Sun) trong tháng."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Ngày cuối cùng của weekday trong tháng."""
    last = date(year, month, calendar.monthrange(year, month)[1])
    delta = (last.weekday() - weekday) % 7
    return last - timedelta(days=delta)


def _is_trading_day(d: date) -> bool:
    """Bỏ qua cuối tuần — chưa check lịch nghỉ lễ."""
    return d.weekday() < 5


def _next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not _is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def _prev_trading_day(d: date) -> date:
    prv = d - timedelta(days=1)
    while not _is_trading_day(prv):
        prv -= timedelta(days=1)
    return prv


# ─── Kiểm tra từng loại sự kiện ──────────────────────────────────────────────

def _check_derivatives_expiry(today: date) -> list[MarketEvent]:
    events = []
    expiry = _nth_weekday(today.year, today.month, 3, 3)  # thứ 5 tuần 3

    # Tính T-1, T-2, T-3 (theo ngày giao dịch)
    t_minus = {}
    d = expiry
    for n in range(1, 4):
        d = _prev_trading_day(d)
        t_minus[n] = d  # t_minus[1]=T-1, t_minus[2]=T-2, t_minus[3]=T-3

    expiry_str = expiry.strftime("%d/%m")

    if today == expiry:
        events.append(MarketEvent(
            icon="🔔",
            title=f"HÔM NAY đáo hạn hợp đồng tương lai VN30 tháng {today.month}/{today.year}",
            desc=(
                "Biến động mạnh nhất trong tuần, đặc biệt 14:30–14:45 cuối phiên. "
                "Giá thanh toán = trung bình VN30 theo từng 5 phút trong toàn phiên — "
                "các vị thế lớn có thể 'bơm xả' để kéo giá về phía có lợi. "
                "Khuyến nghị: hạn chế lệnh lớn cuối phiên, chốt lời sớm nếu đang có lãi."
            ),
            level="high",
        ))
    elif today == t_minus[1]:
        events.append(MarketEvent(
            icon="⏰",
            title=f"T-1 đáo hạn phái sinh — ngày mai ({expiry_str}) đáo hạn",
            desc=(
                "Các vị thế lớn đang roll sang tháng mới, basis thu hẹp mạnh. "
                "Thanh khoản phái sinh tăng, áp lực lên/xuống bluechip VN30 rõ rệt."
            ),
            level="medium",
        ))
    elif today == t_minus[2]:
        events.append(MarketEvent(
            icon="📅",
            title=f"T-2 đáo hạn phái sinh — còn 2 phiên đến đáo hạn ({expiry_str})",
            desc=(
                "Các tổ chức lớn bắt đầu điều chỉnh vị thế. "
                "Theo dõi sát OI (open interest) và dòng tiền ngoại vào VN30."
            ),
            level="medium",
        ))
    elif today == t_minus[3]:
        events.append(MarketEvent(
            icon="📅",
            title=f"T-3 đáo hạn phái sinh — còn 3 phiên đến đáo hạn ({expiry_str})",
            desc=(
                "Bắt đầu tuần đáo hạn phái sinh. Biến động thường tăng dần từ hôm nay, "
                "đặc biệt nếu OI đang ở mức cao hoặc thị trường đang có xu hướng rõ."
            ),
            level="low",
        ))
    return events


def _check_msci_rebalancing(today: date) -> list[MarketEvent]:
    events = []
    m = today.month
    if m not in _MSCI_MONTHS:
        return events

    # Effective date: ngày cuối tháng (thường thứ 6 cuối tháng)
    effective = _last_weekday(today.year, m, 4)  # thứ 6 cuối tháng
    day_before = _prev_trading_day(effective)
    rebal_type = _MSCI_MONTHS[m]

    if today == effective:
        events.append(MarketEvent(
            icon="📊",
            title=f"MSCI {rebal_type} Rebalancing có hiệu lực hôm nay",
            desc=(
                "Các quỹ ETF theo dõi chỉ số MSCI Frontier/Emerging Markets tái cơ cấu danh mục. "
                "Cổ phiếu VN có thể biến động mạnh vào ATC nếu có thay đổi tỷ trọng."
            ),
            level="high" if rebal_type == "Semi-Annual" else "medium",
        ))
    elif today == day_before:
        events.append(MarketEvent(
            icon="📊",
            title=f"Ngày mai MSCI {rebal_type} Rebalancing có hiệu lực ({effective.strftime('%d/%m')})",
            desc="Dòng tiền ngoại có thể tích lũy/xả hôm nay trước khi rebalancing thực hiện.",
            level="medium",
        ))
    return events


def _check_holiday_sessions(today: date) -> list[MarketEvent]:
    events = []
    for (yr, mo, day_start, n_days) in _VN_HOLIDAYS:
        if yr != today.year:
            continue
        hol_start = date(yr, mo, day_start)
        hol_end   = hol_start + timedelta(days=n_days - 1)

        last_before = _prev_trading_day(hol_start)
        first_after = _next_trading_day(hol_end)

        if today == last_before and n_days >= 3:
            events.append(MarketEvent(
                icon="🏮",
                title=f"Phiên cuối trước kỳ nghỉ lễ {n_days} ngày ({hol_start.strftime('%d/%m')}–{hol_end.strftime('%d/%m')})",
                desc=(
                    "Nhiều nhà đầu tư chốt lời/giảm margin trước kỳ nghỉ dài. "
                    "Thanh khoản có thể phân kỳ mạnh vào ATC."
                ),
                level="medium",
            ))
        elif today == first_after and n_days >= 3:
            events.append(MarketEvent(
                icon="🏮",
                title=f"Phiên đầu tiên sau kỳ nghỉ lễ {n_days} ngày",
                desc=(
                    "Tâm lý dồn toa sau nghỉ dài, biến động gap up/down mạnh lúc ATO. "
                    "Tránh đặt lệnh ATO khối lượng lớn khi chưa rõ xu hướng mở cửa."
                ),
                level="medium",
            ))
    return events


def _check_manual_events(today: date) -> list[MarketEvent]:
    events = []
    for (yr, mo, day, title, desc) in _MANUAL_EVENTS:
        if date(yr, mo, day) == today:
            events.append(MarketEvent(
                icon="📌",
                title=title,
                desc=desc,
                level="medium",
            ))
    return events


# ─── Fetch sự kiện kinh tế quốc tế từ Finnhub ────────────────────────────────

# Quốc gia quan trọng với thị trường VN (theo thứ tự tác động)
_WATCH_COUNTRIES = {"US", "CN", "EU", "JP", "GB"}

# Từ khóa high-impact cần nổi bật (so khớp không phân biệt hoa thường)
_HIGH_IMPACT_KEYWORDS = {
    "interest rate", "fed funds", "fomc", "cpi", "inflation",
    "nonfarm", "non-farm", "gdp", "unemployment",
    "pmi", "retail sales", "pce",
}

_COUNTRY_FLAG = {
    "US": "🇺🇸", "CN": "🇨🇳", "EU": "🇪🇺",
    "JP": "🇯🇵", "GB": "🇬🇧",
}

_ICT = timezone(timedelta(hours=7))


def fetch_global_events(today: date | None = None) -> tuple[str, str]:
    """
    Fetch lịch sự kiện kinh tế quốc tế từ Finnhub cho ngày `today` và ngày mai.
    Trả về (html_data, plain_ctx) — không chứa AI inline.
    """
    from scanner.config import FINNHUB_API_KEY
    if not FINNHUB_API_KEY:
        return "", ""

    if today is None:
        today = date.today()
    tomorrow = today + timedelta(days=1)

    try:
        import requests
        url = "https://finnhub.io/api/v1/calendar/economic"
        resp = requests.get(
            url,
            params={"from": today.isoformat(), "to": tomorrow.isoformat(),
                    "token": FINNHUB_API_KEY},
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json().get("economicCalendar", [])
    except Exception as e:
        from scanner.utils import logger
        logger.warning(f"Finnhub calendar fetch failed: {e}")
        return "", ""

    # Lọc: chỉ giữ high-impact hoặc medium của các nước watch
    rows: list[dict] = []
    for item in data:
        country = (item.get("country") or "").upper()
        if country not in _WATCH_COUNTRIES:
            continue
        impact = (item.get("impact") or "").lower()
        if impact not in ("high", "medium"):
            continue

        event_name = item.get("event") or ""
        time_raw = item.get("time") or ""  # "YYYY-MM-DD HH:MM:SS" UTC

        # Parse string UTC → ICT
        try:
            dt_utc = datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            dt_ict = dt_utc.astimezone(_ICT)
            time_str   = dt_ict.strftime("%H:%M (giờ VN) %d/%m")
            event_date = dt_ict.date()
        except Exception:
            time_str   = "TBD"
            event_date = today

        # Chỉ lấy hôm nay và ngày mai
        if event_date not in (today, tomorrow):
            continue

        name_lower = event_name.lower()
        is_high = impact == "high" or any(k in name_lower for k in _HIGH_IMPACT_KEYWORDS)

        # Bỏ qua sự kiện đã có kết quả thực tế (actual != None)
        if item.get("actual") is not None:
            continue

        rows.append({
            "country": country,
            "event":   event_name,
            "time":    time_str,
            "is_high": is_high,
            "date":    event_date,
            "core_country": country in ("US", "CN"),  # ưu tiên US/CN
        })

    if not rows:
        return "", ""

    # Ưu tiên: high-impact US/CN → high-impact khác → medium US/CN → còn lại
    rows.sort(key=lambda r: (
        not (r["is_high"] and r["core_country"]),  # high US/CN lên đầu
        not r["is_high"],                           # high khác tiếp theo
        not r["core_country"],                      # medium US/CN
        r["date"], r["time"],
    ))
    rows = rows[:5]  # chỉ giữ 5 sự kiện quan trọng nhất

    lines = ["\n🌐 <b>Sự kiện kinh tế quốc tế:</b>"]
    today_rows    = [r for r in rows if r["date"] == today]
    tomorrow_rows = [r for r in rows if r["date"] == tomorrow]

    # Build plain-text cho AI trước khi format HTML
    plain_lines: list[str] = []
    for section_label, section_rows in [
        ("Hôm nay", today_rows),
        ("Ngày mai", tomorrow_rows),
    ]:
        if not section_rows:
            continue
        lines.append(f"\n<u>{section_label}:</u>")
        plain_lines.append(f"{section_label}:")
        for r in section_rows:
            flag       = _COUNTRY_FLAG.get(r["country"], r["country"])
            dot        = "‼️" if r["is_high"] else "❕"
            event_html = r["event"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"  {dot} {flag} <b>{event_html}</b> — {r['time']}")
            plain_lines.append(f"  [{r['country']}] {r['event']} lúc {r['time']}")

    return "\n".join(lines), "\n".join(plain_lines)


# ─── Yesterday market review ─────────────────────────────────────────────────

def fetch_yesterday_review() -> tuple[str, str]:
    """
    Lấy chỉ số VNINDEX/VN30/HNX + tin tức trong nước.
    Trả về (html_data, plain_ctx) — không chứa AI inline.
    """
    import pandas as _pd
    from scanner.utils import logger

    html_lines: list[str] = []
    plain_lines: list[str] = []

    # ── Chỉ số index ──────────────────────────────────────────────────────────
    try:
        from vnstock import Trading
        t = Trading(source="VCI")
        df = t.price_board(symbols_list=["VNINDEX", "VN30", "HNX"])
        if df is not None and not df.empty:
            if isinstance(df.columns, _pd.MultiIndex):
                df.columns = [f"{a}.{b}".lower() for a, b in df.columns]
            ticker_col = next(
                (c for c in ["listing.symbol", "symbol"] if c in df.columns),
                df.columns[0],
            )
            index_lines_html: list[str] = []
            for _, row in df.iterrows():
                sym   = str(row.get(ticker_col, "")).strip()
                close = row.get("match.match_price") or row.get("match.close_price", 0)
                chg   = float(row.get("match.price_change_ratio", 0) or 0)
                if not sym or not close:
                    continue
                arrow = "▲" if chg >= 0 else "▼"
                sign  = "+" if chg >= 0 else ""
                index_lines_html.append(f"  {arrow} <b>{sym}</b>: {float(close):,.0f} ({sign}{chg:.2f}%)")
                plain_lines.append(f"{sym}: {float(close):,.0f} ({sign}{chg:.2f}%)")
            if index_lines_html:
                html_lines.append("📊 <b>Thị trường hôm qua:</b>")
                html_lines.extend(index_lines_html)
    except Exception as e:
        logger.debug(f"fetch_yesterday_review index: {e}")

    # ── Tin tức trong nước ────────────────────────────────────────────────────
    try:
        from scanner.news_fetcher import fetch_hot_news
        news_items = fetch_hot_news(max_items=8)
        if news_items:
            html_lines.append("\n📰 <b>Tin nổi bật trong nước:</b>")
            plain_lines.append("\nTin tức nổi bật:")
            for n in news_items[:6]:
                title_html = (n["title"]
                              .replace("&", "&amp;")
                              .replace("<", "&lt;")
                              .replace(">", "&gt;"))
                link = n.get("link", "").strip()
                if link:
                    html_lines.append(f'  • <a href="{link}">{title_html}</a> [{n["source"]}]')
                else:
                    html_lines.append(f"  • {title_html} [{n['source']}]")
                plain_lines.append(f"  - [{n['source']}] {n['title']}")
    except Exception as e:
        logger.debug(f"fetch_yesterday_review news: {e}")

    return "\n".join(html_lines), "\n".join(plain_lines)


# ─── Public API ──────────────────────────────────────────────────────────────

def get_market_events(today: date | None = None) -> list[MarketEvent]:
    """Trả về tất cả sự kiện thị trường đặc biệt cho ngày `today`."""
    if today is None:
        today = date.today()
    events: list[MarketEvent] = []
    events += _check_derivatives_expiry(today)
    events += _check_msci_rebalancing(today)
    events += _check_holiday_sessions(today)
    events += _check_manual_events(today)
    return events


def format_events_for_telegram(events: list[MarketEvent]) -> str:
    """Format danh sách sự kiện thành block HTML cho Telegram."""
    if not events:
        return ""
    lines = ["\n⚡ <b>Lưu ý phiên hôm nay:</b>"]
    for e in events:
        sep = "━" * 20
        level_tag = "🔴" if e.level == "high" else ("🟡" if e.level == "medium" else "🔵")
        lines.append(f"\n{level_tag} {e.icon} <b>{e.title}</b>")
        lines.append(f"<i>{e.desc}</i>")
    return "\n".join(lines)
