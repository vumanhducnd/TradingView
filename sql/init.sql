-- ============================================================
-- ManhDucCapital Trading Database Schema
-- Sync với DB thực tế (Neon PostgreSQL)
-- ============================================================

-- ─── watchlist ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist (
    ticker               VARCHAR(10) PRIMARY KEY,
    exchange             VARCHAR(10) DEFAULT 'HOSE',
    updated_at           TIMESTAMP   DEFAULT NOW(),
    vn100_rank           INTEGER,
    avg_turnover_20d     DOUBLE PRECISION,
    avg_volume_20d       DOUBLE PRECISION,
    liquidity_updated_at TIMESTAMP
);

-- ─── ohlcv ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ohlcv (
    ticker  VARCHAR(10)   NOT NULL,
    date    DATE          NOT NULL,
    open    NUMERIC(15,2) NOT NULL,
    high    NUMERIC(15,2) NOT NULL,
    low     NUMERIC(15,2) NOT NULL,
    close   NUMERIC(15,2) NOT NULL,
    volume  BIGINT        NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker ON ohlcv (ticker);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date   ON ohlcv (date DESC);

-- ─── scan_results ─────────────────────────────────────────────────────────────
-- 1 dòng / ticker, OVERWRITE mỗi lần scan
CREATE TABLE IF NOT EXISTS scan_results (
    ticker                 VARCHAR(10)    PRIMARY KEY,
    updated_at             DATE           DEFAULT CURRENT_DATE,
    close                  NUMERIC(12,4),
    bias_norm              NUMERIC(6,2),
    b_score                SMALLINT,
    r_score                SMALLINT,
    -- Bull criteria (9 tiêu chí)
    bull_ema               BOOLEAN,
    bull_vwap              BOOLEAN,
    bull_rsi               BOOLEAN,
    bull_macd              BOOLEAN,
    bull_adx               BOOLEAN,
    bull_obv               BOOLEAN,
    bull_stoch             BOOLEAN,
    bull_candle            BOOLEAN,
    bull_vol               BOOLEAN,
    volume                 BIGINT,
    turnover               NUMERIC(20,4),
    -- Dual SuperTrend (long=10/3.0, short=7/2.0)
    long_trend             SMALLINT,
    short_trend            SMALLINT,
    long_buy_signal        BOOLEAN,
    short_buy_signal       BOOLEAN,
    long_sell_signal       BOOLEAN,
    short_sell_signal      BOOLEAN,
    long_supertrend        NUMERIC(12,4),
    short_supertrend       NUMERIC(12,4),
    -- Tín hiệu gần nhất (tính từ OHLCV)
    long_last_signal_type  VARCHAR(10),
    short_last_signal_type VARCHAR(10),
    long_last_signal_date  DATE,
    long_last_signal_price NUMERIC(12,4),
    short_last_signal_date DATE,
    short_last_signal_price NUMERIC(12,4),
    long_signal_pnl_pct    NUMERIC(8,2),
    short_signal_pnl_pct   NUMERIC(8,2),
    long_bars_since_signal  INTEGER,
    short_bars_since_signal INTEGER,
    -- Đồng thuận 2 khung
    both_buy               BOOLEAN,
    both_sell              BOOLEAN,
    -- Siêu cổ phiếu (7 tiêu chí)
    ss1                    BOOLEAN,
    ss2                    BOOLEAN,
    ss3                    BOOLEAN,
    ss4                    BOOLEAN,
    ss5                    BOOLEAN,
    ss6                    BOOLEAN,
    ss7                    BOOLEAN,
    super_score            SMALLINT,
    is_super_stock         BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_scan_ticker ON scan_results (ticker);
CREATE INDEX IF NOT EXISTS idx_scan_updated ON scan_results (updated_at DESC);

-- ─── signals ──────────────────────────────────────────────────────────────────
-- Chỉ lưu để chống alert trùng giữa phiên sáng/chiều trong cùng ngày
CREATE TABLE IF NOT EXISTS signals (
    id          SERIAL      PRIMARY KEY,
    ticker      VARCHAR(10) NOT NULL,
    signal_date DATE        NOT NULL,
    signal_type VARCHAR(4)  NOT NULL,   -- 'MUA' hoặc 'BÁN'
    style       VARCHAR(10) DEFAULT 'long',
    CONSTRAINT signals_ticker_date_type_style_key
        UNIQUE (ticker, signal_date, signal_type, style)
);

CREATE INDEX IF NOT EXISTS idx_signals_date   ON signals (signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals (ticker);
