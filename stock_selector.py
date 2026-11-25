"""
09:07 AM & 09:25 AM Stock Selection Pipeline
Selects intraday watchlist stocks within seconds by combining:
  1. 09:07 AM NSE Pre-Open Market Breadth (Advances vs Declines)
  2. 09:25 AM Top 5 Change-in-OI % stocks (NSE OI Spurts)
  3. Bullish Mode: Top 5 Bullish Sectors OR Top 5 Gainers override
     Bearish Mode: Must be in Top 5 Losers
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List
from datetime import datetime

from config import IST, config
from nse_data_feed import NSEDataFeed

logger = logging.getLogger("StockSelector")


@dataclass
class StockCandidate:
    symbol: str
    oi_change_pct: float
    ltp: float
    sectors: List[str]
    best_sector: str
    sector_pct: float
    in_top_movers: bool
    mover_pct: float
    selected: bool
    reason: str


@dataclass
class SelectionResult:
    timestamp_ist: str
    bias: str                      # "LONG", "SHORT", or "NEUTRAL"
    advances: int
    declines: int
    unchanged: int
    top_sectors: List[Dict]        # Top 5 Bullish or Bearish sectors
    top_movers: List[Dict]         # Top 5 Gainers (LONG) or Losers (SHORT)
    candidates: List[StockCandidate] = field(default_factory=list)
    selected_symbols: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class StockSelector:
    def __init__(self, data_feed: NSEDataFeed):
        self.data_feed = data_feed

    def run_0907_preopen_breadth(self) -> Dict:
        """Step 1 (09:07 AM IST): Check NSE Pre-Open Advances vs Declines."""
        breadth = self.data_feed.fetch_preopen_breadth()
        adv = breadth["advances"]
        dec = breadth["declines"]
        if adv > dec:
            bias = "LONG"
        elif dec > adv:
            bias = "SHORT"
        else:
            bias = "NEUTRAL"

        result = {
            "advances": adv,
            "declines": dec,
            "unchanged": breadth["unchanged"],
            "bias": bias,
            "timestamp": breadth["timestamp"],
            "source": breadth["source"],
        }
        logger.info(
            f"[09:07 PRE-OPEN] Advances={adv} | Declines={dec} | Unchanged={breadth['unchanged']} -> Bias={bias}"
        )
        return result

    def run_0925_stock_selection(self, preopen_breadth: Dict) -> SelectionResult:
        """
        Step 2 (09:25 AM IST): Select stocks in under 2 minutes (usually 2-4 seconds).
        """
        t0 = time.perf_counter()
        bias = preopen_breadth.get("bias", "NEUTRAL")
        adv = preopen_breadth.get("advances", 0)
        dec = preopen_breadth.get("declines", 0)
        unch = preopen_breadth.get("unchanged", 0)

        oi_stocks = self.data_feed.fetch_top_oi_change_stocks(top_n=config.TOP_OI_COUNT)
        all_sectors = self.data_feed.fetch_sectoral_indices()
        variations = self.data_feed.fetch_top_gainers_losers(top_n=config.TOP_MOVERS_COUNT)

        sector_pct_map = {s["index"]: s["percentChange"] for s in all_sectors}

        if bias == "LONG":
            bullish_sectors = [s for s in all_sectors if s["percentChange"] > 0][:config.TOP_SECTORS_COUNT]
            top_sector_names = {s["index"] for s in bullish_sectors}
            top_movers = variations.get("gainers", [])[:config.TOP_MOVERS_COUNT]
            top_sectors = bullish_sectors
        elif bias == "SHORT":
            bearish_sectors = sorted(
                [s for s in all_sectors if s["percentChange"] < 0],
                key=lambda x: x["percentChange"],
            )[:config.TOP_SECTORS_COUNT]
            top_sector_names = {s["index"] for s in bearish_sectors}
            top_movers = variations.get("losers", [])[:config.TOP_MOVERS_COUNT]
            top_sectors = bearish_sectors
        else:
            top_sector_names = set()
            top_movers = []
            top_sectors = all_sectors[:config.TOP_SECTORS_COUNT]

        mover_map = {m["symbol"]: m["perChange"] for m in top_movers}
        candidates: List[StockCandidate] = []
        selected_symbols: List[str] = []

        for st in oi_stocks:
            sym = st["symbol"]
            oi_pct = st["oi_change_pct"]
            ltp = st["ltp"]
            sectors = self.data_feed.get_stock_sectors(sym)

            # Pick the sector with strongest alignment to current bias
            best_sec = sectors[0] if sectors else "UNKNOWN"
            best_sec_pct = sector_pct_map.get(best_sec, 0.0)
            for s_name in sectors:
                s_pct = sector_pct_map.get(s_name, 0.0)
                if (bias == "LONG" and s_pct > best_sec_pct) or (bias == "SHORT" and s_pct < best_sec_pct):
                    best_sec = s_name
                    best_sec_pct = s_pct

            in_movers = sym in mover_map
            mover_pct = mover_map.get(sym, 0.0)

            if bias == "LONG":
                sector_bullish = any(s in top_sector_names for s in sectors)
                if sector_bullish:
                    selected = True
                    reason = f"Selected: Bullish Sector ({best_sec} {best_sec_pct:+.2f}%)"
                elif in_movers:
                    selected = True
                    reason = f"Selected: Top 5 Gainer ({mover_pct:+.2f}%) despite flat/weak sector ({best_sec} {best_sec_pct:+.2f}%)"
                else:
                    selected = False
                    reason = f"Removed: Sector ({best_sec} {best_sec_pct:+.2f}%) not in Top 5 Bullish & not in Top 5 Gainers"
            elif bias == "SHORT":
                if in_movers:
                    selected = True
                    reason = f"Selected: Bearish Bias & in Top 5 Losers ({mover_pct:+.2f}%)"
                else:
                    selected = False
                    reason = f"Removed: Bearish Bias requires Top 5 Losers membership"
            else:
                selected = False
                reason = "Removed: Market Breadth Neutral (Advances == Declines)"

            if selected:
                selected_symbols.append(sym)

            candidates.append(
                StockCandidate(
                    symbol=sym,
                    oi_change_pct=oi_pct,
                    ltp=ltp,
                    sectors=sectors,
                    best_sector=best_sec,
                    sector_pct=best_sec_pct,
                    in_top_movers=in_movers,
                    mover_pct=mover_pct,
                    selected=selected,
                    reason=reason,
                )
            )

        elapsed = round(time.perf_counter() - t0, 2)
        res = SelectionResult(
            timestamp_ist=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            bias=bias,
            advances=adv,
            declines=dec,
            unchanged=unch,
            top_sectors=top_sectors,
            top_movers=top_movers,
            candidates=candidates,
            selected_symbols=selected_symbols,
            elapsed_seconds=elapsed,
        )
        logger.info(
            f"[09:25 SELECTION] Done in {elapsed}s | Bias={bias} | Selected={selected_symbols}"
        )
        return res
