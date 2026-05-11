"""
Test price_board batch fetch.
Chạy: python test_price_board.py
      python test_price_board.py --tickers VIC VHM HPG MBB VNM FPT TCB
      python test_price_board.py --find-limit      # tìm số mã tối đa 1 lần gọi
"""

import sys
import time

# Fix Windows terminal encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

import scanner.utils  # noqa: SSL patch
from scanner.data_fetcher import _set_api_key
_set_api_key()

# ── Tickers ───────────────────────────────────────────────────────────────────
if "--tickers" in sys.argv:
    idx = sys.argv.index("--tickers")
    tickers = [t.upper() for t in sys.argv[idx + 1:]]
else:
    try:
        from scanner.database import get_vn100_watchlist
        tickers = get_vn100_watchlist()
        print(f"Loaded {len(tickers)} tickers từ DB")
    except Exception as e:
        print(f"DB unavailable ({e}), dùng list cứng")
        tickers = ["VIC","VHM","HPG","MBB","VNM","FPT","TCB","ACB","VCB","BID",
                   "CTG","MSN","GAS","PLX","SSI","HDB","MWG","REE","POW","SAB"]

SEP = "─" * 70

# ── Find-limit mode ───────────────────────────────────────────────────────────
if "--find-limit" in sys.argv:
    print(f"\n{SEP}")
    print("  TÌM GIỚI HẠN SỐ MÃ price_board (1 lần gọi)")
    print(SEP)

    # Lấy toàn bộ watchlist từ DB để có đủ mã test
    try:
        from scanner.database import get_watchlist
        all_tickers = get_watchlist()
        print(f"  Watchlist: {len(all_tickers)} mã\n")
    except Exception as e:
        print(f"  Không load được DB: {e}")
        sys.exit(1)

    import pandas as pd
    from vnstock import Trading

    sizes = [100, 200, 500, 800, 1000, len(all_tickers)]
    sizes = sorted(set(s for s in sizes if s <= len(all_tickers)))

    print(f"  {'Số mã':>8}  {'Kết quả':>10}  {'Thời gian':>10}  {'Rows trả về':>12}")
    print(f"  {'─'*8}  {'─'*10}  {'─'*10}  {'─'*12}")

    for size in sizes:
        chunk = all_tickers[:size]
        try:
            t0 = time.time()
            df = Trading(source="VCI").price_board(symbols_list=chunk)
            elapsed = time.time() - t0
            rows = len(df) if df is not None else 0
            status = "✓ OK" if rows > 0 else "✗ rỗng"
            print(f"  {size:>8}  {status:>10}  {elapsed:>9.2f}s  {rows:>12}")
        except Exception as e:
            elapsed = time.time() - t0
            short_err = str(e)[:40]
            print(f"  {size:>8}  {'✗ LỖI':>10}  {elapsed:>9.2f}s  {short_err}")
        time.sleep(2)  # tránh rate limit giữa các lần test

    print(f"\n{SEP}\n")
    sys.exit(0)

# ── Step 1: Raw columns ───────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"  price_board test — {len(tickers)} tickers")
print(SEP)

print("\n[1] Raw columns từ price_board (10 mã đầu)...\n")
try:
    import pandas as pd
    from vnstock import Trading

    t0 = time.time()
    df_raw = Trading(source="VCI").price_board(symbols_list=tickers[:10])
    elapsed = time.time() - t0

    # Flatten MultiIndex
    if isinstance(df_raw.columns, pd.MultiIndex):
        flat_cols = [
            f"{a}.{b}" if b and str(b) != "nan" else str(a)
            for a, b in df_raw.columns
        ]
    else:
        flat_cols = list(df_raw.columns)

    print(f"  Thời gian : {elapsed:.2f}s")
    print(f"  Shape     : {df_raw.shape}  ({df_raw.shape[1]} cột)")
    print(f"\n  Columns liên quan đến giá:")
    for c in flat_cols:
        cl = c.lower()
        if any(k in cl for k in ["price","open","high","low","volume","match","symbol","accum"]):
            print(f"    {c}")
except Exception as e:
    print(f"  LỖI: {e}")
    sys.exit(1)

# ── Step 2: Batch OHLCV ───────────────────────────────────────────────────────
print(f"\n{SEP}")
print(f"\n[2] Batch fetch OHLCV — {len(tickers)} tickers cùng lúc...\n")

from scanner.live_scanner import _fetch_via_price_board

t0 = time.time()
result = _fetch_via_price_board(tickers)
elapsed = time.time() - t0

hit  = len(result)
miss = len(tickers) - hit

print(f"  Thời gian : {elapsed:.2f}s")
print(f"  Kết quả   : {hit} thành công / {miss} thất bại")

if result:
    # Header
    print(f"\n  {'Mã':<6}  {'Open':>9}  {'High':>9}  {'Low':>9}  {'Close':>9}  {'Volume':>13}  H≠L")
    print(f"  {'─'*6}  {'─'*9}  {'─'*9}  {'─'*9}  {'─'*9}  {'─'*13}  {'─'*4}")
    for ticker, b in sorted(result.items()):
        hl_ok = "✓" if b["high"] != b["low"] else "·"
        print(
            f"  {ticker:<6}  {b['open']:>9.2f}  {b['high']:>9.2f}  {b['low']:>9.2f}"
            f"  {b['close']:>9.2f}  {b['volume']:>13,.0f}  {hl_ok}"
        )

    real_hl = sum(1 for b in result.values() if b["high"] != b["low"])
    print(f"\n  Mã có H≠L (session đang diễn ra) : {real_hl}/{hit}")
    if real_hl == 0:
        print("  ⚠  H = L = Close — ngoài giờ giao dịch, chỉ có giá tham chiếu")
    else:
        print("  ✓  Dữ liệu OHLCV hợp lệ — dùng được để tính SuperTrend real-time")

# ── Tóm tắt ───────────────────────────────────────────────────────────────────
per_ticker_est = len(tickers) * 1.0
print(f"\n{SEP}")
print(f"  TỔNG KẾT")
print(f"  price_board batch : {elapsed:.1f}s cho {len(tickers)} mã")
print(f"  per-ticker ước tính : ~{per_ticker_est:.0f}s (1s/mã với key)")
print(f"  Nhanh hơn : ~{per_ticker_est / max(elapsed, 0.1):.0f}x")
print(SEP + "\n")
