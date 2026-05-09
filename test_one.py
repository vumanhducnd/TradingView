import time
import pandas as pd
from scanner.utils import logger
from scanner.data_fetcher import load_all_from_db
from scanner.scanner import run_scan, get_current_signals, save_daily_snapshot
from scanner.database import save_scan_results, save_signals
from scanner.excel_report import build_excel_report
from scanner.telegram_bot import send_daily_report

logger.info("=== BAT DAU TEST VN100 ===")

# Buoc 1: Load VN100
logger.info("[1/6] Doc danh sach VN100 tu watchlist.csv...")
vn100 = pd.read_csv("data/watchlist.csv")["ticker"].tolist()
logger.info(f"     VN100: {len(vn100)} ma")

# Buoc 2: Doc OHLCV tu DB
logger.info("[2/6] Doc OHLCV tu PostgreSQL...")
t0 = time.time()
all_data = load_all_from_db()
data = {t: df for t, df in all_data.items() if t in vn100}
logger.info(f"     {len(data)}/{len(vn100)} ma co du lieu | {time.time()-t0:.1f}s")

# Buoc 3: Tinh SuperTrend + BiasNorm
logger.info("[3/6] Tinh SuperTrend + BiasNorm cho tung ma...")
t0 = time.time()
results = run_scan(ticker_data=data, style="short")  # 7/2.0 ngan han
signals = get_current_signals(results)
buy_n  = len(signals["buy"])
sell_n = len(signals["sell"])
logger.info(f"     Xong {len(results)} ma | {time.time()-t0:.1f}s")
logger.info(f"     Ket qua: MUA={buy_n} | BAN={sell_n}")

# In tin hieu
if buy_n or sell_n:
    df_sig = results[results["buy_signal"] | results["sell_signal"]]
    for _, row in df_sig.iterrows():
        sig = "MUA" if row["buy_signal"] else "BAN"
        logger.info(f"     [{sig}] {row['ticker']} | Gia={row['close']:,.0f} | BiasNorm={row['bias_norm']:.0f}")
else:
    logger.info("     Khong co tin hieu hom nay")

# Buoc 4: Luu CSV
logger.info("[4/6] Luu CSV snapshot...")
save_daily_snapshot(results)

# Buoc 5: Luu DB
logger.info("[5/6] Luu vao PostgreSQL...")
save_scan_results(results)
save_signals(results)
build_excel_report(results, signals)

# Buoc 6: Gui Telegram
logger.info("[6/6] Gui Telegram...")
send_daily_report(results, signals)

logger.info("=== HOAN THANH ===")
