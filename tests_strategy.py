"""Unit tests for 2-Stage Sniper Stock Finder & 5m Price Action Strategy."""

import os
os.environ["TELEGRAM_ENABLED"] = "False"

import unittest
from datetime import datetime, timedelta
import pandas as pd

from config import IST, config
from strategy_engine import NseStocksStrategyEngine
from stock_selector import StockSelector, Stage1Result, Stage1Stock
from nse_data_feed import NSEDataFeed


def make_bars(bars_spec):
    start = datetime.now(IST).replace(hour=9, minute=15, second=0, microsecond=0)
    rows = []
    for idx, (o, h, l, c, v) in enumerate(bars_spec):
        rows.append({
            "timestamp": start + timedelta(minutes=5 * idx),
            "open": o, "high": h, "low": l, "close": c, "volume": v,
        })
    return pd.DataFrame(rows).set_index("timestamp")


class TestTwoStageSniperAndStrategy(unittest.TestCase):
    def test_stage1_nr4_vol_surge_and_no_exhaustion(self):
        feed = NSEDataFeed(mode="simulated")
        selector = StockSelector(feed)
        s1 = selector.run_stage1_premarket_watchlist()
        syms = [s.symbol for s in s1.shortlist]
        self.assertGreaterEqual(len(syms), 6)
        self.assertIn("MARICO", syms)
        self.assertIn("RADICO", syms)

    def test_stage2_sniper_sweet_spot_rs_and_double_lock(self):
        feed = NSEDataFeed(mode="simulated")
        selector = StockSelector(feed)
        s1 = selector.run_stage1_premarket_watchlist()
        preopen = selector.run_0907_preopen_breadth()
        res = selector.run_stage2_live_sniper_filter(preopen, s1)
        self.assertTrue(res.breadth_locked)
        self.assertEqual(res.bias, "LONG")
        # DABUR (+4.50%) must be rejected (> 3.0% overextended), MARICO (+1.95%) & RADICO (+1.65%) selected
        self.assertIn("MARICO", res.selected_symbols)
        self.assertIn("RADICO", res.selected_symbols)
        self.assertNotIn("DABUR", res.selected_symbols)
        self.assertLessEqual(len(res.selected_symbols), 2)

    def test_stage2_double_breadth_mismatch_skips(self):
        feed = NSEDataFeed(mode="simulated")
        selector = StockSelector(feed)
        s1 = selector.run_stage1_premarket_watchlist()
        # 09:07 Bearish, 09:25 Bullish -> Mismatch -> NO_TRADE
        preopen = {"advances": 400, "declines": 1500, "unchanged": 50, "bias": "SHORT"}
        res = selector.run_stage2_live_sniper_filter(preopen, s1)
        self.assertFalse(res.breadth_locked)
        self.assertEqual(res.bias, "NO_TRADE")
        self.assertEqual(res.selected_symbols, [])

    def test_stage2_short_gap_up_support_safety_filter(self):
        feed = NSEDataFeed(mode="simulated")
        feed.fetch_live_market_breadth = lambda: {"advances": 800, "declines": 2100, "unchanged": 50}
        feed.fetch_nifty50_change_pct = lambda: -0.40
        feed.fetch_live_market_quotes = lambda: {
            "TATASTEEL": {"pchange": -1.90, "lastPrice": 151.0, "previousClose": 153.9, "volume": 400000},
            "SAIL": {"pchange": -1.75, "lastPrice": 114.0, "previousClose": 116.0, "volume": 420000},
        }
        s1 = Stage1Result(
            timestamp_ist="test",
            total_fo_scanned=2,
            nr4_count=2,
            backup_count=0,
            shortlist=[
                # TATASTEEL sitting directly on a 3-4d Daily Gap-Up Support Zone -> MUST be skipped
                Stage1Stock("TATASTEEL", "NR4", 153.9, 2.0, 2.0, 1.8, 1000000, 2.1, 0.2, [(149.5, 151.5)], ["NIFTY METAL"]),
                # SAIL clean with no gap-up support nearby -> MUST be selected
                Stage1Stock("SAIL", "NR4", 116.0, 1.5, 1.5, 1.6, 1000000, 2.0, 0.2, [], ["NIFTY METAL"]),
            ],
        )
        selector = StockSelector(feed)
        res = selector.run_stage2_live_sniper_filter({"advances": 400, "declines": 1500, "bias": "SHORT"}, s1)
        self.assertEqual(res.bias, "SHORT")
        self.assertEqual(res.selected_symbols, ["SAIL"])

    def test_long_ignore_3_bars_size_limit_shift_and_exits(self):
        engine = NseStocksStrategyEngine(mode="simulated")
        engine.step_0855_stage1()
        engine.step_0907_preopen()
        engine.step_0925_selection()

        bars = [
            (1000, 1004, 999, 1002, 100000),
            (1002, 1003, 999, 1000, 90000),
            (1000, 1005, 999, 1003, 120000),
            (1003, 1004, 1001, 1002, 95000),
            (1004, 1006, 998, 1000, 70000),
            (1000, 1001, 999, 999.5, 60000),
            (999.5, 1000, 998, 998.5, 50000),
            (999, 1002, 998.5, 1001.5, 110000),
            (1001.5, 1009, 1001, 1008.5, 130000),
        ]
        while len(bars) < 72:
            bars.append((1008, 1011, 1007, 1010, 90000))
        bars.append((1010, 1013, 1009, 1012, 95000))

        df = make_bars(bars)
        sym = engine.selection_result.selected_symbols[0]
        for ts, row in df.iterrows():
            engine.process_completed_bar(sym, ts, row, notify_alerts=False)

        self.assertEqual(len(engine.closed_trades), 2)
        t1_leg, eod_leg = engine.closed_trades
        self.assertEqual(t1_leg["entry_price"], 1000.0)
        self.assertEqual(t1_leg["stop_loss"], 996.0)
        self.assertEqual(t1_leg["exit_price"], 1008.0)
        self.assertEqual(eod_leg["exit_price"], 1012.0)
        self.assertEqual(t1_leg["quantity"], eod_leg["quantity"])

    def test_short_mirror_setup(self):
        engine = NseStocksStrategyEngine(mode="simulated")
        trades = engine.run_simulation(bias_scenario="SHORT", outcome="win")
        self.assertGreaterEqual(len(trades), 2)
        self.assertEqual(trades[0]["direction"], "SHORT")

    def test_live_fo_only_filtering(self):
        feed = NSEDataFeed(mode="live")
        feed.fetch_fo_securities_set = lambda: {"DABUR", "MARICO", "RADICO"}
        feed._nse_get_json = lambda url: (
            {"data": [
                {"symbol": "NIFTYFPI", "avgInOI": 99.0, "underlyingValue": 1000},
                {"symbol": "NONFO_CASH", "avgInOI": 80.0, "underlyingValue": 500},
                {"symbol": "DABUR", "avgInOI": 40.0, "underlyingValue": 430},
            ]}
            if "oi-spurts" in url
            else {
                "FOSec": {"data": [{"symbol": "MARICO", "perChange": 4.2, "ltp": 935}]},
                "allSec": {"data": [{"symbol": "DMART", "perChange": 9.9, "ltp": 4000}]},
            }
        )
        oi = feed.fetch_top_oi_change_stocks(top_n=5)
        self.assertEqual([x["symbol"] for x in oi], ["DABUR"])
        movers = feed.fetch_top_gainers_losers(top_n=5)
        self.assertEqual([x["symbol"] for x in movers["gainers"]], ["MARICO"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
