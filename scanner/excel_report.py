"""
Build Excel report with 4 sheets: All stocks, Signals, Backtest, Summary stats.
"""

from datetime import date

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from scanner.config import REPORTS_DIR
from scanner.utils import fmt_price, logger

# Colors
GREEN_FILL = PatternFill("solid", fgColor="C6EFCE")
RED_FILL = PatternFill("solid", fgColor="FFC7CE")
YELLOW_FILL = PatternFill("solid", fgColor="FFEB9C")
HEADER_FILL = PatternFill("solid", fgColor="4472C4")
HEADER_FONT = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)


def build_excel_report(
    results: pd.DataFrame,
    signals: dict[str, pd.DataFrame],
    scan_date: str | None = None,
    ai_analysis: dict | None = None,
    super_stocks: pd.DataFrame | None = None,
) -> dict[str, str]:
    """
    Dual mode  → 2 file riêng: report_long_*.xlsx và report_short_*.xlsx
    Single mode→ 1 file:        report_YYYY-MM-DD.xlsx
    Trả về dict {"long": path, "short": path} hoặc {"long": path}.
    """
    if scan_date is None:
        scan_date = date.today().strftime("%Y-%m-%d")

    # Gắn thông tin sàn giao dịch vào results
    if "exchange" not in results.columns:
        try:
            from scanner.database import db_cursor
            tickers = results["ticker"].tolist()
            with db_cursor(commit=False) as cur:
                cur.execute(
                    "SELECT ticker, exchange FROM watchlist WHERE ticker = ANY(%s)",
                    (tickers,),
                )
                exch_map = {r["ticker"]: r["exchange"] for r in cur.fetchall()}
            results = results.copy()
            results["exchange"] = results["ticker"].map(exch_map).fillna("")
        except Exception:
            results = results.copy()
            results["exchange"] = ""

    is_dual = "long_buy_signal" in results.columns
    paths: dict[str, str] = {}

    if is_dual:
        paths["long"]  = _save_workbook(
            _build_workbook(results, signals, "long",  scan_date, ai_analysis, super_stocks),
            REPORTS_DIR / f"report_long_{scan_date}.xlsx",
        )
        paths["short"] = _save_workbook(
            _build_workbook(results, signals, "short", scan_date, ai_analysis=None, super_stocks=None),
            REPORTS_DIR / f"report_short_{scan_date}.xlsx",
        )
    else:
        wb = Workbook()
        _sheet_signals(wb, signals, ai_analysis=ai_analysis)
        if super_stocks is not None and not super_stocks.empty:
            _sheet_super_stocks(wb, super_stocks, scan_date)
        if ai_analysis:
            _sheet_ai(wb, ai_analysis, scan_date)
        _sheet_all(wb, results)
        _sheet_stats(wb, results, scan_date, style=None)
        if "Sheet" in wb.sheetnames:
            del wb["Sheet"]
        paths["long"] = _save_workbook(wb, REPORTS_DIR / f"report_{scan_date}.xlsx")

    _cleanup_old_reports(keep_days=3)
    return paths


def _cleanup_old_reports(keep_days: int = 3) -> None:
    """Xóa file Excel cũ, chỉ giữ lại keep_days ngày gần nhất cho mỗi style."""
    import re
    pattern = re.compile(r"report(?:_(?:long|short))?_(\d{4}-\d{2}-\d{2})\.xlsx$")
    files: dict[str, list] = {}
    for f in REPORTS_DIR.glob("report*.xlsx"):
        m = pattern.match(f.name)
        if not m:
            continue
        day = m.group(1)
        # Nhóm theo prefix (long/short/single) để giữ đủ ngày cho mỗi style
        prefix = f.name.replace(f"_{day}.xlsx", "")
        files.setdefault(prefix, []).append((day, f))

    for prefix, items in files.items():
        items.sort(key=lambda x: x[0], reverse=True)  # mới nhất trước
        for day, f in items[keep_days:]:
            try:
                f.unlink()
                logger.info(f"Xoa report cu: {f.name}")
            except Exception:
                pass


def _build_workbook(
    results: pd.DataFrame,
    signals: dict[str, pd.DataFrame],
    style: str,
    scan_date: str,
    ai_analysis: dict | None = None,
    super_stocks: pd.DataFrame | None = None,
) -> "Workbook":
    """Tạo 1 Workbook hoàn chỉnh cho 1 style (long hoặc short)."""
    wb = Workbook()
    label = "Dài hạn" if style == "long" else "Ngắn hạn"

    _sheet_signals(wb, signals, ai_analysis=ai_analysis, style_filter=style)  # Tab 1
    _sheet_nam_giu(wb, results, style=style)                                    # Tab 2
    _sheet_dung_ngoai(wb, results, style=style)                                 # Tab 3
    _sheet_super_stocks(wb, results, scan_date)                                 # Tab 5 — dùng results trực tiếp

    if style == "long" and ai_analysis:
        _sheet_ai(wb, ai_analysis, scan_date)                                   # Tab 6 — chỉ DH

    _sheet_history(wb, results=results, style=style)                            # Tab cuối

    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    return wb


def _save_workbook(wb: "Workbook", path) -> str:
    """Lưu workbook, fallback timestamp nếu file đang mở."""
    try:
        wb.save(path)
    except PermissionError:
        from datetime import datetime
        ts = datetime.now().strftime("%H%M%S")
        path = path.parent / f"{path.stem}_{ts}{path.suffix}"
        wb.save(path)
        logger.warning(f"File goc bi khoa, luu thanh: {path.name}")
    logger.info(f"Excel report saved: {path}")
    return str(path)


