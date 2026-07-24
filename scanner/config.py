import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
SIGNALS_DIR = DATA_DIR / "signals"
REPORTS_DIR = ROOT_DIR / "reports"
WATCHLIST_FILE = DATA_DIR / "watchlist.csv"

for d in [SIGNALS_DIR, REPORTS_DIR]:
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
LOOKBACK_DAYS = 400
MAX_WORKERS = 1      # 1 worker để tránh rate limit (tăng lên 3 nếu có API key)
FETCH_DELAY = 3.5    # giây chờ giữa mỗi request (guest: 20/phút → cần >3s, community: 60/phút → 1s)

# vnstock API key (đăng ký miễn phí tại vnstocks.com/login → 60 req/phút)
VNSTOCK_API_KEY = os.getenv("VNSTOCK_API_KEY", "")

# Google Gemini API key (miễn phí tại aistudio.google.com → 1500 req/ngày)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Groq API key (miễn phí tại console.groq.com → 14,400 req/ngày)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
_UNSUPPORTED_GROQ_MODELS = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-4-scout-17b-16e-instruct",
}


def _resolve_groq_model(env_name: str, default_model: str) -> str:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return default_model
    if raw_value in _UNSUPPORTED_GROQ_MODELS:
        return default_model
    return raw_value


GROQ_MODEL = _resolve_groq_model("GROQ_MODEL", _DEFAULT_GROQ_MODEL)
GROQ_VISION_MODEL = _resolve_groq_model("GROQ_VISION_MODEL", GROQ_MODEL)

# OpenAI API key (platform.openai.com → trả phí theo token)
# Dùng làm fallback khi Groq hết quota (trước Gemini)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Finnhub API key (miễn phí tại finnhub.io → 60 req/phút)
# Dùng để lấy lịch sự kiện kinh tế quốc tế hàng ngày
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# Telegram — bot dài hạn (long, 10/3.0)
# Fallback về TELEGRAM_BOT_TOKEN nếu chưa set biến riêng
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN_LONG") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID_LONG")  or os.getenv("TELEGRAM_CHAT_ID", "")

# Telegram — bot ngắn hạn (short, 7/2.0)
TELEGRAM_TOKEN_SHORT   = os.getenv("TELEGRAM_BOT_TOKEN_SHORT") or TELEGRAM_TOKEN
TELEGRAM_CHAT_ID_SHORT = os.getenv("TELEGRAM_CHAT_ID_SHORT")   or TELEGRAM_CHAT_ID

# Multi-channel: TELEGRAM_CHAT_ID_LONG / SHORT có thể là nhiều ID cách nhau bằng dấu phẩy
# Ví dụ: TELEGRAM_CHAT_ID_LONG=-100123456,-100789012
def _parse_chat_ids(raw: str) -> list[str]:
    return [c.strip() for c in raw.split(",") if c.strip()] if raw else []

TELEGRAM_CHAT_IDS_LONG  = _parse_chat_ids(TELEGRAM_CHAT_ID)
TELEGRAM_CHAT_IDS_SHORT = _parse_chat_ids(TELEGRAM_CHAT_ID_SHORT)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

