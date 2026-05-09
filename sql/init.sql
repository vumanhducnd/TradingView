-- ============================================================
-- ManhDucCapital Trading Database Schema
-- ============================================================

-- Danh sách mã theo dõi
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      VARCHAR(10) PRIMARY KEY,
    exchange    VARCHAR(10) DEFAULT 'HOSE',
    updated_at  TIMESTAMP DEFAULT NOW()
);

-- Dữ liệu OHLCV lịch sử (1 mã = nhiều dòng theo ngày)
CREATE TABLE IF NOT EXISTS ohlcv (
    ticker  VARCHAR(10)    NOT NULL,
    date    DATE           NOT NULL,
    open    NUMERIC(15,2)  NOT NULL,
    high    NUMERIC(15,2)  NOT NULL,
    low     NUMERIC(15,2)  NOT NULL,
    close   NUMERIC(15,2)  NOT NULL,
    volume  BIGINT         NOT NULL,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker ON ohlcv (ticker);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date   ON ohlcv (date DESC);

-- Kết quả quét hàng ngày (toàn bộ 100 mã mỗi ngày)
CREATE TABLE IF NOT EXISTS scan_results (
    id          SERIAL PRIMARY KEY,
    scan_date   DATE           NOT NULL,
    ticker      VARCHAR(10)    NOT NULL,
    close       NUMERIC(15,2),
    trend       SMALLINT,          -- 1=tăng, -1=giảm
    supertrend  NUMERIC(15,2),
    bias_norm   NUMERIC(5,1),
    b_score     SMALLINT,
    r_score     SMALLINT,
    buy_signal  BOOLEAN DEFAULT FALSE,
    sell_signal BOOLEAN DEFAULT FALSE,
    -- 9 tiêu chí bull
    bull_ema    BOOLEAN, bull_vwap   BOOLEAN, bull_rsi  BOOLEAN,
    bull_macd   BOOLEAN, bull_adx    BOOLEAN, bull_obv  BOOLEAN,
    bull_stoch  BOOLEAN, bull_candle BOOLEAN, bull_vol  BOOLEAN,
    atr         NUMERIC(15,4),
    volume      BIGINT,
    UNIQUE (scan_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_scan_date   ON scan_results (scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_scan_ticker ON scan_results (ticker);
CREATE INDEX IF NOT EXISTS idx_scan_signal ON scan_results (scan_date, buy_signal, sell_signal);

-- Lịch sử tín hiệu MUA/BÁN (chỉ lưu khi có signal)
CREATE TABLE IF NOT EXISTS signals (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(10)   NOT NULL,
    signal_date DATE          NOT NULL,
    signal_type VARCHAR(4)    NOT NULL,  -- 'MUA' hoặc 'BÁN'
    price       NUMERIC(15,2),
    supertrend  NUMERIC(15,2),
    bias_norm   NUMERIC(5,1),
    b_score     SMALLINT,
    UNIQUE (ticker, signal_date, signal_type)
);

CREATE INDEX IF NOT EXISTS idx_signals_date   ON signals (signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals (ticker);

-- Lịch sử backtest từng lệnh
CREATE TABLE IF NOT EXISTS backtest_trades (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(10)   NOT NULL,
    style       VARCHAR(10)   DEFAULT 'long',
    buy_date    DATE,
    buy_price   NUMERIC(15,2),
    sell_date   DATE,
    sell_price  NUMERIC(15,2),
    hold_days   INTEGER,
    pnl_pct     NUMERIC(8,2),
    pnl_amount  NUMERIC(15,2),
    win         BOOLEAN,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bt_ticker ON backtest_trades (ticker);

-- Tóm tắt backtest theo mã
CREATE TABLE IF NOT EXISTS backtest_summary (
    ticker             VARCHAR(10) PRIMARY KEY,
    style              VARCHAR(10) DEFAULT 'long',
    total_trades       INTEGER,
    win_trades         INTEGER,
    win_rate           NUMERIC(5,1),
    total_return_pct   NUMERIC(8,2),
    max_drawdown_pct   NUMERIC(8,2),
    avg_hold_days      NUMERIC(6,1),
    updated_at         TIMESTAMP DEFAULT NOW()
);