def _sheet_all(wb: Workbook, df: pd.DataFrame) -> None:
    ws = wb.create_sheet("Tất cả cổ phiếu")

    criteria = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]
    crit_labels = ["EMA", "VWAP", "RSI", "MACD", "ADX", "OBV", "Stoch", "Nến", "Vol"]

    # Kiểm tra dual mode
    is_dual = "long_buy_signal" in df.columns

    if is_dual:
        headers = [
            "Mã", "Xu hướng DH", "Xu hướng NH",
            "Tín hiệu DH", "Tín hiệu NH", "Đồng thuận",
            "Lệnh HĐ", "Ngày vào lệnh", "Giá vào lệnh", "Giá hiện tại",
            "Giữ (phiên)", "Lời/Lỗ %",
            "BiasNorm", "Nhận xét", "bScore", "rScore",
        ] + crit_labels
    else:
        headers = [
            "Mã", "Xu hướng", "Tín hiệu mới",
            "Lệnh HĐ", "Ngày vào lệnh", "Giá vào lệnh", "Giá hiện tại",
            "Giữ (phiên)", "Lời/Lỗ %",
            "BiasNorm", "Nhận xét", "bScore", "rScore",
        ] + crit_labels
    _write_header(ws, headers)

    for i, (_, row) in enumerate(df.iterrows(), start=2):
        trend_str = "↑ TĂNG" if row.get("trend", 0) == 1 else "↓ GIẢM"
        pnl      = row.get("signal_pnl_pct") or row.get("pnl_pct")
        last_sig = row.get("last_signal_type", "")
        sig_date = row.get("last_signal_date")
        sig_price = row.get("last_signal_price")
        bars  = row.get("bars_since_signal")

        def _sig_str(buy_col, sell_col):
            if row.get(buy_col):  return "🟢 MUA"
            if row.get(sell_col): return "🔴 BÁN"
            return ""

        if is_dual:
            trend_dh = "↑" if row.get("long_trend",  0) == 1 else "↓"
            trend_nh = "↑" if row.get("short_trend", 0) == 1 else "↓"
            sig_dh   = _sig_str("long_buy_signal",  "long_sell_signal")
            sig_nh   = _sig_str("short_buy_signal", "short_sell_signal")
            both     = "🔥 CẢ 2" if row.get("both_buy") or row.get("both_sell") else ""
            base_row = [
                row.get("ticker", ""),
                trend_dh, trend_nh,
                sig_dh, sig_nh, both,
                "Nắm giữ" if last_sig == "MUA" else ("Đứng ngoài" if last_sig == "BÁN" else ""),
                str(sig_date)[:10] if sig_date else "",
                round(sig_price, 2) if sig_price else "",
                row.get("close", 0),
                bars if bars is not None else "",
                round(pnl, 2) if pnl is not None else "",
                round(row.get("bias_norm", 0), 1),
                row.get("bias_label", ""),
                row.get("b_score", 0),
                row.get("r_score", 0),
            ]
        else:
            new_sig = _sig_str("buy_signal", "sell_signal")
            base_row = [
                row.get("ticker", ""),
                trend_str,
                new_sig,
                "Nắm giữ" if last_sig == "MUA" else ("Đứng ngoài" if last_sig == "BÁN" else ""),
                str(sig_date)[:10] if sig_date else "",
                round(sig_price, 2) if sig_price else "",
                row.get("close", 0),
                bars if bars is not None else "",
                round(pnl, 2) if pnl is not None else "",
                round(row.get("bias_norm", 0), 1),
                row.get("bias_label", ""),
                row.get("b_score", 0),
                row.get("r_score", 0),
            ]
        for c in criteria:
            base_row.append("✓" if row.get(f"bull_{c}") else "")

        for j, val in enumerate(base_row, start=1):
            ws.cell(row=i, column=j, value=val)

        # Row color
        bias = row.get("bias_norm", 50)
        fill = None
        if row.get("buy_signal"):
            fill = GREEN_FILL
        elif row.get("sell_signal"):
            fill = RED_FILL
        elif bias >= 70:
            fill = YELLOW_FILL

        if fill:
            for j in range(1, len(headers) + 1):
                ws.cell(row=i, column=j).fill = fill

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


STAR_FILL   = PatternFill("solid", fgColor="FFD700")   # vàng
STRONG_FILL = PatternFill("solid", fgColor="00B050")   # xanh đậm


_SS_COLS   = ["ss1", "ss2", "ss3", "ss4", "ss5", "ss6", "ss7"]
_SS_LABELS = [
    "Gia>EMA200", "EMA200 Tang", "Gan dinh 52T",
    "EMA50>EMA200", "Gia>EMA50", "ADX>25", "Vol20>Vol60",
]
_SS_LIQTHRESH = 50e6   # 50 tỷ VND (avg_turnover_20d đơn vị VND/1000)


