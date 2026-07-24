"""
Telegram notification module.
Uses requests (synchronous) — no asyncio needed for GitHub Actions.
"""

import html
import json
import math
import re
import time

import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import date

from scanner.config import (
    TELEGRAM_API, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN_SHORT, TELEGRAM_CHAT_ID_SHORT,
    TELEGRAM_CHAT_IDS_LONG, TELEGRAM_CHAT_IDS_SHORT,
)
from scanner.database import load_exchange_map
from scanner.utils import fmt_date, fmt_price, logger, tk_label, tv_link


def _val(row, *names, default=None):
    """Lấy giá trị đầu tiên không-NaN từ danh sách tên cột (hỗ trợ single + dual mode)."""
    for name in names:
        v = row.get(name)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return default


def _fmt(v, default="–") -> str:
    """fmt_price với fallback khi NaN/None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return fmt_price(v)


_ALLOWED_TELEGRAM_HTML_TAGS = {"b", "strong", "i", "em", "u", "code", "pre", "a"}
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)([^<>]*)>")


def _sanitize_telegram_text(text: str, parse_mode: str = "HTML") -> str:
    """Escape unsafe HTML chars for Telegram while preserving a small allowlist of tags."""
    if parse_mode != "HTML" or not text:
        return text or ""

    pieces: list[str] = []
    last_index = 0
    for match in _HTML_TAG_RE.finditer(text):
        pieces.append(html.escape(text[last_index:match.start()], quote=False))
        tag_name = match.group(2).lower()
        if tag_name in _ALLOWED_TELEGRAM_HTML_TAGS:
            pieces.append(match.group(0))
        else:
            pieces.append(html.escape(match.group(0), quote=False))
        last_index = match.end()

    pieces.append(html.escape(text[last_index:], quote=False))
    return "".join(pieces)


def _fmt_tk(row) -> str:
    """Thanh khoản hôm nay + TB20 + nhãn mức trong ngoặc."""
    tk = _val(row, "turnover")
    if tk and tk > 0:
        avg = _val(row, "avg_turnover_20d")
        avg_f = float(avg) if avg else 0.0
        ref_ty = avg_f / 1e6 if avg_f > 0 else float(tk) / 1e9
        label = tk_label(ref_ty)
        today_str = f"{float(tk) / 1e9:.1f} tỷ"
        if avg_f > 0:
            return f"{today_str} (TB20: {avg_f / 1e6:.1f} tỷ · {label})"
        return f"{today_str} ({label})"
    vol = _val(row, "volume")
    if vol and vol > 0:
        return f"{vol / 1e6:.1f}M cp"
    return "–"


def _chat_ids(style: str) -> tuple[str, list[str]]:
    """
    Trả về (token, [chat_id, ...]) theo style: 'long' | 'short'.
    Ưu tiên DB (bot_channels), fallback về .env nếu DB rỗng.
    """
    token = TELEGRAM_TOKEN_SHORT if style == "short" else TELEGRAM_TOKEN
    try:
        from scanner.database import get_bot_channels
        all_channels = get_bot_channels(style=style, active_only=False)
        if all_channels:
            # Đã cấu hình DB → chỉ gửi đến channel đang active
            active_ids = [c["chat_id"] for c in all_channels if c["is_active"]]
            return token, active_ids   # có thể rỗng nếu tất cả bị tạm dừng
    except Exception as e:
        logger.debug(f"get_bot_channels failed, fallback to env: {e}")
    # Fallback .env (chỉ khi chưa cấu hình DB)
    if style == "short":
        return token, TELEGRAM_CHAT_IDS_SHORT or [TELEGRAM_CHAT_ID_SHORT]
    return token, TELEGRAM_CHAT_IDS_LONG or [TELEGRAM_CHAT_ID]


def _send_raw(text: str, token: str, chat_id: str, parse_mode: str = "HTML") -> bool:
    if not token or not chat_id:
        logger.warning("Telegram credentials not set — skipping")
        return False
    url = TELEGRAM_API.format(token=token)
    safe_text = _sanitize_telegram_text(text, parse_mode=parse_mode)
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": safe_text, "parse_mode": parse_mode,
                      "link_preview_options": {"is_disabled": True}},
                timeout=30,
                verify=False,
            )
            if resp.status_code == 403:
                logger.warning(f"Telegram 403 Forbidden — chat_id={chat_id} (bot bị kick hoặc block)")
                return False  # không retry, 403 là lỗi vĩnh viễn
            resp.raise_for_status()
            return True
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                logger.warning(f"Telegram send failed (chat_id={chat_id}): {e}")
    return False


def send_message(text: str, style: str = "long", parse_mode: str = "HTML") -> bool:
    """Gửi tin nhắn đến tất cả channel theo style ('long' | 'short')."""
    token, chat_ids = _chat_ids(style)
    if not chat_ids:
        logger.info(f"[{style.upper()}] Không có channel nào đang active — bỏ qua.")
        return False

    # Lấy title từ DB để log rõ hơn
    try:
        from scanner.database import get_bot_channels
        title_map = {c["chat_id"]: c.get("title") or c["chat_id"]
                     for c in get_bot_channels(style=style, active_only=False)}
    except Exception:
        title_map = {}

    results = []
    for cid in chat_ids:
        ok = _send_raw(text, token, cid, parse_mode)
        label = title_map.get(cid, cid)
        if ok:
            logger.info(f"[{style.upper()}] ✓ Đã gửi → {label} ({cid})")
        else:
            logger.warning(f"[{style.upper()}] ✗ Gửi thất bại → {label} ({cid})")
        results.append(ok)
        if len(chat_ids) > 1:
            time.sleep(0.3)
    return any(results)


def send_to_user(chat_id: str, text: str, style: str = "short") -> bool:
    """Gửi tin nhắn trực tiếp đến 1 user (notification cá nhân từ đúng bot)."""
    token = TELEGRAM_TOKEN_SHORT if style == "short" else TELEGRAM_TOKEN
    return _send_raw(text, token, chat_id)


def send_message_both(text: str, parse_mode: str = "HTML") -> None:
    """Gửi cùng 1 tin đến tất cả channel của cả 2 style."""
    send_message(text, style="long", parse_mode=parse_mode)
    if TELEGRAM_TOKEN_SHORT != TELEGRAM_TOKEN or TELEGRAM_CHAT_ID_SHORT != TELEGRAM_CHAT_ID:
        time.sleep(0.2)
        send_message(text, style="short", parse_mode=parse_mode)


def send_file(file_path: str, caption: str = "", style: str = "long") -> bool:
    """Gửi file đến tất cả channel theo style."""
    token, chat_ids = _chat_ids(style)
    if not token or not chat_ids:
        logger.warning("Telegram credentials not set — skipping file send")
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    fname = file_path.split("\\")[-1].split("/")[-1]
    safe_caption = _sanitize_telegram_text(caption, parse_mode="HTML")
    ok_any = False
    for cid in chat_ids:
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": cid, "caption": safe_caption, "parse_mode": "HTML"},
                    files={"document": (fname, f)},
                    timeout=60,
                    verify=False,
                )
            resp.raise_for_status()
            ok_any = True
            if len(chat_ids) > 1:
                time.sleep(0.5)
        except Exception as e:
            logger.warning(f"Telegram send_file failed ({cid}): {e}")
    if ok_any:
        logger.info(f"File sent: {file_path}")
    return ok_any


def send_photo(image_bytes: bytes, caption: str = "", style: str = "long") -> bool:
    """Gửi ảnh PNG/JPEG (bytes) đến tất cả channel theo style."""
    token, chat_ids = _chat_ids(style)
    if not token or not chat_ids:
        logger.warning("Telegram credentials not set — skipping send_photo")
        return False
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    safe_caption = _sanitize_telegram_text(caption, parse_mode="HTML")
    ok_any = False
    for cid in chat_ids:
        try:
            resp = requests.post(
                url,
                data={"chat_id": cid, "caption": safe_caption, "parse_mode": "HTML"},
                files={"photo": ("chart.png", image_bytes, "image/png")},
                timeout=60,
                verify=False,
            )
            resp.raise_for_status()
            ok_any = True
            logger.info(f"[{style.upper()}] ✓ send_photo → {cid}")
            if len(chat_ids) > 1:
                time.sleep(0.5)
        except Exception as e:
            logger.warning(f"send_photo failed ({cid}): {e}")
    return ok_any


def send_photo_group(images: list[bytes], caption: str = "", style: str = "long") -> bool:
    """Gửi album ảnh (sendMediaGroup) đến tất cả channel theo style.
    Caption chỉ đặt trên ảnh đầu tiên, tối đa 1024 ký tự.
    """
    token, chat_ids = _chat_ids(style)
    if not token or not chat_ids:
        logger.warning("Telegram credentials not set — skipping send_photo_group")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMediaGroup"

    media_json = []
    files: dict = {}
    safe_caption = _sanitize_telegram_text(caption, parse_mode="HTML")
    for i, img in enumerate(images):
        key = f"photo{i}"
        item: dict = {"type": "photo", "media": f"attach://{key}"}
        if i == 0 and safe_caption:
            item["caption"]    = safe_caption[:1024]
            item["parse_mode"] = "HTML"
        media_json.append(item)
        files[key] = (f"chart{i}.png", img, "image/png")

    ok_any = False
    for cid in chat_ids:
        try:
            resp = requests.post(
                url,
                data={"chat_id": cid, "media": json.dumps(media_json)},
                files=files,
                timeout=60,
                verify=False,
            )
            resp.raise_for_status()
            ok_any = True
            logger.info(f"[{style.upper()}] ✓ send_photo_group({len(images)} ảnh) → {cid}")
            if len(chat_ids) > 1:
                time.sleep(0.5)
        except Exception as e:
            logger.warning(f"send_photo_group failed ({cid}): {e}")
    return ok_any


def send_daily_report(
    results: pd.DataFrame,
    signals: dict[str, pd.DataFrame],

    super_stocks: pd.DataFrame | None = None,
    intraday_reversals: dict[str, list[str]] | None = None,
) -> None:
    """
    Gửi báo cáo cuối ngày.
    Dual mode: mỗi bot nhận header + positions + tín hiệu riêng của style đó.
    Single mode: tất cả → bot dài hạn.
    """
    today = date.today().strftime("%d/%m/%Y")
    # Load exchange + avg_turnover_20d để enrich results
    tickers_all = results["ticker"].tolist() if not results.empty else []
    _exch_map = load_exchange_map(tickers_all)
    from scanner.database import load_avg_turnover
    _avg_tk = load_avg_turnover(tickers_all)
    if _avg_tk and "avg_turnover_20d" not in results.columns:
        results = results.copy()
        results["avg_turnover_20d"] = results["ticker"].map(_avg_tk)

    TOP_N = 5
    # Ưu tiên turnover (giá trị giao dịch), fallback volume
    vol_col = "turnover" if "turnover" in results.columns else ("volume" if "volume" in results.columns else None)

    def _top_n(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df.nlargest(TOP_N, vol_col) if vol_col and vol_col in df.columns else df.head(TOP_N)

    def _signal_lines(df: pd.DataFrame, emoji: str, label: str, st_col: str,
                      direction: str = "buy") -> list[str]:
        top = _top_n(df)
        if top.empty:
            return []
        total = len(df)
        shown = len(top)
        count_str = f"{shown}/{total}" if shown < total else f"{shown}"
        lines = [f"{emoji} <b>{label} — {count_str} mã</b>"]

        # Xác định prefix (long_ / short_ / "") để lấy đúng cột date/price
        if st_col.startswith("long_"):
            _pfx = "long_"
        elif st_col.startswith("short_"):
            _pfx = "short_"
        else:
            _pfx = ""

        rows_list = list(top.iterrows())
        for i, (_, row) in enumerate(rows_list):
            st    = _val(row, st_col, "supertrend")
            close = _val(row, "close")
            low   = _val(row, "low")
            crown = "👑" if row.get("is_super_stock") else ""
            _ticker_link = f"<b>{tv_link(row['ticker'], _exch_map.get(row['ticker'], ''))}{crown}</b>"
            if direction == "buy":
                sl_str = _fmt(float(st) * 0.98) if st and float(st) > 0 else "–"
                lines.append(
                    f"  {_ticker_link}\n"
                    f"    Giá mua       : {_fmt(low or close)}\n"
                    f"    Giá SL         : {sl_str}\n"
                    f"    Thanh khoản   : {_fmt_tk(row)}"
                )
            else:
                buy_date   = fmt_date(_val(row, f"{_pfx}prev_buy_date", "prev_buy_date"))
                buy_price  = _val(row, f"{_pfx}prev_buy_price",  "prev_buy_price")
                sell_price = _val(row, f"{_pfx}last_signal_price", "last_signal_price", default=close)
                try:
                    s_f = float(sell_price) if sell_price else None
                    b_f = float(buy_price)  if buy_price  else None
                    pnl = round((s_f - b_f) / b_f * 100, 2) if s_f and b_f and b_f > 0 else None
                except Exception:
                    pnl = None
                pnl_str  = (f"{'🟢' if pnl >= 0 else '🔴'} {pnl:+.2f}%") if pnl is not None else None
                date_str = buy_date if buy_date and buy_date not in ("", "N", "None") else None
                has_buy  = buy_price and float(buy_price) > 0

                block = f"  {_ticker_link}\n"
                if has_buy:
                    if date_str:
                        try:
                            from datetime import date as _d, datetime as _dt
                            _days = (_d.today() - _dt.strptime(date_str, "%d/%m/%Y").date()).days
                            _days_str = f" ({_days} ngày)"
                        except Exception:
                            _days_str = ""
                        block += f"    Ngày mua      : {date_str}{_days_str}\n"
                    block += f"    Giá mua       : {_fmt(buy_price)}\n"
                block += f"    Giá bán       : {_fmt(sell_price)}\n"
                if pnl_str:
                    block += f"    Lời/Lỗ        : {pnl_str}\n"
                block += f"    Thanh khoản   : {_fmt_tk(row)}"
                lines.append(block)
            if i < len(rows_list) - 1:
                lines.append("")  # dòng trống phân cách giữa các mã
        return lines

    def _send_for_style(style: str) -> None:
        """Gửi toàn bộ report (header + positions + tín hiệu) cho 1 style."""
        p = f"{style}_"
        is_long = style == "long"
        label   = "📈 Đầu tư Dài Hạn" if is_long else "⚡ Đầu tư Ngắn Hạn"

        buy_df  = results[results[f"{p}buy_signal"].astype(bool)]  if f"{p}buy_signal"  in results.columns else pd.DataFrame()
        sell_df = results[results[f"{p}sell_signal"].astype(bool)] if f"{p}sell_signal" in results.columns else pd.DataFrame()

        # Lọc TK TB20 < 10 tỷ — đồng bộ với filter trong phiên
        _tk_col = "avg_turnover_20d"
        if _tk_col in buy_df.columns:
            _tk_mask = buy_df[_tk_col].fillna(0) / 1e6 >= 10.0
            if (~_tk_mask).any():
                logger.info(f"  [{style}] Bo qua {(~_tk_mask).sum()} MUA vi TK TB20 < 10 ty")
            buy_df = buy_df[_tk_mask]
        if _tk_col in sell_df.columns:
            _tk_mask = sell_df[_tk_col].fillna(0) / 1e6 >= 10.0
            if (~_tk_mask).any():
                logger.info(f"  [{style}] Bo qua {(~_tk_mask).sum()} BAN vi TK TB20 < 10 ty")
            sell_df = sell_df[_tk_mask]

        no_sig  = len(results) - len(buy_df) - len(sell_df)

        style_name = "Đầu tư Dài Hạn" if is_long else "Đầu tư Ngắn Hạn"

        # Top tăng/giảm mạnh trong ngày theo TK
        top_gainers, top_losers = [], []
        try:
            from scanner.database import load_daily_change
            tickers_all = results["ticker"].tolist()
            chg_map = load_daily_change(tickers_all)
            tk_series = results.set_index("ticker")["turnover"] if "turnover" in results.columns else None
            def _sort_by_tk(t_list):
                if tk_series is None:
                    return t_list
                return sorted(t_list, key=lambda t: float(tk_series.get(t, 0) or 0), reverse=True)
            gainers = _sort_by_tk([t for t, v in chg_map.items() if v > 0])[:5]
            losers  = _sort_by_tk([t for t, v in chg_map.items() if v < 0])[:5]
            top_gainers = [f"{t}({chg_map[t]:+.1f}%)" for t in gainers]
            top_losers  = [f"{t}({chg_map[t]:+.1f}%)" for t in losers]
        except Exception as e:
            logger.warning(f"load_daily_change failed: {e}")

        # AI cuối phiên (chạy trước để gộp vào header)
        ai_end = ""
        try:
            from scanner.ai_analyst import generate_end_of_session_ai
            ai_end = generate_end_of_session_ai(
                results=results,
                buy_tickers=buy_df["ticker"].tolist() if not buy_df.empty else [],
                sell_tickers=sell_df["ticker"].tolist() if not sell_df.empty else [],
                style_label=style_name,
                top_gainers=top_gainers,
                top_losers=top_losers,
                intraday_reversals=intraday_reversals,
            )
        except Exception as e:
            logger.warning(f"AI cuoi phien failed: {e}")

        # Header + AI gộp 1 tin
        disclaimer = "<i>📌 Tín hiệu kỹ thuật để tham khảo — bạn vẫn là người ra quyết định nhé!</i>"
        header_lines = [
            f"<b>Báo cáo cuối phiên — {today} — {style_name}</b>",
            f"💰 Bứt phá: <b>{len(buy_df)}</b> mã  |  💸 Đảo chiều: <b>{len(sell_df)}</b> mã",
        ]
        if ai_end:
            header_lines.append(f"\n{ai_end}")
        header_lines.append(f"\n{disclaimer}")
        send_message("\n".join(header_lines), style=style)
        time.sleep(0.8)

        _send_top_vung_xanh(results, style=style)
        time.sleep(0.8)

        st_col = f"{p}supertrend"
        for lines in [
            _signal_lines(buy_df,  "💰", "Tín hiệu bứt phá xác nhận", st_col, direction="buy"),
            _signal_lines(sell_df, "💸", "Tín hiệu đảo chiều giảm",   st_col, direction="sell"),
        ]:
            if lines:
                send_message("\n".join(lines), style=style)
                time.sleep(0.5)

        if buy_df.empty and sell_df.empty:
            top5 = results.nlargest(5, "bias_norm")[["ticker", "bias_norm"]]
            txt = "\n".join(f"  • {tv_link(r['ticker'], _exch_map.get(r['ticker'], ''))}: {r['bias_norm']:.0f}/100" for _, r in top5.iterrows())
            send_message(f"Hôm nay không có tín hiệu mới.\n\n<b>Top 5 mạnh nhất:</b>\n{txt}", style=style)

    _send_for_style("long")
    time.sleep(1)
    _send_for_style("short")

    # ── Excel — mỗi file gửi đúng bot ────────────────────────────────────────
    try:
        from scanner.company_data import build_company_data
        from scanner.excel_report import build_excel_report
        company_data = build_company_data(results, signals)
        excel_paths = build_excel_report(
            results, signals,
            super_stocks=super_stocks,
            company_data=company_data,
        )
        for file_style, path in excel_paths.items():
            if path:
                send_file(
                    path,
                    caption=f" Report Đầu tư {'Dài hạn' if file_style == 'long' else 'Ngắn hạn'} {today}",
                    style=file_style,
                )
                time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Excel report failed: {e}")




def _send_top_vung_xanh(results: pd.DataFrame, style: str = "long", top_n: int = 5) -> None:
    """Top N mã vùng xanh (long_trend=1) theo TK cao nhất."""
    p = f"{style}_"
    exch_map = load_exchange_map(results["ticker"].tolist() if not results.empty else [])
    trend_col = f"{p}trend" if f"{p}trend" in results.columns else "trend"
    df = results[results.get(trend_col, results.get("trend", pd.Series(dtype=int))) == 1].copy()
    if df.empty:
        return
    if "turnover" in df.columns:
        df = df.sort_values("turnover", ascending=False)
    df = df.head(top_n)

    date_col  = f"{p}last_signal_date"  if f"{p}last_signal_date"  in results.columns else "last_signal_date"
    price_col = f"{p}last_signal_price" if f"{p}last_signal_price" in results.columns else "last_signal_price"

    lines = [f"<b>💼 Top {top_n} vùng xanh (Thanh khoản cao nhất):</b>"]
    for _, row in df.iterrows():
        ticker = row.get("ticker", "")
        crown  = "👑" if row.get("is_super_stock") else ""
        close  = _val(row, "close")
        bd     = fmt_date(row.get(date_col))
        buy_p  = _val(row, price_col)
        tk     = float(row.get("turnover") or 0)
        tk_str = f"{tk/1e9:.1f} tỷ" if tk > 0 else "–"

        pnl = round((float(close) - float(buy_p)) / float(buy_p) * 100, 2) \
              if close and buy_p and float(buy_p) > 0 else None
        pnl_str = f" | {pnl:+.1f}%" if pnl is not None else ""

        try:
            from datetime import date as _d, datetime as _dt
            _days = (_d.today() - _dt.strptime(bd, "%d/%m/%Y").date()).days if bd else None
            days_str = f" ({_days} ngày)" if _days is not None else ""
        except Exception:
            days_str = ""

        lines.append(f"  <b>{tv_link(ticker, exch_map.get(ticker, ''))}{crown}</b> | Giá {_fmt(close)} | TK {tk_str}{pnl_str} | Từ {bd}{days_str}")

    send_message("\n".join(lines), style=style)


