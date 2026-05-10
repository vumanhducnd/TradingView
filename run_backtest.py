"""
Chạy backtest cho toàn bộ mã trong DB.
Kết quả lưu vào data/backtest/backtest_results.csv và backtest_trades.csv
"""

import sys
from datetime import date, timedelta
from scanner.utils import logger, StepTimer
from scanner.data_fetcher import load_all_from_db
from scanner.backtest import run_portfolio_backtest

style = "long"   # "long" (10/3.0) hoặc "short" (7/2.0)
if "--short" in sys.argv:
    style = "short"

# Mặc định: 1 năm gần nhất
date_to   = date.today().strftime("%Y-%m-%d")
date_from = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")

print(f"\n=== BACKTEST ({style.upper()}) — {date_from} đến {date_to} ===\n")

with StepTimer("[1/2] Doc OHLCV tu PostgreSQL"):
    data = load_all_from_db()
print(f"       {len(data)} ma")

with StepTimer(f"[2/2] Chay backtest {len(data)} ma"):
    summary = run_portfolio_backtest(data, style=style, date_from=date_from, date_to=date_to)

if summary.empty:
    print("Khong co ket qua backtest")
    sys.exit(1)

# In tóm tắt
summary = summary.sort_values("total_return_pct", ascending=False)
print(f"\n=== KET QUA ({len(summary)} ma) ===")
print(f"Win rate TB:     {summary['win_rate'].mean():.1f}%")
print(f"Return TB:       {summary['total_return_pct'].mean():.1f}%")
print(f"Max drawdown TB: {summary['max_drawdown_pct'].mean():.1f}%")
print(f"Giu lenh TB:     {summary['avg_hold_days'].mean():.0f} ngay")

print(f"\nTop 10 hieu qua nhat:")
top10 = summary.head(10)[["ticker", "total_return_pct", "win_rate", "total_trades"]]
print(top10.to_string(index=False))
print("\nXong! Xem chi tiet trong data/backtest/")