def _sheet_super_stocks(wb: Workbook, df: pd.DataFrame, scan_date: str) -> None:
    """
    Siêu cổ phiếu: score ≥ 5/7 + TK TB 20 phiên ≥ 50 tỷ, sort TK giảm dần.
    Cột: Mã | Giá | Score | TK TB 20p (tỷ) | ss1–ss7 (✓/✗)
    """
    ws = wb.create_sheet("Sieu co phieu (5-7 tieu chi)")

    headers = ["Ma", "Gia", "Score", "TK TB 20p (ty)"] + _SS_LABELS
    _write_header(ws, headers)

    # Load avg_turnover_20d từ watchlist
    try:
        from scanner.database import load_avg_turnover
        tk_map = load_avg_turnover(df["ticker"].tolist())
    except Exception:
        tk_map = {}

    rows_data = []
    for _, row in df.iterrows():
        raw_score = row.get("super_score")
        try:
            score = int(raw_score) if raw_score is not None and raw_score == raw_score else 0
        except (ValueError, TypeError):
            score = 0
        ticker  = row.get("ticker", "")
        avg_tk  = tk_map.get(ticker, 0)          # VND/1000
        tk_ty   = avg_tk / 1e6                   # → tỷ VND

        if score < 5 or avg_tk < _SS_LIQTHRESH:
            continue
        rows_data.append((avg_tk, score, tk_ty, row))

    rows_data.sort(key=lambda x: x[0], reverse=True)

    for i, (avg_tk, score, tk_ty, row) in enumerate(rows_data, start=2):
        vals = [
            row.get("ticker", ""),
            row.get("close", ""),
            f"{score}/7",
            round(tk_ty, 1),
        ] + ["✓" if row.get(c) else "✗" for c in _SS_COLS]

        for j, v in enumerate(vals, start=1):
            ws.cell(row=i, column=j, value=v)

        # Màu theo score
        fill = STAR_FILL if score == 7 else (STRONG_FILL if score >= 6 else GREEN_FILL)
        for j in range(1, len(headers) + 1):
            cell = ws.cell(row=i, column=j)
            cell.fill = fill
            if fill == STRONG_FILL:
                cell.font = Font(color="FFFFFF")

        # Tô từng ô ✓/✗ theo kết quả
        for j, c in enumerate(_SS_COLS, start=5):
            ws.cell(row=i, column=j).fill = GREEN_FILL if row.get(c) else RED_FILL

    note_row = len(rows_data) + 3
    ws.cell(row=note_row, column=1, value="Tieu chi: score>=5/7 va TK TB 20 phien >=50 ty VND").font = BOLD
    ws.cell(row=note_row + 1, column=1, value="7/7=Vang | 6/7=Xanh dam | 5/7=Xanh nhat").font = BOLD

    # ── Sắp thành Siêu cổ phiếu (score 3–4, top 5 TK) ───────────────────────
    ALMOST_FILL = PatternFill("solid", fgColor="FFF2CC")   # vàng nhạt

    almost_data = []
    for _, row in df.iterrows():
        raw_score = row.get("super_score")
        try:
            score = int(raw_score) if raw_score is not None and raw_score == raw_score else 0
        except (ValueError, TypeError):
            score = 0
        ticker = row.get("ticker", "")
        avg_tk = tk_map.get(ticker, 0)
        if 3 <= score <= 4:
            almost_data.append((avg_tk, score, avg_tk / 1e6, row))

    almost_data.sort(key=lambda x: x[0], reverse=True)
    almost_top5 = almost_data[:5]

    if almost_top5:
        sep_row = note_row + 3
        cell = ws.cell(row=sep_row, column=1, value="⏳ Sap thanh Sieu co phieu (score 3-4/7, top 5 TK)")
        cell.font = Font(bold=True, size=11)
        cell.fill = ALMOST_FILL

        hdr_row = sep_row + 1
        for j, h in enumerate(headers, start=1):
            c = ws.cell(row=hdr_row, column=j, value=h)
            c.fill = PatternFill("solid", fgColor="FFE699")
            c.font = Font(bold=True)

        for k, (avg_tk, score, tk_ty, row) in enumerate(almost_top5, start=hdr_row + 1):
            vals = [
                row.get("ticker", ""),
                row.get("close", ""),
                f"{score}/7",
                round(tk_ty, 1),
            ] + ["✓" if row.get(c) else "✗" for c in _SS_COLS]

            for j, v in enumerate(vals, start=1):
                ws.cell(row=k, column=j, value=v).fill = ALMOST_FILL

            for j, c in enumerate(_SS_COLS, start=5):
                ws.cell(row=k, column=j).fill = GREEN_FILL if row.get(c) else _LIGHT_RED

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _sheet_ai(wb: Workbook, ai_analysis: dict, scan_date: str) -> None:
    """Sheet phân tích AI: overview + bảng từng tín hiệu."""
    ws = wb.create_sheet("Phan tich AI")

    WRAP = Alignment(wrap_text=True, vertical="top")
    AI_FILL = PatternFill("solid", fgColor="EAF4FB")

    # --- Tổng quan thị trường ---
    ws.cell(row=1, column=1, value="NHAN DINH TONG QUAN THI TRUONG").font = Font(bold=True, size=13)
    ws.cell(row=1, column=1).fill = PatternFill("solid", fgColor="2F75B6")
    ws.cell(row=1, column=1).font = Font(bold=True, size=13, color="FFFFFF")
    ws.merge_cells("A1:D1")

    overview = ai_analysis.get("overview", "")
    ws.cell(row=2, column=1, value=overview).alignment = WRAP
    ws.merge_cells("A2:D2")
    ws.row_dimensions[2].height = max(60, len(overview) // 5)

    # --- Tín hiệu MUA ---
    buy_signals = ai_analysis.get("buy_signals", [])
    sell_signals = ai_analysis.get("sell_signals", [])

    row = 4
    for section_label, section_fill, signals_list in [
        ("PHAN TICH TIN HIEU MUA", "C6EFCE", buy_signals),
        ("PHAN TICH TIN HIEU BAN", "FFC7CE", sell_signals),
    ]:
        if not signals_list:
            continue
        ws.cell(row=row, column=1, value=section_label).font = Font(bold=True, color="FFFFFF")
        ws.cell(row=row, column=1).fill = PatternFill("solid", fgColor="375623" if "MUA" in section_label else "9C0006")
        ws.merge_cells(f"A{row}:D{row}")
        row += 1

        header_row = row
        for j, h in enumerate(["Ma", "Nhan dinh AI", "Scan date"], start=1):
            c = ws.cell(row=header_row, column=j, value=h)
            c.fill = HEADER_FILL
            c.font = HEADER_FONT
        row += 1

        for item in signals_list:
            ws.cell(row=row, column=1, value=item.get("ticker", "")).font = Font(bold=True)
            cell = ws.cell(row=row, column=2, value=item.get("analysis", ""))
            cell.alignment = WRAP
            cell.fill = AI_FILL
            ws.cell(row=row, column=3, value=scan_date)
            ws.row_dimensions[row].height = 40
            row += 1

        row += 1  # khoảng cách giữa section

    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 80
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14


def _sheet_signals(wb: Workbook, signals: dict, ai_analysis: dict | None = None, style_filter: str | None = None) -> None:
    """
    Tab đầu tiên: Tín hiệu trong ngày.
    Cột: Mã | Tín hiệu | Khung | Ngày mua | Giá đóng cửa | Giá ST | TK (tỷ) | BiasNorm | bScore | Nhận xét AI
    """
    style_label = {"long": " Dài hạn", "short": " Ngắn hạn"}.get(style_filter or "", "")
    ws = wb.create_sheet(f"Tin hieu trong ngay{style_label}")

    today_str = date.today().strftime("%d/%m/%Y")

    headers = [
        "Ma", "Tin hieu", "Khung",
        "Ngay mua", "Gia mua/ban (ST)",
        "TK (ty VND)", "BiasNorm",
    ]
    _write_header(ws, headers)

    is_dual = any("long_buy_signal" in df.columns for df in signals.values() if not df.empty)

    # (signal_label, buy_col, sell_col, khung, style_key, st_col)
    all_combos = [
        ("But pha xac nhan", "long_buy_signal",   "long_sell_signal",  "Dai han",  "long",  "long_supertrend"),
        ("Dao chieu giam",   "long_sell_signal",  "long_buy_signal",   "Dai han",  "long",  "long_supertrend"),
        ("But pha xac nhan", "short_buy_signal",  "short_sell_signal", "Ngan han", "short", "short_supertrend"),
        ("Dao chieu giam",   "short_sell_signal", "short_buy_signal",  "Ngan han", "short", "short_supertrend"),
    ]
    combos = [c for c in all_combos if style_filter is None or c[4] == style_filter]

    # Thu thập tất cả rows trước, sort TK cao → thấp, rồi mới ghi
    collected: list[tuple] = []  # (turnover, ticker_key, row, signal_label, khung, st_col, fill)
    seen: set[str] = set()

    def _collect(row, signal_label, khung, st_col, fill):
        ticker = row.get("ticker", "")
        key    = f"{ticker}_{signal_label}_{khung}"
        if key in seen:
            return
        seen.add(key)
        tk = float(row.get("turnover") or 0)
        collected.append((tk, key, row, signal_label, khung, st_col, fill))

    if is_dual:
        for signal_label, buy_col, _sell_col, khung, _style, st_col in combos:
            is_buy = "But pha" in signal_label
            src    = signals.get("buy" if is_buy else "sell", pd.DataFrame())
            if src.empty or buy_col not in src.columns:
                continue
            fill = GREEN_FILL if is_buy else RED_FILL
            for _, row in src[src[buy_col].astype(bool)].iterrows():
                _collect(row, signal_label, khung, st_col, fill)
    else:
        for df, label, fill, st_col in [
            (signals.get("buy",  pd.DataFrame()), "But pha xac nhan", GREEN_FILL, "supertrend"),
            (signals.get("sell", pd.DataFrame()), "Dao chieu giam",   RED_FILL,   "supertrend"),
        ]:
            for _, row in df.iterrows():
                _collect(row, label, "Dai han", st_col, fill)

    collected.sort(key=lambda x: x[0], reverse=True)

    row_idx = 2
    for tk, _key, row, signal_label, khung, st_col, fill in collected:
        st = row.get(st_col) or row.get("supertrend") or ""
        vals = [
            row.get("ticker", ""),
            signal_label,
            khung,
            today_str,
            round(float(st), 2) if st else "",
            round(tk / 1e9, 1) if tk else "",
            round(row.get("bias_norm", 0), 1),
        ]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=row_idx, column=j, value=v).fill = fill
        row_idx += 1

    # Điều chỉnh chiều cao dòng cho cột AI
    for r in range(2, row_idx):
        ws.row_dimensions[r].height = 40

    ws.column_dimensions["J"].width = 60  # cột Nhận xét AI rộng hơn
    _auto_width(ws)
    ws.column_dimensions["J"].width = max(ws.column_dimensions["J"].width, 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions



def _sheet_nam_giu(wb: Workbook, results: pd.DataFrame, style: str = "long") -> None:
    """Vùng xanh: long_trend=1, ngày/giá mua từ SuperTrend flip gần nhất, sort TK↓."""
    ws = wb.create_sheet("Vung xanh (Nam giu)")
    headers = ["Ma", "Ngay Mua", "Giu Lenh (ngay)", "Gia Mua", "Gia Hien Tai", "Loi/Lo %", "TK (ty)"]
    _write_header(ws, headers)

    p         = f"{style}_"
    trend_col = f"{p}trend" if f"{p}trend" in results.columns else "trend"
    date_col  = f"{p}last_signal_date"  if f"{p}last_signal_date"  in results.columns else "last_signal_date"
    price_col = f"{p}last_signal_price" if f"{p}last_signal_price" in results.columns else "last_signal_price"

    df = results[results.get(trend_col, results.get("trend", pd.Series(dtype=int))) == 1].copy()
    if "turnover" in df.columns:
        df = df.sort_values("turnover", ascending=False)

    for i, (_, row) in enumerate(df.iterrows(), start=2):
        ticker = row.get("ticker", "")
        close  = float(row.get("close") or 0)
        tk     = float(row.get("turnover") or 0)
        bd     = str(row.get(date_col)  or "")[:10]
        buy_p  = float(row.get(price_col) or 0)

        try:
            hold = (date.today() - pd.to_datetime(bd).date()).days if bd else ""
        except Exception:
            hold = ""

        pnl     = round((close - buy_p) / buy_p * 100, 2) if buy_p > 0 and close > 0 else None
        pnl_str = f"{pnl:+.2f}%" if pnl is not None else ""

        vals = [ticker, bd, hold, buy_p or "", close or "", pnl_str, round(tk / 1e9, 1) if tk else ""]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=i, column=j, value=v)
        fill = GREEN_FILL if (pnl is None or pnl >= 0) else YELLOW_FILL
        for j in range(1, len(headers) + 1):
            ws.cell(row=i, column=j).fill = fill

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


