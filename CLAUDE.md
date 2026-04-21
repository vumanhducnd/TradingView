# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a single-file TradingView Pine Script v5 indicator: `ManhDucCapital.pine`. There is no build system, no tests, and no package manager — editing the `.pine` file and pasting it into TradingView's Pine Editor is the entire workflow.

## Architecture

The file is organized into sequential sections (Pine Script executes top-to-bottom on every bar):

| Section | Purpose |
|---|---|
| Inputs | All `input.*` declarations grouped by UI group |
| Utility functions | `f_pad`, `f_price`, `f_million`, `f_sign`, `f_buyPrice`, `f_sellPrice` |
| Palette | Theme-aware color constants (`T_BG_DRK`, `T_GRN`, `T_RED`, etc.) |
| Technical indicators | EMA, VWAP, RSI, MACD, ADX, OBV, Stochastic — computed once, reused everywhere |
| Dual Score Logic | Bull/bear 9-point scoring → `biasNorm` (0–100), `biasColor`, `biasTextVN` |
| SuperTrend | ATR-based trend line with optional strength filter; produces `buySignal` / `sellSignal` |
| Bollinger Band | Optional overlay, color follows `trend` |
| Signal tracking | Tracks most recent signal: `signalPrice`, `signalType`, `signalDate`, `signalCount`, `pnl` |
| Volume accumulation | `trend_buy_vol` / `trend_sell_vol` reset on trend flip; `delta_vol_pct` |
| Support/Resistance | Single dashed line + label that follows the SuperTrend level |
| Backtest zone lines | Vertical start/end lines for backtest period |
| Backtest engine | Simulates buy-on-`buySignal` / sell-on-`sellSignal`; tracks capital, PnL, drawdown |
| History table (`htbl`) | Full trade log, rendered only on `barstate.islast` |
| Signal table (`stbl`) | Top-right summary box, rendered only on `barstate.islast` |
| Star label (`lbStar`) | `✪` marker at current SuperTrend level |
| Analysis label (`lbVol`) | Floating multi-line analysis box with risk targets and recommendation |
| Alerts | Three `alertcondition` calls at the bottom |

## Key Design Decisions

**Price formatting (`f_price`):** Assumes Vietnamese stock prices. Values `< 10000` are treated as USD-style decimals; values `≥ 10000` are divided by 1000 to convert from VNĐ units (e.g., `25500 → 25.5`).

**biasNorm:** Normalised bull/bear score in range `[0, 100]`. Values ≥ 55 = bullish, ≤ 45 = bearish, 45–55 = neutral. Used both in the signal table and as an optional flip-filter for SuperTrend.

**Backtest engine limitations:** Only one position at a time (buy on `buySignal` if flat, sell on `sellSignal` if long). No stop-loss. `compoundMode` toggles whether PnL compounds on the running capital.

**`var` vs non-`var`:** Tables and persistent state use `var`. All indicator calculations are non-`var` and recompute every bar.

**Performance:** All table rendering is gated on `barstate.islast` to avoid redundant cell updates on every historical bar.

## Pine Script Conventions Used

- `nz(x, default)` — null-safe previous value access
- `ta.change(trend)` — detects trend flip for volume reset
- `extend.right` on SR line — line extends forward automatically
- `last_bar_index + 1` as x-position for the analysis label — places it just beyond the last candle
