"""
2-Stage Sniper Stock Finder Method (Strictly NSE Securities in F&O)
  Stage 1 (Pre-Market 08:55 AM / 15:30 PM):
    - Scan ~218 F&O stocks on Daily OHLCV
    - NR4 Compression + Volume Surge >= 1.3x (vs 20d avg) + No 3-Day Exhaustion
    - High-ATR + Strong Close backup if NR4 yields too few stocks -> Top 10-15 watchlist
  Stage 2 (Live 09:07 AM + 09:25 AM):
    - Double Breadth Lock (09:07 Pre-Open + 09:25 Live Market must agree)
    - Top 2-3 Strongest Green (> +0.2%) or Red (< -0.2%) Sectors
    - Sweet-Spot Move (0.5% to 3.0%, reject 4%+ runners)
    - A+ Sniper Relative Strength (>= 0.5% better than Nifty 50 AND own Sector)
    - Priority Boost: NSE OI Spurts or Opening Volume >= 2x
    - Short Safety: Reject if sitting on 3-4 day Daily Gap-Up Support Zone
    - Final Output: Top 1 to 2 Sniper Stocks
"""

import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple

from config import IST, config
from nse_data_feed import NSEDataFeed

logger = logging.getLogger("StockSelector")


@dataclass
class Stage1Stock:
    symbol: str
    tag: str                          # "NR4" or "ATR_BACKUP"
    last_close: float
    last_range: float
    nr4_min_range: float
    vol_ratio: float
    avg_vol_20: float
    atr_pct: float
    clv: float
    gap_up_zones: List[Tuple[float, float]] = field(default_factory=list)
    sectors: List[str] = field(default_factory=list)


@dataclass
class Stage1Result:
    timestamp_ist: str
    total_fo_scanned: int
    nr4_count: int
    backup_count: int
    shortlist: List[Stage1Stock] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass
class StockCandidate:
    symbol: str
    stage1_tag: str
    stock_pct: float
    ltp: float
    best_sector: str
    sector_pct: float
    nifty50_pct: float
    in_oi_spurts: bool
    oi_change_pct: float
    opening_vol_ratio: float
    score: float
    selected: bool
    reason: str