_LIGHT_RED  = PatternFill("solid", fgColor="FFD7D7")   # đỏ nhạt cho toàn bộ vùng đỏ
_LIGHT_GREEN = PatternFill("solid", fgColor="C6EFCE")  # xanh nhạt khi tránh được lỗ


def _sheet_dung_ngoai(wb: Workbook, results: pd.DataFrame, style: str = "long") -> None:
    """Vùng đỏ: long_trend=-1, ngày/giá bán từ SuperTrend flip gần nhất, sort TK↓."""
    ws = wb.create_sheet("Vung do (Dung ngoai)")
    # Cột 6 = "Tranh lo": âm % nếu giá giảm sau bán (đúng), dương % nếu giá tăng (sai)
    headers = ["Ma", "Ngay Ban", "Dung Ngoai (ngay)", "Gia Ban", "Gia Hien Tai", "Tranh lo", "TK (ty)"]
    _write_header(ws, headers)

    p         = f"{style}_"
    trend_col = f"{p}trend" if f"{p}trend" in results.columns else "trend"
    date_col  = f"{p}last_signal_date"  if f"{p}last_signal_date"  in results.columns else "last_signal_date"
    price_col = f"{p}last_signal_price" if f"{p}last_signal_price" in results.columns else "last_signal_price"

    df = results[results.get(trend_col, results.get("trend", pd.Series(dtype=int))) == -1].copy()
    if "turnover" in df.columns:
        df = df.sort_values("turnover", ascending=False)

    pnl_col_idx = 6  # cột "Tranh lo" (1-indexed)

    for i, (_, row) in enumerate(df.iterrows(), start=2):
        ticker = row.get("ticker", "")
        close  = float(row.get("close") or 0)
        tk     = float(row.get("turnover") or 0)
        sd     = str(row.get(date_col)  or "")[:10]
        sell_p = float(row.get(price_col) or 0)

        try:
            hold = (date.today() - pd.to_datetime(sd).date()).days if sd else ""
        except Exception:
            hold = ""

        # pnl = % thay đổi từ giá bán đến giá hiện tại
        # Âm = giá giảm sau bán → đúng quyết định (tránh được lỗ)
        # Dương = giá tăng sau bán → sai quyết định
        pnl = round((close - sell_p) / sell_p * 100, 2) if sell_p > 0 and close > 0 else None
        pnl_str = f"{pnl:+.2f}%" if pnl is not None else ""

        vals = [ticker, sd, hold, sell_p or "", close or "", pnl_str, round(tk / 1e9, 1) if tk else ""]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=i, column=j, value=v)

        # Toàn hàng: đỏ nhạt
        for j in range(1, len(headers) + 1):
            ws.cell(row=i, column=j).fill = _LIGHT_RED

        # Cột "Tránh lỗ": xanh nhạt nếu giá giảm (tránh được lỗ), đỏ nhạt nếu giá tăng
        if pnl is not None:
            ws.cell(row=i, column=pnl_col_idx).fill = _LIGHT_GREEN if pnl <= 0 else _LIGHT_RED

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _sheet_stats(wb: Workbook, df: pd.DataFrame, scan_date: str, style: str | None = None) -> None:
    label = {"long": " Dài hạn", "short": " Ngắn hạn"}.get(style or "", "")
    ws = wb.create_sheet(f"Thống kê{label}")

    def write_kv(r, key, val):
        ws.cell(row=r, column=1, value=key).font = BOLD
        ws.cell(row=r, column=2, value=val)

    is_dual = "long_buy_signal" in df.columns
    p = f"{style}_" if style else ""
    if is_dual and style:
        buy_count  = int(df[f"{p}buy_signal"].sum())  if f"{p}buy_signal"  in df.columns else 0
        sell_count = int(df[f"{p}sell_signal"].sum()) if f"{p}sell_signal" in df.columns else 0
        both_count = 0
    elif is_dual:
        buy_count  = int((df["long_buy_signal"]  | df["short_buy_signal"]).sum())
        sell_count = int((df["long_sell_signal"] | df["short_sell_signal"]).sum())
        both_count = int((df.get("both_buy", False) | df.get("both_sell", False)).sum())
    else:
        buy_count  = int(df["buy_signal"].sum())  if "buy_signal"  in df.columns else 0
        sell_count = int(df["sell_signal"].sum()) if "sell_signal" in df.columns else 0
        both_count = 0
    avg_bias = round(df["bias_norm"].mean(), 1) if "bias_norm" in df.columns else 0

    write_kv(1, "Ngày quét", scan_date)
    write_kv(2, "Tổng mã quét", len(df))
    write_kv(3, "Tín hiệu MUA", buy_count)
    write_kv(4, "Tín hiệu BÁN", sell_count)
    if is_dual:
        write_kv(5, "Đồng thuận cả 2", both_count)
    write_kv(6, "BiasNorm trung bình", avg_bias)

    # BiasNorm distribution
    ws.cell(row=7, column=1, value="Phân phối BiasNorm").font = BOLD
    buckets = [(0, 25, "Rất yếu"), (25, 45, "Yếu"), (45, 55, "Trung tính"), (55, 75, "Mạnh"), (75, 100, "Rất mạnh")]
    for r, (lo, hi, label) in enumerate(buckets, start=8):
        count = int(((df["bias_norm"] >= lo) & (df["bias_norm"] < hi)).sum())
        ws.cell(row=r, column=1, value=f"{label} ({lo}-{hi})")
        ws.cell(row=r, column=2, value=count)

    # Top 5 mạnh nhất (bias_norm)
    ws.cell(row=14, column=1, value="Top 5 mạnh nhất (BiasNorm)").font = BOLD
    top5 = df.nlargest(5, "bias_norm")[["ticker", "bias_norm"]]
    for r, (_, tr) in enumerate(top5.iterrows(), start=15):
        ws.cell(row=r, column=1, value=tr["ticker"])
        ws.cell(row=r, column=2, value=round(tr["bias_norm"], 1))

    # Top 10 mạnh + thanh khoản cao
    ws.cell(row=21, column=1, value="Top 10 Mạnh + Thanh khoản cao").font = BOLD
    top10_headers = ["Mã", "BiasNorm", "TK (tỷ)", "Xu hướng DH", "Xu hướng NH"]
    for j, h in enumerate(top10_headers, start=1):
        cell = ws.cell(row=22, column=j, value=h)
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT

    if "turnover" in df.columns and "bias_norm" in df.columns:
        tmp = df.copy()
        tmp["turnover"] = pd.to_numeric(tmp["turnover"], errors="coerce").fillna(0)
        tmp["bias_norm"] = pd.to_numeric(tmp["bias_norm"], errors="coerce").fillna(0)

        # Normalize cả 2 về [0,1] rồi lấy trung bình
        max_tk = tmp["turnover"].max() or 1
        max_bn = tmp["bias_norm"].max() or 1
        tmp["_score"] = (tmp["bias_norm"] / max_bn) * 0.6 + (tmp["turnover"] / max_tk) * 0.4
        top10 = tmp.nlargest(10, "_score")

        for r, (_, tr) in enumerate(top10.iterrows(), start=23):
            tk_ty = round(tr["turnover"] / 1e9, 1) if tr["turnover"] else ""
            trend_dh = "↑" if tr.get("long_trend", 0) == 1 else "↓"
            trend_nh = "↑" if tr.get("short_trend", 0) == 1 else "↓"
            vals = [tr["ticker"], round(tr["bias_norm"], 1), tk_ty, trend_dh, trend_nh]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=r, column=j, value=v)
            fill = GREEN_FILL if tr.get("long_trend", 0) == 1 else RED_FILL
            for j in range(1, 6):
                ws.cell(row=r, column=j).fill = fill

    _auto_width(ws)



