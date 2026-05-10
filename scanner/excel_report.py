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
) -> str:
    """Create reports/report_YYYY-MM-DD.xlsx. Returns file path string."""
    if scan_date is None:
        scan_date = date.today().strftime("%Y-%m-%d")

    path = REPORTS_DIR / f"report_{scan_date}.xlsx"
    wb = Workbook()
    is_dual = "long_buy_signal" in results.columns

    _sheet_signals(wb, signals)   # Tab đầu tiên

    if is_dual:
        _sheet_style(wb, results, style="short", title="Ngắn hạn (Mua lấy vị thế sớm)")
        _sheet_style(wb, results, style="long",  title="Dài hạn (Giữ cổ phiếu lâu hơn)")
    else:
        _sheet_all(wb, results)

    _sheet_stats(wb, results, scan_date)

    # Backtest: tạm tắt, chạy riêng bằng run_backtest.py
    pass

    # Remove default empty sheet
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    # Thử lưu, nếu file đang mở thì thêm timestamp vào tên
    try:
        wb.save(path)
    except PermissionError:
        from datetime import datetime
        ts = datetime.now().strftime("%H%M%S")
        path = REPORTS_DIR / f"report_{scan_date}_{ts}.xlsx"
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


def _sheet_signals(wb: Workbook, signals: dict) -> None:
    ws = wb.create_sheet("Tín hiệu")
    headers = [
        "Mã", "Loại tín hiệu", "Khung", "Đồng thuận", "Giá HT",
        "Ngày vào lệnh", "Giá vào lệnh",
        "TK (tỷ VND)", "BiasNorm", "Nhận xét", "bScore",
    ]
    _write_header(ws, headers)

    is_dual = any("long_buy_signal" in df.columns
                  for df in signals.values() if not df.empty)

    row_idx = 2
    # Dual mode: lấy từ long và short signals
    seen: set[str] = set()
    for signal_label, buy_col, sell_col, khung in [
        ("MUA DH", "long_buy_signal",  "long_sell_signal",  "Dài hạn"),
        ("BÁN DH", "long_sell_signal", "long_buy_signal",   "Dài hạn"),
        ("MUA NH", "short_buy_signal", "short_sell_signal", "Ngắn hạn"),
        ("BÁN NH", "short_sell_signal","short_buy_signal",  "Ngắn hạn"),
    ] if is_dual else []:
        src = signals.get("buy", pd.DataFrame()) if "MUA" in signal_label else signals.get("sell", pd.DataFrame())
        if src.empty or buy_col not in src.columns:
            continue
        subset = src[src[buy_col] == True]
        fill = GREEN_FILL if "MUA" in signal_label else RED_FILL
        for _, row in subset.iterrows():
            key = f"{row.get('ticker')}_{signal_label}"
            if key in seen:
                continue
            seen.add(key)
            last_sig  = row.get(f"{'long' if 'DH' in signal_label else 'short'}_last_signal_type", "")
            sig_date  = row.get(f"{'long' if 'DH' in signal_label else 'short'}_last_signal_date")
            sig_price = row.get(f"{'long' if 'DH' in signal_label else 'short'}_last_signal_price")
            tk = row.get("turnover", 0)
            both = "🔥 CẢ 2" if row.get("both_buy") or row.get("both_sell") else ""
            vals = [
                row.get("ticker", ""),
                signal_label,
                khung,
                both,
                row.get("close", 0),
                str(sig_date)[:10] if sig_date else "",
                round(sig_price, 2) if sig_price else "",
                round(tk / 1e9, 1) if tk else "",
                round(row.get("bias_norm", 0), 1),
                row.get("bias_label", ""),
                row.get("b_score", 0),
            ]
            for j, v in enumerate(vals, start=1):
                ws.cell(row=row_idx, column=j, value=v).fill = fill
            row_idx += 1

    # Single mode fallback
    if not is_dual:
        for signal_type, df in [("MUA", signals.get("buy", pd.DataFrame())),
                                  ("BÁN", signals.get("sell", pd.DataFrame()))]:
            if df.empty:
                continue
            fill = GREEN_FILL if signal_type == "MUA" else RED_FILL
            for _, row in df.iterrows():
                sig_date  = row.get("last_signal_date")
                sig_price = row.get("last_signal_price")
                tk = row.get("turnover", 0)
                vals = [
                    row.get("ticker", ""),
                    signal_type,
                    "Dài hạn",
                    "",
                    row.get("close", 0),
                    str(sig_date)[:10] if sig_date else "",
                    round(sig_price, 2) if sig_price else "",
                    round(tk / 1e9, 1) if tk else "",
                    round(row.get("bias_norm", 0), 1),
                    row.get("bias_label", ""),
                    row.get("b_score", 0),
                ]
                for j, v in enumerate(vals, start=1):
                    ws.cell(row=row_idx, column=j, value=v).fill = fill
                row_idx += 1

    _auto_width(ws)
    ws.freeze_panes = "A2"


