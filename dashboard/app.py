"""
Streamlit Dashboard — 4 tabs:
  1. Tín hiệu hôm nay
  2. Phân tích cổ phiếu
  3. Backtest
  4. Lịch sử tín hiệu
"""

import glob
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).parent.parent
SIGNALS_DIR = ROOT / "data" / "signals"
BACKTEST_DIR = ROOT / "data" / "backtest"
REPORTS_DIR = ROOT / "reports"

st.set_page_config(
    page_title="ManhDucCapital Scanner",
    page_icon="📊",
    layout="wide",
)

st.title("📊 ManhDucCapital Scanner")
st.caption("SuperTrend + BiasNorm | Top 100 VN-Index")

tab1, tab2, tab3, tab4 = st.tabs([
    "🟢 Tín hiệu hôm nay",
    "🔍 Phân tích cổ phiếu",
    "📈 Backtest",
    "📅 Lịch sử tín hiệu",
])


# ─── Tab 1: Today's Signals ───────────────────────────────────────────────────

def _load_signal_files() -> list[str]:
    files = sorted(glob.glob(str(SIGNALS_DIR / "signals_*.csv")), reverse=True)
    return files


def _load_signals(file_path: str) -> pd.DataFrame:
    df = pd.read_csv(file_path)
    return df


with tab1:
    signal_files = _load_signal_files()
    if not signal_files:
        st.warning("Chưa có file tín hiệu nào. Chạy scanner trước.")
        st.stop()

    # Date selector
    dates = [Path(f).stem.replace("signals_", "") for f in signal_files]
    selected_date = st.selectbox("Chọn ngày:", dates, index=0)
    selected_file = SIGNALS_DIR / f"signals_{selected_date}.csv"
    df = _load_signals(str(selected_file))

    # Summary metrics
    buy_count = int(df["buy_signal"].sum()) if "buy_signal" in df.columns else 0
    sell_count = int(df["sell_signal"].sum()) if "sell_signal" in df.columns else 0
    avg_bias = df["bias_norm"].mean() if "bias_norm" in df.columns else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tổng mã quét", len(df))
    col2.metric("🟢 Tín hiệu MUA", buy_count)
    col3.metric("🔴 Tín hiệu BÁN", sell_count)
    col4.metric("BiasNorm trung bình", f"{avg_bias:.1f}")

    # Color-coded table
    st.subheader("Danh sách cổ phiếu")

    def color_rows(row):
        if row.get("buy_signal"):
            return ["background-color: #C6EFCE"] * len(row)
        if row.get("sell_signal"):
            return ["background-color: #FFC7CE"] * len(row)
        if row.get("bias_norm", 0) >= 70:
            return ["background-color: #FFEB9C"] * len(row)
        return [""] * len(row)

    display_cols = [c for c in ["ticker", "close", "trend", "bias_norm", "bias_label",
                                 "buy_signal", "sell_signal", "b_score", "r_score",
                                 "supertrend", "atr", "volume"] if c in df.columns]
    styled = df[display_cols].style.apply(color_rows, axis=1)
    st.dataframe(styled, use_container_width=True, height=500)

    # Export button
    report_file = REPORTS_DIR / f"report_{selected_date}.xlsx"
    if report_file.exists():
        with open(report_file, "rb") as f:
            st.download_button(
                label="⬇️ Tải Excel",
                data=f,
                file_name=report_file.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    # BiasNorm distribution chart
    if "bias_norm" in df.columns:
        st.subheader("Phân phối BiasNorm")
        bins = [0, 25, 45, 55, 75, 100]
        labels = ["Rất yếu\n(0-25)", "Yếu\n(25-45)", "Trung tính\n(45-55)",
                  "Mạnh\n(55-75)", "Rất mạnh\n(75-100)"]
        counts = pd.cut(df["bias_norm"], bins=bins, labels=labels).value_counts().sort_index()
        fig = go.Figure(go.Bar(x=counts.index.tolist(), y=counts.values,
                               marker_color=["#FF4444", "#FF9999", "#AAAAAA", "#99CC99", "#33AA33"]))
        fig.update_layout(height=300, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)


# ─── Tab 2: Individual Stock Analysis ─────────────────────────────────────────

with tab2:
    signal_files2 = _load_signal_files()
    if not signal_files2:
        st.info("Chưa có data.")
        st.stop()

    latest_df = _load_signals(signal_files2[0])
    tickers = sorted(latest_df["ticker"].tolist()) if "ticker" in latest_df.columns else []

    selected_ticker = st.selectbox("Chọn mã cổ phiếu:", tickers)
    if not selected_ticker:
        st.stop()

    row = latest_df[latest_df["ticker"] == selected_ticker].iloc[0] if not latest_df[latest_df["ticker"] == selected_ticker].empty else None

    if row is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric("Giá đóng cửa", f"{row.get('close', 0):,.0f}")
        c2.metric("BiasNorm", f"{row.get('bias_norm', 0):.0f}/100", delta=row.get("bias_label", ""))
        trend_str = "↑ Tăng" if row.get("trend", 0) == 1 else "↓ Giảm"
        c3.metric("Xu hướng", trend_str)

    # BiasNorm gauge
    bias_val = float(row.get("bias_norm", 50)) if row is not None else 50
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=bias_val,
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#2196F3"},
            "steps": [
                {"range": [0, 30], "color": "#FF4444"},
                {"range": [30, 45], "color": "#FF9999"},
                {"range": [45, 55], "color": "#AAAAAA"},
                {"range": [55, 70], "color": "#99CC99"},
                {"range": [70, 100], "color": "#33AA33"},
            ],
            "threshold": {"line": {"color": "black", "width": 3}, "value": bias_val},
        },
        title={"text": "BiasNorm"},
    ))
    fig_gauge.update_layout(height=300, margin=dict(t=30, b=0))
    st.plotly_chart(fig_gauge, use_container_width=True)

    # Criteria breakdown table
    if row is not None:
        criteria = ["ema", "vwap", "rsi", "macd", "adx", "obv", "stoch", "candle", "vol"]
        labels = ["EMA9>21", "Giá>VWAP", "RSI>52", "MACD↑ tăng", "ADX>20", "OBV↑", "Stoch↑", "Nến nửa trên", "Volume↑"]
        crit_data = []
        for key, label in zip(criteria, labels):
            bull = row.get(f"bull_{key}", False)
            crit_data.append({"Tiêu chí": label, "Bull": "✅" if bull else "❌"})
        st.subheader("9 tiêu chí BiasNorm")
        st.dataframe(pd.DataFrame(crit_data), use_container_width=True, hide_index=True)