OPEN_FILL   = PatternFill("solid", fgColor="DDEBF7")   # xanh nhạt — đang giữ
CLOSED_FILL = PatternFill("solid", fgColor="F2F2F2")   # xám nhạt — đã đóng


def _sheet_history(wb: Workbook, results: pd.DataFrame, style: str = "long") -> None:
    """Tab lịch sử lệnh: 1 giao dịch gần nhất/mã (MUA→BÁN), sort theo TK cao→thấp."""
    ws = wb.create_sheet("Lich su lenh")

    headers = [
        "Ma", "Ngay Mua", "Gia Mua",
        "Ngay Ban", "Gia Ban",
        "Loi/Lo %", "Trang Thai", "TK (ty)",
    ]
    _write_header(ws, headers)

    tickers = results["ticker"].tolist() if "ticker" in results.columns else []
    tk_map  = results.set_index("ticker")["turnover"].to_dict() if "turnover" in results.columns else {}
    close_map = results.set_index("ticker")["close"].to_dict() if "close" in results.columns else {}

    # Lấy lịch sử từ DB — chỉ với các mã trong scan hiện tại
    trade_df = pd.DataFrame()
    try:
        from scanner.database import load_trade_history
        trade_df = load_trade_history(style=style, tickers=tickers or None)
    except Exception as e:
        logger.warning(f"_sheet_history: load_trade_history failed: {e}")

    if trade_df.empty:
        ws.cell(row=2, column=1, value="Chua co du lieu lich su lenh (signals table trong)")
        return

    # Gắn turnover → sort TK cao → thấp
    trade_df["_tk"] = trade_df["ticker"].map(tk_map).fillna(0)
    trade_df = trade_df.sort_values("_tk", ascending=False).reset_index(drop=True)

    for i, (_, row) in enumerate(trade_df.iterrows(), start=2):
        ticker     = row.get("ticker", "")
        buy_date   = str(row.get("buy_date")  or "")[:10]
        sell_date  = str(row.get("sell_date") or "")[:10] if pd.notna(row.get("sell_date")) else ""
        buy_price  = row.get("buy_price")
        sell_price = row.get("sell_price")
        status     = row.get("status", "")
        tk_ty      = round(float(row.get("_tk") or 0) / 1e9, 1)

        # P&L: đã đóng → dùng sell_price; đang giữ → dùng close hiện tại
        pnl = row.get("pnl_pct")
        if status == "Dang giu":
            cur_close = close_map.get(ticker)
            if cur_close and buy_price and float(buy_price) > 0:
                pnl = round((float(cur_close) - float(buy_price)) / float(buy_price) * 100, 2)

        try:
            pnl = round(float(pnl), 2) if pnl is not None else None
        except Exception:
            pnl = None

        pnl_str    = f"{pnl:+.2f}%" if pnl is not None else ""
        status_str = "Dang giu" if status == "Dang giu" else "Da dong"

        vals = [
            ticker,
            buy_date,
            round(float(buy_price), 2)  if buy_price  else "",
            sell_date,
            round(float(sell_price), 2) if sell_price else "",
            pnl_str,
            status_str,
            tk_ty or "",
        ]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=i, column=j, value=v)

        # Màu hàng
        if status == "Dang giu":
            row_fill = GREEN_FILL if (pnl is None or pnl >= 0) else RED_FILL
            for j in range(1, len(headers) + 1):
                ws.cell(row=i, column=j).fill = row_fill
        else:
            for j in range(1, len(headers) + 1):
                ws.cell(row=i, column=j).fill = CLOSED_FILL
            # Cột P&L tô riêng theo lời/lỗ
            ws.cell(row=i, column=6).fill = GREEN_FILL if (pnl is not None and pnl >= 0) else RED_FILL

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _write_header(ws, headers: list) -> None:
    for j, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=j, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")