def _sheet_stats(wb: Workbook, df: pd.DataFrame, scan_date: str) -> None:
    ws = wb.create_sheet("Thống kê")

    def write_kv(r, key, val):
        ws.cell(row=r, column=1, value=key).font = BOLD
        ws.cell(row=r, column=2, value=val)

    is_dual = "long_buy_signal" in df.columns
    if is_dual:
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


def _sheet_backtest(wb: Workbook, df: pd.DataFrame) -> None:
    ws = wb.create_sheet("Backtest")
    if df.empty:
        return

    # Mapping tên cột tiếng Anh → tiếng Việt
    col_map = {
        "ticker":           "Mã",
        "avg_tk_20p_ty":    "TK TB 20 phiên (tỷ)",
        "style":            "Khung",
        "total_trades":     "Tổng lệnh",
        "win_trades":       "Lệnh thắng",
        "loss_trades":      "Lệnh thua",
        "win_rate":         "Tỷ lệ thắng %",
        "total_return_pct": "Lợi nhuận %",
        "max_drawdown_pct": "Drawdown tối đa %",
        "avg_hold_days":    "Giữ TB (ngày)",
        "final_capital":    "Vốn cuối (VND)",
        "initial_capital":  "Vốn đầu (VND)",
        "buy_date":         "Ngày mua",
        "buy_price":        "Giá mua",
        "sell_date":        "Ngày bán",
        "sell_price":       "Giá bán",
        "hold_days":        "Giữ (ngày)",
        "pnl_pct":          "Lời/Lỗ %",
        "pnl_amount":       "Lời/Lỗ (VND)",
        "capital_after":    "Vốn sau lệnh",
        "win":              "Thắng",
    }

    # Sort theo thanh khoản TB giảm dần
    if "avg_tk_20p_ty" in df.columns:
        df = df.sort_values("avg_tk_20p_ty", ascending=False)

    cols = list(df.columns)
    headers_vn = [col_map.get(c, c) for c in cols]
    _write_header(ws, headers_vn)

    # Cột tiền cần format phân cách nghìn
    money_cols = {"final_capital", "initial_capital", "pnl_amount", "capital_after"}

    for i, (_, row) in enumerate(df.iterrows(), start=2):
        for j, col in enumerate(cols, start=1):
            val = row.get(col)
            # Format tiền
            if col in money_cols and val is not None:
                try:
                    val = f"{int(val):,}"
                except Exception:
                    pass
            ws.cell(row=i, column=j, value=val)

        # Màu theo lời/lỗ
        pnl_j = next((j for j, c in enumerate(cols, 1) if c in ("pnl_pct", "total_return_pct")), None)
        if pnl_j:
            pnl_val = row.get(cols[pnl_j - 1], 0)
            if isinstance(pnl_val, (int, float)):
                fill = GREEN_FILL if pnl_val > 0 else RED_FILL
                for j in range(1, len(cols) + 1):
                    ws.cell(row=i, column=j).fill = fill

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
