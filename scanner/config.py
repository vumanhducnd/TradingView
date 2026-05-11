import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
SIGNALS_DIR = DATA_DIR / "signals"
BACKTEST_DIR = DATA_DIR / "backtest"
REPORTS_DIR = ROOT_DIR / "reports"
WATCHLIST_FILE = DATA_DIR / "watchlist.csv"

for d in [SIGNALS_DIR, BACKTEST_DIR, REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# SuperTrend parameters
ST_PERIOD_LONG = 10
ST_MULT_LONG = 3.0
ST_PERIOD_SHORT = 7
ST_MULT_SHORT = 2.0
ST_DEFAULT_STYLE = "long"  # "long" or "short"

# Indicator parameters (matching Pine Script exactly)
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_SMOOTH = 3
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ADX_PERIOD = 14
OBV_EMA_PERIOD = 10
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
STOCH_SMOOTH = 3
VOL_AVG_PERIOD = 20

# BiasNorm thresholds
BIAS_BULL_THR = 55
BIAS_BEAR_THR = 45
BIAS_STRONG_THR = 70

BIAS_WEAK_THR = 30

# Data fetch
LOOKBACK_DAYS = 400  # enough for EMA warm-up
MAX_WORKERS = 1      # 1 worker để tránh rate limit (tăng lên 3 nếu có API key)
FETCH_DELAY = 3.5    # giây chờ giữa mỗi request (guest: 20/phút → cần >3s, community: 60/phút → 1s)

# vnstock API key (đăng ký miễn phí tại vnstocks.com/login → 60 req/phút)
VNSTOCK_API_KEY = os.getenv("VNSTOCK_API_KEY", "")

# Google Gemini API key (miễn phí tại aistudio.google.com → 1500 req/ngày)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Groq API key (miễn phí tại console.groq.com → 14,400 req/ngày)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Backtest
DEFAULT_CAPITAL = 100_000_000  # 100 triệu VND
COMPOUND_MODE = True
ENTRY_MODE = "realistic"  # "best" | "ideal" | "realistic"