ORANGE_FILL  = PatternFill("solid", fgColor="FCE4D6")
BLUE_FILL    = PatternFill("solid", fgColor="DDEBF7")


def _sheet_positions(wb: Workbook, df: pd.DataFrame) -> None:
    """Sheet vị thế đang mở — tất cả mã đang nắm giữ (có buy_date, chưa bán)."""
    ws = wb.create_sheet("Vị thế đang mở")

    # Lọc mã đang giữ: có buy_date và trend vẫn còn bullish (hoặc đang chờ)
    pos = df[df["buy_date"].notna()].copy() if "buy_date" in df.columns else pd.DataFrame()

    if pos.empty:
        ws.cell(row=1, column=1, value="Không có vị thế đang mở")
        return

    headers = [
        "Mã", "Xu hướng", "Ngày mua", "Giá mua",
        "Giá hiện tại", "Lời/Lỗ %", "Lời/Lỗ VND",
        "Hỗ trợ (Stop)", "Tránh lỗ tối đa %",
        "Kháng cự (Target)", "Tiềm năng lãi %",
        "BiasNorm", "Nhận xét",
    ]
    _write_header(ws, headers)

    # Tóm tắt tổng vị thế ở cuối
    total_pnl_pct = []

    for i, (_, row) in enumerate(pos.iterrows(), start=2):
        close     = row.get("close", 0) or 0
        buy_price = row.get("buy_price") or 0
        support   = row.get("support", 0) or 0
        resist    = row.get("resistance", 0) or 0
        pnl_pct   = row.get("pnl_pct")
        trend_str = "↑ TĂNG" if row.get("trend", 0) == 1 else "↓ GIẢM"

        # Tránh lỗ tối đa % = khoảng cách từ giá mua xuống stop loss
        max_loss_pct = round((buy_price - support) / buy_price * 100, 2) if buy_price and support else None

        # Tiềm năng lãi % = khoảng cách từ giá hiện tại lên kháng cự
        upside_pct = round((resist - close) / close * 100, 2) if close and resist else None

        # Lời/lỗ VND (giả sử 1 đơn vị)
        pnl_vnd = round((close - buy_price) / buy_price * 100, 2) if buy_price else None

        vals = [
            row.get("ticker", ""),
            trend_str,
            str(row.get("buy_date", ""))[:10],
            buy_price,
            close,
            pnl_pct,
            pnl_vnd,
            support,
            f"-{max_loss_pct}%" if max_loss_pct else "",
            resist,
            f"+{upside_pct}%" if upside_pct else "",
            round(row.get("bias_norm", 0), 1),
            row.get("bias_label", ""),
        ]

        for j, v in enumerate(vals, start=1):
            ws.cell(row=i, column=j, value=v)

        # Màu theo lời/lỗ
        if pnl_pct is not None:
            fill = GREEN_FILL if pnl_pct >= 0 else RED_FILL
            total_pnl_pct.append(pnl_pct)
        else:
            fill = BLUE_FILL

        for j in range(1, len(headers) + 1):
            ws.cell(row=i, column=j).fill = fill

    # Dòng tóm tắt cuối
    if total_pnl_pct:
        summary_row = len(pos) + 3
        ws.cell(row=summary_row, column=1, value=f"Tổng {len(pos)} vị thế").font = BOLD
        ws.cell(row=summary_row, column=6,
                value=round(sum(total_pnl_pct) / len(total_pnl_pct), 2)).font = BOLD
        ws.cell(row=summary_row - 1, column=6, value="Lời/Lỗ TB %").font = BOLD

    _auto_width(ws)
    ws.freeze_panes = "A2"