@dataclass
class SelectionResult:
    timestamp_ist: str
    bias: str                      # "LONG", "SHORT", or "NO_TRADE"
    breadth_locked: bool
    qty_multiplier: float
    preopen_adv: int
    preopen_dec: int
    live_adv: int
    live_dec: int
    nifty50_pct: float
    top_sectors: List[Dict]        # Top 2-3 Strongest/Weakest qualifying sectors
    stage1_count: int
    candidates: List[StockCandidate] = field(default_factory=list)
    selected_symbols: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class StockSelector:
    def __init__(self, data_feed: NSEDataFeed):
        self.data_feed = data_feed

    def run_stage1_premarket_watchlist(self) -> Stage1Result:
        """
        Stage 1 (08:55 AM / EOD): Filter ~218 NSE F&O stocks to 10-15 best Daily setups.
        """
        t0 = time.perf_counter()
        fo_set = self.data_feed.fetch_fo_securities_set()
        daily_map = self.data_feed.fetch_fo_daily_history(fo_set)

        primary: List[Stage1Stock] = []
        backups: List[Stage1Stock] = []

        for sym, df in daily_map.items():
            if len(df) < config.VOLUME_SURGE_LOOKBACK_DAYS + 1:
                continue

            o = df["open"].astype(float).values
            h = df["high"].astype(float).values
            l = df["low"].astype(float).values
            c = df["close"].astype(float).values
            v = df["volume"].astype(float).values

            # 1. No 3-Day Exhaustion (last 3 completed candles NOT all green or all red)
            colors = [
                1 if c[i] > o[i] else (-1 if c[i] < o[i] else 0)
                for i in range(-config.EXHAUSTION_STREAK_DAYS, 0)
            ]
            if all(col == 1 for col in colors) or all(col == -1 for col in colors):
                continue

            # 2. NR4 Compression
            ranges = h - l
            last_range = float(ranges[-1])
            nr4_min = float(min(ranges[-config.NR4_LOOKBACK_DAYS:]))
            is_nr4 = last_range <= nr4_min * 1.0001

            # 3. Volume Surge >= 1.3x vs prior 20 trading days
            avg_vol_20 = float(v[-(config.VOLUME_SURGE_LOOKBACK_DAYS + 1):-1].mean())
            if avg_vol_20 <= 0:
                continue
            vol_ratio = float(v[-1]) / avg_vol_20

            # Compute ATR(14)% and Close Location Value (CLV) for backup ranking
            tr_list = []
            for i in range(-config.ATR_PERIOD, 0):
                tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
                tr_list.append(tr)
            atr_pct = (sum(tr_list) / len(tr_list)) / max(1.0, float(c[-1])) * 100.0
            clv = (float(c[-1]) - float(l[-1])) / max(0.01, last_range)

            # Collect 3-4 day Daily Gap-Up Support Zones for Stage 2 Short safety check
            gap_zones: List[Tuple[float, float]] = []
            for k in range(-4, 0):
                if l[k] > h[k - 1] or o[k] > c[k - 1] * 1.005:
                    z_low = round(min(float(h[k - 1]), float(c[k - 1])), 2)
                    z_high = round(max(float(l[k]), float(o[k])), 2)
                    gap_zones.append((z_low, z_high))

            sectors = self.data_feed.get_stock_sectors(sym)
            item = Stage1Stock(
                symbol=sym,
                tag="NR4" if is_nr4 else "ATR_BACKUP",
                last_close=round(float(c[-1]), 2),
                last_range=round(last_range, 2),
                nr4_min_range=round(nr4_min, 2),
                vol_ratio=round(vol_ratio, 2),
                avg_vol_20=round(avg_vol_20, 1),
                atr_pct=round(atr_pct, 2),
                clv=round(clv, 2),
                gap_up_zones=gap_zones,
                sectors=sectors,
            )

            if is_nr4 and vol_ratio >= config.MIN_VOLUME_SURGE_RATIO:
                primary.append(item)
            elif clv >= 0.75 or clv <= 0.25:
                backups.append(item)

        primary.sort(key=lambda x: x.vol_ratio, reverse=True)
        shortlist = primary[:config.STAGE1_MAX_STOCKS]

        backup_added = 0
        if len(shortlist) < config.STAGE1_MIN_STOCKS:
            backups.sort(key=lambda x: x.atr_pct * max(1.0, x.vol_ratio), reverse=True)
            needed = config.STAGE1_MIN_STOCKS - len(shortlist)
            extra = backups[:needed]
            shortlist.extend(extra)
            backup_added = len(extra)

        elapsed = round(time.perf_counter() - t0, 2)
        res = Stage1Result(
            timestamp_ist=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            total_fo_scanned=len(daily_map),
            nr4_count=min(len(primary), config.STAGE1_MAX_STOCKS),
            backup_count=backup_added,
            shortlist=shortlist,
            elapsed_seconds=elapsed,
        )
        logger.info(
            f"[STAGE 1] Scanned {res.total_fo_scanned} F&O stocks in {elapsed}s | "
            f"NR4={res.nr4_count} + Backup={res.backup_count} -> Shortlist={len(shortlist)}"
        )
        return res

    def run_0907_preopen_breadth(self) -> Dict:
        """09:07 AM IST: Fetch NSE Pre-Open Advances vs Declines."""
        breadth = self.data_feed.fetch_preopen_breadth()
        adv = breadth["advances"]
        dec = breadth["declines"]
        bias = "LONG" if adv > dec else ("SHORT" if dec > adv else "NEUTRAL")
        result = {
            "advances": adv,
            "declines": dec,
            "unchanged": breadth["unchanged"],
            "bias": bias,
            "timestamp": breadth["timestamp"],
            "source": breadth["source"],
        }
        logger.info(f"[09:07 PRE-OPEN] Adv={adv} | Dec={dec} -> Bias={bias}")
        return result

    def run_stage2_live_sniper_filter(
        self, preopen_breadth: Dict, stage1: Stage1Result
    ) -> SelectionResult:
        """
        Stage 2 (09:25 AM IST): Narrow Stage 1 watchlist down to 1-2 Sniper Stocks.
        """
        t0 = time.perf_counter()
        pre_adv = preopen_breadth.get("advances", 0)
        pre_dec = preopen_breadth.get("declines", 0)
        bias_0907 = preopen_breadth.get("bias", "NEUTRAL")

        # 1. Double Breadth Lock (09:07 + 09:25)
        live_breadth = self.data_feed.fetch_live_market_breadth()
        live_adv = live_breadth.get("advances", 0)
        live_dec = live_breadth.get("declines", 0)
        bias_0925 = "LONG" if live_adv > live_dec else ("SHORT" if live_dec > live_adv else "NEUTRAL")

        breadth_locked = (bias_0907 in ("LONG", "SHORT")) and (bias_0907 == bias_0925)
        if breadth_locked:
            final_bias = bias_0925
            qty_multiplier = 1.0
        elif config.BREADTH_MISMATCH_MODE == "half_qty" and bias_0925 in ("LONG", "SHORT"):
            final_bias = bias_0925
            qty_multiplier = 0.5
        else:
            final_bias = "NO_TRADE"
            qty_multiplier = 0.0

        all_sectors = self.data_feed.fetch_sectoral_indices()
        nifty50_pct = self.data_feed.fetch_nifty50_change_pct()
        sector_pct_map = {s["index"]: s["percentChange"] for s in all_sectors}

        if final_bias == "LONG":
            top_sectors = [
                s for s in all_sectors if s["percentChange"] > config.MIN_SECTOR_MOVE_PCT
            ][:config.TOP_SECTORS_COUNT]
        elif final_bias == "SHORT":
            top_sectors = sorted(
                [s for s in all_sectors if s["percentChange"] < -config.MIN_SECTOR_MOVE_PCT],
                key=lambda x: x["percentChange"],
            )[:config.TOP_SECTORS_COUNT]
        else:
            top_sectors = all_sectors[:config.TOP_SECTORS_COUNT]

        top_sector_names = {s["index"] for s in top_sectors}

        if final_bias == "NO_TRADE":
            elapsed = round(time.perf_counter() - t0, 2)
            return SelectionResult(
                timestamp_ist=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
                bias="NO_TRADE",
                breadth_locked=False,
                qty_multiplier=0.0,
                preopen_adv=pre_adv,
                preopen_dec=pre_dec,
                live_adv=live_adv,
                live_dec=live_dec,
                nifty50_pct=nifty50_pct,
                top_sectors=top_sectors,
                stage1_count=len(stage1.shortlist),
                candidates=[],
                selected_symbols=[],
                elapsed_seconds=elapsed,
            )

        quotes = self.data_feed.fetch_live_market_quotes()
        oi_spurts = self.data_feed.fetch_top_oi_change_stocks(top_n=config.TOP_OI_COUNT)
        oi_map = {x["symbol"]: x["oi_change_pct"] for x in oi_spurts}

        now_ist = datetime.now(IST)
        market_open_dt = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        elapsed_mins = max(10.0, min(375.0, (now_ist - market_open_dt).total_seconds() / 60.0))

        candidates: List[StockCandidate] = []
        passing: List[StockCandidate] = []

        for st in stage1.shortlist:
            q = quotes.get(st.symbol)
            if not q or q.get("lastPrice", 0) <= 0:
                candidates.append(StockCandidate(
                    symbol=st.symbol, stage1_tag=st.tag, stock_pct=0.0, ltp=st.last_close,
                    best_sector=st.sectors[0] if st.sectors else "UNKNOWN", sector_pct=0.0,
                    nifty50_pct=nifty50_pct, in_oi_spurts=False, oi_change_pct=0.0,
                    opening_vol_ratio=0.0, score=0.0, selected=False,
                    reason="Skipped: No live quote available",
                ))
                continue

            stock_pct = round(float(q["pchange"]), 2)
            ltp = round(float(q["lastPrice"]), 2)
            live_vol = int(q.get("volume", 0))
            expected_vol = max(1.0, st.avg_vol_20 * (elapsed_mins / 375.0))
            open_vol_ratio = round(live_vol / expected_vol, 2)

            in_oi = st.symbol in oi_map
            oi_pct = oi_map.get(st.symbol, 0.0)

            best_sec = st.sectors[0] if st.sectors else "UNKNOWN"
            best_sec_pct = sector_pct_map.get(best_sec, 0.0)
            for s_name in st.sectors:
                s_pct = sector_pct_map.get(s_name, 0.0)
                if (final_bias == "LONG" and s_pct > best_sec_pct) or (final_bias == "SHORT" and s_pct < best_sec_pct):
                    best_sec = s_name
                    best_sec_pct = s_pct

            sector_ok = any(s in top_sector_names for s in st.sectors)

            reject_reason = ""
            if not sector_ok:
                reject_reason = f"Sector {best_sec} ({best_sec_pct:+.2f}%) not in Top {config.TOP_SECTORS_COUNT}"
            elif final_bias == "LONG":
                if stock_pct < config.SWEET_SPOT_MIN_PCT:
                    reject_reason = f"Move {stock_pct:+.2f}% < +{config.SWEET_SPOT_MIN_PCT}%"
                elif stock_pct > config.SWEET_SPOT_MAX_PCT:
                    reject_reason = f"Overextended {stock_pct:+.2f}% > +{config.SWEET_SPOT_MAX_PCT}%"
                elif stock_pct < nifty50_pct + config.MIN_RELATIVE_STRENGTH_PCT:
                    reject_reason = f"RS vs Nifty {stock_pct:+.2f}% < {nifty50_pct + 0.5:+.2f}%"
                elif stock_pct < best_sec_pct + config.MIN_RELATIVE_STRENGTH_PCT:
                    reject_reason = f"RS vs Sector {stock_pct:+.2f}% < {best_sec_pct + 0.5:+.2f}%"
            else:
                if stock_pct > -config.SWEET_SPOT_MIN_PCT:
                    reject_reason = f"Move {stock_pct:+.2f}% > -{config.SWEET_SPOT_MIN_PCT}%"
                elif stock_pct < -config.SWEET_SPOT_MAX_PCT:
                    reject_reason = f"Overextended {stock_pct:+.2f}% < -{config.SWEET_SPOT_MAX_PCT}%"
                elif stock_pct > nifty50_pct - config.MIN_RELATIVE_STRENGTH_PCT:
                    reject_reason = f"RS vs Nifty {stock_pct:+.2f}% > {nifty50_pct - 0.5:+.2f}%"
                elif stock_pct > best_sec_pct - config.MIN_RELATIVE_STRENGTH_PCT:
                    reject_reason = f"RS vs Sector {stock_pct:+.2f}% > {best_sec_pct - 0.5:+.2f}%"
                else:
                    for z_low, z_high in st.gap_up_zones:
                        if z_low * 0.997 <= ltp <= z_high * 1.005:
                            reject_reason = f"Short Safety: Near 3-4d Daily Gap-Up Support ({z_low}-{z_high})"
                            break

            if reject_reason:
                candidates.append(StockCandidate(
                    symbol=st.symbol, stage1_tag=st.tag, stock_pct=stock_pct, ltp=ltp,
                    best_sector=best_sec, sector_pct=best_sec_pct, nifty50_pct=nifty50_pct,
                    in_oi_spurts=in_oi, oi_change_pct=oi_pct, opening_vol_ratio=open_vol_ratio,
                    score=0.0, selected=False, reason=reject_reason,
                ))
            else:
                rs_margin = (
                    (stock_pct - nifty50_pct) + (stock_pct - best_sec_pct)
                    if final_bias == "LONG"
                    else (nifty50_pct - stock_pct) + (best_sec_pct - stock_pct)
                )
                score = (
                    (30.0 if in_oi else 0.0)
                    + (30.0 if open_vol_ratio >= config.OPENING_VOL_SURGE_MULT else 0.0)
                    + (20.0 if st.tag == "NR4" else 5.0)
                    + rs_margin
                )
                boosts = []
                if in_oi:
                    boosts.append(f"OI+{oi_pct:.1f}%")
                if open_vol_ratio >= config.OPENING_VOL_SURGE_MULT:
                    boosts.append(f"OpenVol {open_vol_ratio}x")
                boost_str = f" | Boost: {', '.join(boosts)}" if boosts else ""
                cand = StockCandidate(
                    symbol=st.symbol, stage1_tag=st.tag, stock_pct=stock_pct, ltp=ltp,
                    best_sector=best_sec, sector_pct=best_sec_pct, nifty50_pct=nifty50_pct,
                    in_oi_spurts=in_oi, oi_change_pct=oi_pct, opening_vol_ratio=open_vol_ratio,
                    score=round(score, 2), selected=False,
                    reason=f"A+ Sniper ({st.tag}, {best_sec} {best_sec_pct:+.2f}%{boost_str})",
                )
                passing.append(cand)
                candidates.append(cand)

        passing.sort(key=lambda x: x.score, reverse=True)
        selected_symbols: List[str] = []
        for cand in passing[:config.FINAL_WATCHLIST_MAX]:
            cand.selected = True
            selected_symbols.append(cand.symbol)

        elapsed = round(time.perf_counter() - t0, 2)
        res = SelectionResult(
            timestamp_ist=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            bias=final_bias,
            breadth_locked=breadth_locked,
            qty_multiplier=qty_multiplier,
            preopen_adv=pre_adv,
            preopen_dec=pre_dec,
            live_adv=live_adv,
            live_dec=live_dec,
            nifty50_pct=nifty50_pct,
            top_sectors=top_sectors,
            stage1_count=len(stage1.shortlist),
            candidates=candidates,
            selected_symbols=selected_symbols,
            elapsed_seconds=elapsed,
        )
        logger.info(
            f"[STAGE 2] Done in {elapsed}s | Bias={final_bias} (Locked={breadth_locked}) | "
            f"Final Sniper Watchlist={selected_symbols}"
        )
        return res
