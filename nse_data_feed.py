"""
NSE India Live Data Feed & Market Replay Simulator
Uses curl_cffi (Chrome TLS impersonation) for reliable NSE API access,
yfinance for 5-minute intraday OHLCV candles, and realistic simulation for offline testing.
"""

import logging
import random
import zlib
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
import pandas as pd
import yfinance as yf
from curl_cffi import requests as c_requests

from config import (
    IST,
    INDEX_DERIVATIVE_SYMBOLS,
    FO_STOCK_TO_SECTOR,
    YF_SECTOR_TO_NSE,
    config,
)

logger = logging.getLogger("NSEDataFeed")

NSE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class NSEDataFeed:
    def __init__(self, mode: Optional[str] = None):
        self.mode = mode or config.DATA_MODE
        self._session: Optional[c_requests.Session] = None
        self._cookies_warmed_at: Optional[datetime] = None
        self._yf_sector_cache: Dict[str, List[str]] = {}

    def _get_session(self, force_refresh: bool = False) -> c_requests.Session:
        now = datetime.now(IST)
        needs_warm = (
            self._session is None
            or force_refresh
            or self._cookies_warmed_at is None
            or (now - self._cookies_warmed_at).total_seconds() > 180
        )
        if needs_warm:
            self._session = c_requests.Session(impersonate="chrome124")
            try:
                self._session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
                self._cookies_warmed_at = now
            except Exception as e:
                logger.warning(f"NSE homepage cookie warmup warning: {e}")
        return self._session

    def _nse_get_json(self, url: str) -> Dict:
        """Fetch JSON from NSE endpoint with 1 automatic cookie refresh retry."""
        for attempt in range(2):
            session = self._get_session(force_refresh=(attempt == 1))
            try:
                resp = session.get(url, headers=NSE_HEADERS, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(f"NSE {url} returned status {resp.status_code} (attempt {attempt + 1})")
            except Exception as e:
                logger.warning(f"NSE request error on {url} (attempt {attempt + 1}): {e}")
        return {}

    def fetch_preopen_breadth(self) -> Dict:
        """
        09:07 AM: Fetch Pre-Open Market Advances & Declines from NSE.
        Tries key=ALL first, falls back to key=FO.
        """
        if self.mode == "simulated":
            return {
                "advances": 1565,
                "declines": 530,
                "unchanged": 274,
                "timestamp": datetime.now(IST).strftime("%d-%b-%Y 09:07:30"),
                "source": "SIMULATED",
            }

        for key in ("ALL", "FO"):
            data = self._nse_get_json(f"https://www.nseindia.com/api/market-data-pre-open?key={key}")
            adv = data.get("advances")
            dec = data.get("declines")
            if adv is not None and dec is not None:
                return {
                    "advances": int(adv),
                    "declines": int(dec),
                    "unchanged": int(data.get("unchanged") or 0),
                    "timestamp": str(data.get("timestamp") or datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S")),
                    "source": f"NSE_PREOPEN_{key}",
                }

        logger.error("Failed to fetch live NSE pre-open breadth.")
        return {
            "advances": 0,
            "declines": 0,
            "unchanged": 0,
            "timestamp": datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S"),
            "source": "UNAVAILABLE",
        }

    def fetch_top_oi_change_stocks(self, top_n: int = 5) -> List[Dict]:
        """
        09:25 AM: Fetch Top N stocks by Change in OI % from NSE OI Spurts.
        Excludes index contracts (NIFTY, BANKNIFTY, FINNIFTY, etc.).
        """
        if self.mode == "simulated":
            return [
                {"symbol": "DABUR", "oi_change_pct": 46.84, "ltp": 430.0},
                {"symbol": "MARICO", "oi_change_pct": 34.23, "ltp": 935.0},
                {"symbol": "SUNPHARMA", "oi_change_pct": 28.50, "ltp": 1785.0},
                {"symbol": "TATASTEEL", "oi_change_pct": 25.10, "ltp": 152.0},
                {"symbol": "RADICO", "oi_change_pct": 22.40, "ltp": 2140.0},
            ][:top_n]

        payload = self._nse_get_json("https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings")
        rows = payload.get("data", [])
        stocks = []
        for item in rows:
            sym = str(item.get("symbol", "")).strip().upper()
            if not sym or sym in INDEX_DERIVATIVE_SYMBOLS:
                continue
            try:
                oi_pct = float(item.get("avgInOI", 0.0) or 0.0)
                ltp = float(item.get("underlyingValue", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            stocks.append({
                "symbol": sym,
                "oi_change_pct": round(oi_pct, 2),
                "latest_oi": item.get("latestOI", 0),
                "prev_oi": item.get("prevOI", 0),
                "change_in_oi": item.get("changeInOI", 0),
                "ltp": ltp,
            })

        stocks.sort(key=lambda x: x["oi_change_pct"], reverse=True)
        return stocks[:top_n]

    def fetch_sectoral_indices(self) -> List[Dict]:
        """Fetch NSE Sectoral Indices and their % change from allIndices."""
        if self.mode == "simulated":
            return [
                {"index": "NIFTY FMCG", "percentChange": 3.79, "last": 58420.0},
                {"index": "NIFTY PHARMA", "percentChange": 2.38, "last": 22910.0},
                {"index": "NIFTY PRIVATE BANK", "percentChange": 2.21, "last": 26800.0},
                {"index": "NIFTY FINANCIAL SERVICES", "percentChange": 2.17, "last": 25950.0},
                {"index": "NIFTY AUTO", "percentChange": 1.16, "last": 24100.0},
                {"index": "NIFTY IT", "percentChange": 1.12, "last": 41200.0},
                {"index": "NIFTY OIL & GAS", "percentChange": 0.99, "last": 11500.0},
                {"index": "NIFTY REALTY", "percentChange": 0.02, "last": 980.0},
                {"index": "NIFTY METAL", "percentChange": -2.04, "last": 9100.0},
                {"index": "NIFTY MEDIA", "percentChange": -4.44, "last": 1750.0},
            ]

        payload = self._nse_get_json("https://www.nseindia.com/api/allIndices")
        allowed_extra = {"NIFTY BANK", "NIFTY FINANCIAL SERVICES"}
        excluded_aliases = {"NIFTY FINANCIAL SERVICES 25/50", "NIFTY FINANCIAL SERVICES EX-BANK"}
        sectors = []
        seen = set()

        for item in payload.get("data", []):
            key = str(item.get("key", ""))
            idx_name = str(item.get("index", "")).strip()
            if idx_name in excluded_aliases:
                continue
            if key == "SECTORAL INDICES" or idx_name in allowed_extra:
                if idx_name in seen:
                    continue
                seen.add(idx_name)
                try:
                    pct = float(item.get("percentChange", 0.0) or 0.0)
                    last_val = float(item.get("last", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                sectors.append({
                    "index": idx_name,
                    "percentChange": round(pct, 2),
                    "last": last_val,
                })

        sectors.sort(key=lambda x: x["percentChange"], reverse=True)
        return sectors

    def fetch_top_gainers_losers(self, top_n: int = 5) -> Dict[str, List[Dict]]:
        """Fetch Top N F&O Gainers and Losers from NSE live-analysis-variations."""
        if self.mode == "simulated":
            return {
                "gainers": [
                    {"symbol": "DABUR", "perChange": 7.77, "ltp": 430.0},
                    {"symbol": "RADICO", "perChange": 5.40, "ltp": 2140.0},
                    {"symbol": "MARICO", "perChange": 4.85, "ltp": 935.0},
                    {"symbol": "CIPLA", "perChange": 4.10, "ltp": 1520.0},
                    {"symbol": "INFY", "perChange": 3.60, "ltp": 1840.0},
                ][:top_n],
                "losers": [
                    {"symbol": "TATASTEEL", "perChange": -4.20, "ltp": 152.0},
                    {"symbol": "ZEEL", "perChange": -3.85, "ltp": 128.0},
                    {"symbol": "SAIL", "perChange": -3.40, "ltp": 114.0},
                    {"symbol": "HINDALCO", "perChange": -2.90, "ltp": 645.0},
                    {"symbol": "VEDL", "perChange": -2.50, "ltp": 462.0},
                ][:top_n],
            }

        def _parse_variation(endpoint: str) -> List[Dict]:
            data = self._nse_get_json(f"https://www.nseindia.com/api/live-analysis-variations?index={endpoint}")
            rows = data.get("FOSec", {}).get("data") or data.get("allSec", {}).get("data") or []
            parsed = []
            for r in rows[:top_n]:
                sym = str(r.get("symbol", "")).strip().upper()
                if not sym:
                    continue
                parsed.append({
                    "symbol": sym,
                    "perChange": round(float(r.get("perChange", r.get("net_price", 0.0)) or 0.0), 2),
                    "ltp": float(r.get("ltp", 0.0) or 0.0),
                })
            return parsed

        return {
            "gainers": _parse_variation("gainers"),
            "losers": _parse_variation("loosers"),
        }

    def get_stock_sectors(self, symbol: str) -> List[str]:
        """Resolve an F&O stock symbol to its NSE Sectoral Index name(s)."""
        sym = symbol.upper().strip()
        if sym in FO_STOCK_TO_SECTOR:
            return FO_STOCK_TO_SECTOR[sym]
        if sym in self._yf_sector_cache:
            return self._yf_sector_cache[sym]

        try:
            info = yf.Ticker(f"{sym}.NS").info or {}
            yf_sec = info.get("sector", "")
            mapped = YF_SECTOR_TO_NSE.get(yf_sec, [f"YF:{yf_sec}"] if yf_sec else ["UNKNOWN"])
            self._yf_sector_cache[sym] = mapped
            return mapped
        except Exception:
            return ["UNKNOWN"]

    def fetch_intraday_5m_candles(
        self, symbol: str, target_date: Optional[date] = None
    ) -> pd.DataFrame:
        """
        Fetch today's 5-minute OHLCV candles in IST (Asia/Kolkata).
        Filters cleanly between 09:15 and 15:25 IST for target_date.
        """
        if target_date is None:
            target_date = datetime.now(IST).date()

        try:
            df = yf.download(
                f"{symbol}.NS",
                period="2d",
                interval="5m",
                progress=False,
                auto_adjust=False,
            )
            if df is None or df.empty:
                return pd.DataFrame()

            # Flatten MultiIndex columns returned by newer yfinance versions
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] for col in df.columns]

            df = df.rename(columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })

            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC").tz_convert(IST)
            else:
                df.index = df.index.tz_convert(IST)

            # Keep only target_date market hours (09:15 to 15:25)
            df = df[df.index.date == target_date]
            df = df[(df.index.strftime("%H:%M") >= "09:15") & (df.index.strftime("%H:%M") <= "15:25")]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            return df
        except Exception as e:
            logger.warning(f"Failed fetching 5m candles for {symbol}: {e}")
            return pd.DataFrame()

    def generate_simulated_5m_candles(
        self,
        symbol: str,
        direction: str = "LONG",
        start_price: float = 900.0,
        trigger_setup: bool = True,
        scenario: str = "win",
    ) -> pd.DataFrame:
        """
        Generate realistic 5-minute OHLCV candles from 09:15 to 15:20 IST
        for testing the exact Price Action rules (ignored 3 bars, low-vol alert candle <= 0.5%,
        alert shift on even lower volume, breakout entry, 1:2 RR 50% exit, and 15:15 EOD exit).
        """
        rng = random.Random(zlib.crc32(f"{symbol}_{direction}_{scenario}".encode()))
        now_ist = datetime.now(IST)
        start_dt = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        num_bars = 74  # 09:15 to 15:20 in 5-min steps

        rows = []
        price = start_price
        base_vol = 120000

        for i in range(num_bars):
            ts = start_dt + timedelta(minutes=5 * i)
            t_str = ts.strftime("%H:%M")

            # Bars 0, 1, 2, 3 -> 09:15..09:30 (first 3 ignored + 09:30 normal volume bar)
            if i <= 3:
                vol = rng.randint(145000, 260000)
                o = price
                c = o + rng.uniform(-2.0, 2.5)
                h = max(o, c) + rng.uniform(0.5, 2.0)
                l = min(o, c) - rng.uniform(0.5, 2.0)

            # Bar 4 (09:35): First Alert Candle (Red for LONG / Green for SHORT), low volume, <= 0.5%
            elif trigger_setup and i == 4:
                vol = 95000
                o = price
                move = max(1.0, round(price * 0.0028, 2))
                if direction == "LONG":
                    c = o - move
                    h = o + 0.4
                    l = c - 0.3
                else:
                    c = o + move
                    h = c + 0.3
                    l = o - 0.4

            # Bar 5 (09:40): Does NOT break alert candle; instead shifts to an EVEN LOWER volume alert candle
            elif trigger_setup and i == 5:
                vol = 68000
                o = price - 0.5 if direction == "LONG" else price + 0.5
                move = max(0.9, round(price * 0.0022, 2))
                if direction == "LONG":
                    c = o - move
                    h = o + 0.2
                    l = c - 0.2
                else:
                    c = o + move
                    h = c + 0.2
                    l = o - 0.2

            # Bar 6 (09:45): Breakout Entry Candle! Breaks Alert Candle High (LONG) or Low (SHORT)
            elif trigger_setup and i == 6:
                vol = 165000
                o = price
                if direction == "LONG":
                    c = o + round(price * 0.0045, 2)
                    h = c + 1.2
                    l = o - 0.4
                else:
                    c = o - round(price * 0.0045, 2)
                    h = o + 0.4
                    l = c - 1.2

            # Post-entry trend bars (09:50 onwards)
            else:
                vol = rng.randint(85000, 175000)
                o = price
                if trigger_setup and i > 6:
                    if scenario == "win":
                        drift = rng.uniform(0.2, 2.2) if direction == "LONG" else rng.uniform(-2.2, -0.2)
                    else:
                        # Hits SL around bar 9-11
                        drift = rng.uniform(-3.0, -0.8) if (direction == "LONG" and i <= 12) else rng.uniform(0.8, 3.0) if (direction == "SHORT" and i <= 12) else rng.uniform(-0.5, 0.5)
                else:
                    drift = rng.uniform(-1.5, 1.5)
                c = max(10.0, o + drift)
                h = max(o, c) + rng.uniform(0.2, 1.5)
                l = min(o, c) - rng.uniform(0.2, 1.5)

            rows.append({
                "timestamp": ts,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": int(vol),
            })
            price = c

        df = pd.DataFrame(rows).set_index("timestamp")
        return df