# ─── Tab 3: Backtest ──────────────────────────────────────────────────────────

with tab3:
    results_file = BACKTEST_DIR / "backtest_results.csv"
    trades_file = BACKTEST_DIR / "backtest_trades.csv"

    if not results_file.exists():
        st.info("Chưa có dữ liệu backtest. Chạy `python -m scanner.backtest` hoặc đợi workflow hàng tuần.")
        st.stop()

    bt_df = pd.read_csv(results_file)
    st.subheader("Tổng hợp backtest theo mã")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng mã", len(bt_df))
    avg_wr = bt_df["win_rate"].mean() if "win_rate" in bt_df.columns else 0
    c2.metric("Win rate TB", f"{avg_wr:.1f}%")
    avg_ret = bt_df["total_return_pct"].mean() if "total_return_pct" in bt_df.columns else 0
    c3.metric("Return TB", f"{avg_ret:.1f}%")
    avg_dd = bt_df["max_drawdown_pct"].mean() if "max_drawdown_pct" in bt_df.columns else 0
    c4.metric("Drawdown TB", f"{avg_dd:.1f}%")

    st.dataframe(bt_df.sort_values("total_return_pct", ascending=False), use_container_width=True, height=400)

    if trades_file.exists():
        trades_df = pd.read_csv(trades_file)
        ticker_filter = st.selectbox("Xem chi tiết lệnh của mã:", ["Tất cả"] + sorted(trades_df["ticker"].unique().tolist()))
        if ticker_filter != "Tất cả":
            trades_df = trades_df[trades_df["ticker"] == ticker_filter]

        st.dataframe(trades_df, use_container_width=True, height=400)

        # Equity curve
        if "capital_after" in trades_df.columns:
            fig_eq = go.Figure(go.Scatter(
                y=trades_df["capital_after"].values,
                mode="lines+markers",
                line=dict(color="#2196F3"),
                name="Vốn",
            ))
            fig_eq.update_layout(title="Đường vốn (Equity Curve)", height=350,
                                  yaxis_title="Vốn (VND)", xaxis_title="Lệnh")
            st.plotly_chart(fig_eq, use_container_width=True)


# ─── Tab 4: Signal History ────────────────────────────────────────────────────

with tab4:
    all_files = _load_signal_files()
    if not all_files:
        st.info("Chưa có dữ liệu lịch sử.")
        st.stop()

    dfs = []
    for f in all_files:
        try:
            d = pd.read_csv(f)
            d["scan_date"] = Path(f).stem.replace("signals_", "")
            dfs.append(d)
        except Exception:
            pass

    history = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

    if history.empty:
        st.warning("Không đọc được lịch sử.")
        st.stop()

    # Signal count timeline
    st.subheader("Số tín hiệu MUA/BÁN theo ngày")
    timeline = (
        history.groupby("scan_date")
        .agg(buy=("buy_signal", "sum"), sell=("sell_signal", "sum"))
        .reset_index()
    )
    fig_tl = go.Figure()
    fig_tl.add_trace(go.Bar(x=timeline["scan_date"], y=timeline["buy"], name="MUA", marker_color="#33AA33"))
    fig_tl.add_trace(go.Bar(x=timeline["scan_date"], y=timeline["sell"], name="BÁN", marker_color="#FF4444"))
    fig_tl.update_layout(barmode="group", height=350)
    st.plotly_chart(fig_tl, use_container_width=True)

    # Download full history
    csv = history.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Tải toàn bộ lịch sử CSV", csv, "signal_history.csv", "text/csv")
