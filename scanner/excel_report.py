"""
Build Excel report with 4 sheets: All stocks, Signals, Backtest, Summary stats.
"""

from datetime import date
from pathlib import Path

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

    _sheet_all(wb, results)
    _sheet_signals(wb, signals)
    _sheet_stats(wb, results, scan_date)

    # Load backtest results if available
    bt_file = Path("data/backtest/backtest_results.csv")
    if bt_file.exists():
        _sheet_backtest(wb, pd.read_csv(bt_file))

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

    headers = [
        "Mã", "Xu hướng", "Tín hiệu mới",
        "Lệnh HĐ", "Ngày vào lệnh", "Giá vào lệnh", "Giá hiện tại",
        "Giữ (phiên)", "Lời/Lỗ %",
        "BiasNorm", "Nhận xét", "bScore", "rScore",
    ] + crit_labels
    _write_header(ws, headers)

    for i, (_, row) in enumerate(df.iterrows(), start=2):
        trend_str = "↑ TĂNG" if row.get("trend", 0) == 1 else "↓ GIẢM"
        new_sig  = "🟢 MUA" if row.get("buy_signal") else ("🔴 BÁN" if row.get("sell_signal") else "")
        last_sig = row.get("last_signal_type", "")
        sig_date = row.get("last_signal_date")
        sig_date_str = str(sig_date)[:10] if sig_date else ""
        sig_price = row.get("last_signal_price")
        bars  = row.get("bars_since_signal")
        pnl   = row.get("signal_pnl_pct")   # PnL từ lần lật gần nhất
        max_loss = row.get("max_loss_pct")

        base_row = [
            row.get("ticker", ""),
            trend_str,
            new_sig,
            f"{'🟢' if last_sig == 'MUA' else '🔴'} {last_sig}" if last_sig else "",
            sig_date_str,
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
    headers = ["Mã", "Loại", "Giá", "SuperTrend", "Khoảng cách %", "BiasNorm", "Nhận xét", "bScore"]
    _write_header(ws, headers)

    row_idx = 2
    for signal_type, df in [("MUA", signals.get("buy", pd.DataFrame())),
                              ("BÁN", signals.get("sell", pd.DataFrame()))]:
        if df.empty:
            continue
        fill = GREEN_FILL if signal_type == "MUA" else RED_FILL
        for _, row in df.iterrows():
            close = row.get("close", 0)
            st = row.get("supertrend", 0)
            dist_pct = ((close - st) / st * 100) if st else 0
            vals = [
                row.get("ticker", ""),
                signal_type,
                close,
                round(st, 2),
                round(dist_pct, 2),
                round(row.get("bias_norm", 0), 1),
                row.get("bias_label", ""),
                row.get("b_score", 0),
            ]
            for j, v in enumerate(vals, start=1):
                cell = ws.cell(row=row_idx, column=j, value=v)
                cell.fill = fill
            row_idx += 1

    _auto_width(ws)
    ws.freeze_panes = "A2"


def _sheet_stats(wb: Workbook, df: pd.DataFrame, scan_date: str) -> None:
    ws = wb.create_sheet("Thống kê")

    def write_kv(r, key, val):
        ws.cell(row=r, column=1, value=key).font = BOLD
        ws.cell(row=r, column=2, value=val)

    buy_count = int(df["buy_signal"].sum()) if "buy_signal" in df.columns else 0
    sell_count = int(df["sell_signal"].sum()) if "sell_signal" in df.columns else 0
    avg_bias = round(df["bias_norm"].mean(), 1) if "bias_norm" in df.columns else 0

    write_kv(1, "Ngày quét", scan_date)
    write_kv(2, "Tổng mã quét", len(df))
    write_kv(3, "Tín hiệu MUA", buy_count)
    write_kv(4, "Tín hiệu BÁN", sell_count)
    write_kv(5, "BiasNorm trung bình", avg_bias)

    # BiasNorm distribution
    ws.cell(row=7, column=1, value="Phân phối BiasNorm").font = BOLD
    buckets = [(0, 25, "Rất yếu"), (25, 45, "Yếu"), (45, 55, "Trung tính"), (55, 75, "Mạnh"), (75, 100, "Rất mạnh")]
    for r, (lo, hi, label) in enumerate(buckets, start=8):
        count = int(((df["bias_norm"] >= lo) & (df["bias_norm"] < hi)).sum())
        ws.cell(row=r, column=1, value=f"{label} ({lo}-{hi})")
        ws.cell(row=r, column=2, value=count)

    # Top 5 strongest
    ws.cell(row=14, column=1, value="Top 5 mạnh nhất").font = BOLD
    top5 = df.nlargest(5, "bias_norm")[["ticker", "bias_norm"]]
    for r, (_, tr) in enumerate(top5.iterrows(), start=15):
        ws.cell(row=r, column=1, value=tr["ticker"])
        ws.cell(row=r, column=2, value=round(tr["bias_norm"], 1))

    _auto_width(ws)


def _sheet_backtest(wb: Workbook, df: pd.DataFrame) -> None:
    ws = wb.create_sheet("Backtest")
    if df.empty:
        return

    headers = list(df.columns)
    _write_header(ws, headers)
    for i, (_, row) in enumerate(df.iterrows(), start=2):
        for j, col in enumerate(headers, start=1):
            ws.cell(row=i, column=j, value=row.get(col))
        pnl_col = next((j for j, c in enumerate(headers, 1) if "pnl_pct" in c.lower()), None)
        if pnl_col:
            val = row.get(headers[pnl_col - 1], 0)
            if isinstance(val, (int, float)):
                ws.cell(row=i, column=pnl_col).fill = GREEN_FILL if val > 0 else RED_FILL

    _auto_width(ws)
    ws.freeze_panes = "A2"


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


def _auto_width(ws, max_width: int = 30) -> None:
    for col in ws.columns:
        length = max(
            len(str(cell.value)) if cell.value is not None else 0
            for cell in col
        )
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(length + 3, max_width)