def _sheet_style(wb: Workbook, df: pd.DataFrame, style: str, title: str) -> None:
    """Sheet riêng cho 1 style (long hoặc short) trong dual mode."""
    ws = wb.create_sheet(title)
    p = f"{style}_"  # prefix: long_ hoặc short_

    criteria = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]
    crit_labels = ["EMA", "VWAP", "RSI", "MACD", "ADX", "OBV", "Stoch", "Nến", "Vol"]

    headers = [
        "Mã", "Xu hướng", "Tín hiệu",
        "Lệnh HĐ", "Ngày vào lệnh", "Giá vào lệnh", "Giá hiện tại",
        "Giữ (phiên)", "Lời/Lỗ %",
        "TK (tỷ VND)", "BiasNorm", "Nhận xét", "bScore", "rScore",
    ] + crit_labels
    _write_header(ws, headers)

    # Sort: tín hiệu trước, rồi bias_norm
    buy_col  = f"{p}buy_signal"
    sell_col = f"{p}sell_signal"
    df_sorted = df.copy()
    df_sorted["_r"] = 1
    if buy_col  in df.columns: df_sorted.loc[df[buy_col],  "_r"] = 0
    if sell_col in df.columns: df_sorted.loc[df[sell_col], "_r"] = 0
    df_sorted = df_sorted.sort_values(["_r", "bias_norm"], ascending=[True, False])

    for i, (_, row) in enumerate(df_sorted.iterrows(), start=2):
        trend_val = row.get(f"{p}trend", 0)
        trend_str = "↑ TĂNG" if trend_val == 1 else "↓ GIẢM"
        sig_str   = "🟢 MUA" if row.get(buy_col) else ("🔴 BÁN" if row.get(sell_col) else "")

        last_sig   = row.get(f"{p}last_signal_type", "")
        sig_date   = row.get(f"{p}last_signal_date")
        sig_price  = row.get(f"{p}last_signal_price")
        bars       = row.get(f"{p}bars_since_signal")
        pnl        = row.get(f"{p}signal_pnl_pct")

        tk = row.get("turnover", 0)
        tk_ty = round(tk / 1e9, 1) if tk else ""  # đổi sang tỷ VND

        base_row = [
            row.get("ticker", ""),
            trend_str,
            sig_str,
            "Nắm giữ" if last_sig == "MUA" else ("Đứng ngoài" if last_sig == "BÁN" else ""),
            str(sig_date)[:10] if sig_date else "",
            round(sig_price, 2) if sig_price else "",
            row.get("close", 0),
            bars if bars is not None else "",
            round(pnl, 2) if pnl is not None else "",
            tk_ty,
            round(row.get("bias_norm", 0), 1),
            row.get("bias_label", ""),
            row.get("b_score", 0),
            row.get("r_score", 0),
        ]
        for c in criteria:
            base_row.append("✓" if row.get(f"bull_{c}") else "")

        for j, val in enumerate(base_row, start=1):
            ws.cell(row=i, column=j, value=val)

        # Màu theo lệnh hiện tại: xanh = đang nắm giữ (MUA), đỏ = đứng ngoài (BÁN/chưa vào)
        if last_sig == "MUA":
            fill = GREEN_FILL
        elif last_sig == "BÁN":
            fill = RED_FILL
        else:
            fill = None

        if fill:
            for j in range(1, len(headers) + 1):
                ws.cell(row=i, column=j).fill = fill

        # Tô đậm hơn nếu hôm nay có tín hiệu mới
        if row.get(buy_col) or row.get(sell_col):
            NEW_BUY  = PatternFill("solid", fgColor="00B050")  # xanh đậm
            NEW_SELL = PatternFill("solid", fgColor="FF0000")  # đỏ đậm
            accent = NEW_BUY if row.get(buy_col) else NEW_SELL
            for j in range(1, 4):  # chỉ tô 3 cột đầu để phân biệt
                ws.cell(row=i, column=j).fill = accent

    _auto_width(ws)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def _sheet_dong_thuan(wb: Workbook, df: pd.DataFrame) -> None:
    """Sheet các mã có tín hiệu đồng thuận cả ngắn hạn lẫn dài hạn."""
    ws = wb.create_sheet("🔥 Đồng thuận cả 2")

    both = df[(df.get("both_buy", False) == True) | (df.get("both_sell", False) == True)].copy()
    if both.empty:
        ws.cell(row=1, column=1, value="Không có mã nào đồng thuận cả 2 style hôm nay")
        return

    headers = ["Mã", "Loại", "Giá HT", "Lời/Lỗ DH%", "Lời/Lỗ NH%", "BiasNorm", "Nhận xét"]
    _write_header(ws, headers)

    for i, (_, row) in enumerate(both.iterrows(), start=2):
        loai = "🟢 MUA" if row.get("both_buy") else "🔴 BÁN"
        vals = [
            row.get("ticker", ""),
            loai,
            row.get("close", 0),
            round(row.get("long_signal_pnl_pct",  0), 2) if row.get("long_signal_pnl_pct")  else "",
            round(row.get("short_signal_pnl_pct", 0), 2) if row.get("short_signal_pnl_pct") else "",
            round(row.get("bias_norm", 0), 1),
            row.get("bias_label", ""),
        ]
        for j, v in enumerate(vals, start=1):
            ws.cell(row=i, column=j, value=v)
        fill = GREEN_FILL if row.get("both_buy") else RED_FILL
        for j in range(1, len(headers) + 1):
            ws.cell(row=i, column=j).fill = fill

    _auto_width(ws)


def _auto_width(ws, max_width: int = 30) -> None:
    for col in ws.columns:
        length = max(
            len(str(cell.value)) if cell.value is not None else 0
            for cell in col
        )
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(length + 3, max_width)
