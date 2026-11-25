# NSE Stocks Intraday Auto Paper-Trader & Telegram Bot (IST)

Automated intraday stocks strategy running strictly in **Indian Standard Time (`Asia/Kolkata`)**, built for **Render Cloud 24/7** + **Telegram Alerts** + **Excel Trade Log**.

## Strategy Rules

### 1. Stock Selection (09:07 AM – 09:27 AM IST)
- **09:07 AM IST**: Fetches NSE Pre-Open Market `Advances` vs `Declines`.
  - `Advances > Declines` -> **LONG (Bullish)** bias
  - `Declines > Advances` -> **SHORT (Bearish)** bias
- **09:25 AM IST** (executes in ~2–4 seconds, well before 09:27 AM):
  - Restricted strictly to **Securities in F&O (`FOSec`)** via NSE `master-quote` / `pre-open?key=FO`.
  - Fetches Top 5 F&O stocks by **Change in OI %** (`live-analysis-oi-spurts-underlyings`).
  - **If Bullish (`Advances > Declines`)**: Keeps F&O OI stocks whose sector is among the **Top 5 Bullish Sectors**, OR stocks that are in the **Top 5 F&O Gainers (`FOSec`)** even if their sector isn't moving; drops the rest.
  - **If Bearish (`Declines > Advances`)**: Keeps F&O OI stocks that are in the **Top 5 F&O Losers (`FOSec`)**; drops the rest.

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
