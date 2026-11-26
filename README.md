# NSE Stocks Intraday Auto Paper-Trader & Telegram Bot (IST)

Automated intraday stocks strategy running strictly in **Indian Standard Time (`Asia/Kolkata`)**, built for **Render Cloud 24/7** + **Telegram Alerts** + **Excel Trade Log**.

## Strategy Rules

### 1. 2-Stage Sniper Stock Finder Method (Strictly Securities in F&O)
- **Stage 1 (08:55 AM IST Pre-Market)**: Scans ~218 active NSE F&O stocks via official NSE Bhavcopy daily OHLCV and shortlists 10–15 stocks matching:
  1. **NR4 Compression**: Previous day's Daily range is the smallest of the last 4 trading days.
  2. **Volume Surge (`>= 1.3x`)**: Previous day's volume `>= 1.3x` 20-day average volume.
  3. **No 3-Day Exhaustion**: Not 3 consecutive green or 3 consecutive red daily candles.
  *(High-ATR + strong-close backup fills up to 10 stocks if NR4 yields too few.)*
- **Stage 2 (09:07 AM + 09:25 AM IST Live Sniper Filter)** → narrows Stage 1 to **1–2 Final Stocks**:
  1. **Double Breadth Lock**: 09:07 Pre-Open (`Advances vs Declines`) and 09:25 Live Market (`Advances vs Declines`) must agree in direction (mismatch skips day by default, or `BREADTH_MISMATCH_MODE=half_qty`).
  2. **Top 2–3 Strongest Sectors**: `> +0.2%` Green Sectors (LONG) or `< -0.2%` Red Sectors (SHORT).
  3. **Sweet-Spot Move (`0.5%–3.0%`) + A+ Relative Strength**: Excludes `4%+` runners; requires `>= 0.5%` outperformance vs BOTH Nifty 50 AND own Sector, plus priority boost for NSE OI Spurts / Opening Vol `>= 2x`, and Short safety against 3–4d Daily Gap-Up support zones.

### 2. 5-Minute Price Action (09:30 AM – 15:15 PM IST)
- Ignores the first 3 candles (`09:15`, `09:20`, `09:25`) while recording the day's lowest volume so far.
- **Alert Candle**:
  - **LONG**: First Red candle (`close < open`) from `09:30` onwards whose volume is **lower than all prior candles of the day** (including the first 3 ignored candles) AND whose candle range `(high - low) / close <= 0.5%`.
  - **SHORT**: Mirror Green candle (`close > open`) with the day's lowest volume AND size `<= 0.5%`.
  - If another qualifying candle forms with an **even lower day volume** before breakout, the Alert Candle automatically shifts to the newer candle.
- **Entry**: First watchlist stock to break Alert Candle High (LONG) or Low (SHORT) enters immediately.
- **Stop Loss**: Alert Candle Low − 2 pts (LONG) / Alert Candle High + 2 pts (SHORT).
- **Exits**: Book **50% Qty at 1:2 RR**, hold remaining **50% Qty until 15:15 PM IST** (auto square-off + Excel summary sent to Telegram).

## Local Commands
```powershell
python main.py --test-telegram
python main.py --test-selection
python main.py --mode test
python main.py --mode test-short
python main.py --mode live
```
