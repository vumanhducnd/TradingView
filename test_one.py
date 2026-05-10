import sys
from scanner.utils import logger, StepTimer, is_trading_day
from scanner.data_fetcher import load_all_from_db
from scanner.scanner import run_scan_dual, get_current_signals, save_daily_snapshot
from scanner.database import save_scan_results, save_signals, load_scan_results, load_scan_dates
from scanner.excel_report import build_excel_report
from scanner.telegram_bot import send_daily_report

force = "--force" in sys.argv  # python test_one.py --force để scan lại bất kể ngày

print("\n=== MANHDUCAPITAL SCANNER ===\n")

# ─── Kiểm tra có cần scan mới không ────────────────────────────────────────
if not force and not is_trading_day():
    dates = load_scan_dates()
    if dates:
        last_date = dates[0]
        with StepTimer(f"Ngoai phien — Load ket qua scan {last_date} tu DB"):
            results = load_scan_results(last_date)
        if not results.empty:
            signals = get_current_signals(results)
            buy_n  = len(signals.get("buy",  []))
            sell_n = len(signals.get("sell", []))
            print(f"  {len(results)} ma | MUA={buy_n} | BAN={sell_n}")
            with StepTimer("Xuat Excel"):
                build_excel_report(results, signals)
            send_daily_report(results, signals)
            print("\n=== XONG (du lieu cache) ===\n")
            sys.exit(0)
    print("  Chua co du lieu scan nao trong DB. Chay scan moi...")

# ─── Scan mới ────────────────────────────────────────────────────────────────
with StepTimer("[1/5] Doc OHLCV tu PostgreSQL"):
    data = load_all_from_db()
print(f"       {len(data)} ma co du lieu")

with StepTimer("[2/5] Tinh SuperTrend ngan han + dai han"):
    results = run_scan_dual(ticker_data=data)
    signals = get_current_signals(results)

buy_n  = len(signals["buy"])
sell_n = len(signals["sell"])
both_n = len(signals.get("both_buy", [])) + len(signals.get("both_sell", []))
print(f"       {len(results)} ma | MUA={buy_n} | BAN={sell_n} | DONG THUAN={both_n}")

# In tin hieu
is_dual = "long_buy_signal" in results.columns
if buy_n or sell_n:
    if is_dual:
        mask = (results["long_buy_signal"]  | results["short_buy_signal"] |
                results["long_sell_signal"] | results["short_sell_signal"])
    else:
        mask = results["buy_signal"] | results["sell_signal"]
    for _, row in results[mask].iterrows():
        if is_dual:
            dh   = "MUA" if row.get("long_buy_signal")  else ("BAN" if row.get("long_sell_signal")  else "-")
            nh   = "MUA" if row.get("short_buy_signal") else ("BAN" if row.get("short_sell_signal") else "-")
            both = " [CA 2!]" if row.get("both_buy") or row.get("both_sell") else ""
            print(f"       {row['ticker']} | DH:{dh} NH:{nh}{both} | Gia={row['close']:,.0f} | Bias={row['bias_norm']:.0f}")
else:
    print("       Khong co tin hieu hom nay")

with StepTimer("[3/5] Luu CSV snapshot"):
    save_daily_snapshot(results)

with StepTimer("[4/5] Luu vao PostgreSQL"):
    save_scan_results(results)
    save_signals(results)

with StepTimer("[5/5] Xuat Excel"):
    build_excel_report(results, signals)

with StepTimer("[6/6] Gui Telegram"):
    send_daily_report(results, signals)

print("\n=== HOAN THANH ===\n")
