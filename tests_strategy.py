"""Unit tests for NSE Stocks Intraday Strategy & Stock Selection."""

import os
os.environ["TELEGRAM_ENABLED"] = "False"

import unittest
from datetime import datetime, timedelta
import pandas as pd

from config import IST
from strategy_engine import NseStocksStrategyEngine
from stock_selector import StockSelector
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


class TestStockSelectionAndStrategy(unittest.TestCase):
    def test_bullish_selection_sector_and_gainer_override(self):
        feed = NSEDataFeed(mode="simulated")
        selector = StockSelector(feed)
        breadth = selector.run_0907_preopen_breadth()
        self.assertEqual(breadth["bias"], "LONG")
        res = selector.run_0925_stock_selection(breadth)
        # DABUR & MARICO (NIFTY FMCG +3.79%), SUNPHARMA (NIFTY PHARMA +2.38%), RADICO (NIFTY FMCG + Top Gainer)
        # TATASTEEL (NIFTY METAL -2.04% & not Top Gainer) -> removed!
        self.assertIn("DABUR", res.selected_symbols)
        self.assertIn("SUNPHARMA", res.selected_symbols)
        self.assertNotIn("TATASTEEL", res.selected_symbols)
        self.assertLess(res.elapsed_seconds, 5.0)

    def test_bearish_selection_requires_top_5_losers(self):
        feed = NSEDataFeed(mode="simulated")
        feed.fetch_top_oi_change_stocks = lambda top_n=5: [
            {"symbol": "TATASTEEL", "oi_change_pct": 40.0, "ltp": 152.0},
            {"symbol": "DABUR", "oi_change_pct": 35.0, "ltp": 430.0},
        ]
        selector = StockSelector(feed)
        res = selector.run_0925_stock_selection({"bias": "SHORT", "advances": 400, "declines": 1500, "unchanged": 50})
        self.assertEqual(res.selected_symbols, ["TATASTEEL"])

    def test_long_ignore_3_bars_size_limit_shift_and_exits(self):
        engine = NseStocksStrategyEngine(mode="simulated")
        engine.step_0907_preopen()
        engine.step_0925_selection()

        # Bars:
        # 0 (09:15): ignored, vol 100k
        # 1 (09:20): ignored red small, vol 90k -> sets min_day_vol=90k
        # 2 (09:25): ignored, vol 120k
        # 3 (09:30): red, vol 95k (>=90k -> NOT lowest, ignored)
        # 4 (09:35): red, vol 70k, BUT size = 8/1000 = 0.8% (>0.5% -> rejected, though min_day_vol becomes 70k)
        # 5 (09:40): red, vol 60k, size = 2/1000 = 0.2% <= 0.5% -> ALERT CANDLE #1 (H=1001, L=999)
        # 6 (09:45): red, does NOT break 1001, vol 50k, size = 2/999 = 0.2% -> SHIFTS ALERT CANDLE #2 (H=1000, L=998, SL=996)
        # 7 (09:50): green breaks 1000 -> ENTRY @ 1000, SL=996, risk=4, T1=1008
        # 8 (09:55): hits 1009 -> T1 booked (50% qty) @ 1008
        # 72 (15:15): EOD exit remaining 50% @ 1012
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
        # Pad up to 15:15 (index 72)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
