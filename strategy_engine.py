"""
5-Minute Price Action Strategy Engine for NSE Intraday Stocks
Implements:
  - Ignore first 3 5m candles (09:15, 09:20, 09:25); track lowest volume of the day
  - LONG (Advances > Declines): Red candle with lowest day volume & size <= 0.5% -> Alert Candle
  - SHORT (Declines > Advances): Green candle with lowest day volume & size <= 0.5% -> Alert Candle
  - Shifts Alert Candle if a newer qualifying candle forms with even lower day volume
  - Entry on breakout of Alert Candle High (LONG) / Low (SHORT) — first watchlist stock wins
  - SL = Alert Low - 2 pts (LONG) / Alert High + 2 pts (SHORT)
  - Book 50% qty at 1:2 RR; hold remaining 50% until 15:15 IST EOD exit
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd

from config import IST, config
from nse_data_feed import NSEDataFeed
from stock_selector import StockSelector, SelectionResult
from telegram_notifier import notifier
from excel_logger import logger_excel

logger = logging.getLogger("StrategyEngine")


@dataclass
class AlertCandle:
    symbol: str
    direction: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    size_pct: float
    trigger_price: float
    stop_loss: float
    target_1: float


@dataclass
class StockScannerState:
    symbol: str
    direction: str
    min_day_vol: Optional[int] = None
    processed_bars: int = 0
    last_bar_ts: Optional[str] = None
    alert_candle: Optional[AlertCandle] = None


class NseStocksStrategyEngine:
    def __init__(self, mode: Optional[str] = None):
        self.mode = mode or config.DATA_MODE
        self.data_feed = NSEDataFeed(mode=self.mode)
        self.selector = StockSelector(self.data_feed)

        self.date_str: str = datetime.now(IST).strftime("%Y-%m-%d")
        self.preopen_breadth: Optional[Dict] = None
        self.selection_result: Optional[SelectionResult] = None
        self.scanners: Dict[str, StockScannerState] = {}

        self.active_trade: Optional[Dict] = None
        self.closed_trades: List[Dict] = []
        self.trades_taken_today: int = 0
        self.eod_completed: bool = False

    def step_0907_preopen(self) -> Dict:
        """09:07 AM IST: Fetch NSE Pre-Open Breadth and broadcast Alert #1."""
        self.preopen_breadth = self.selector.run_0907_preopen_breadth()
        notifier.notify_preopen_breadth(self.preopen_breadth)
        return self.preopen_breadth

    def step_0925_selection(self) -> SelectionResult:
        """09:25 AM IST: Run OI + Sector/Mover stock selection and broadcast Alert #2."""
        if self.preopen_breadth is None:
            self.preopen_breadth = self.selector.run_0907_preopen_breadth()

        self.selection_result = self.selector.run_0925_stock_selection(self.preopen_breadth)
        self.scanners = {
            sym: StockScannerState(symbol=sym, direction=self.selection_result.bias)
            for sym in self.selection_result.selected_symbols
        }
        notifier.notify_stock_selection(self.selection_result)
        return self.selection_result

    def _compute_quantity(self, entry_price: float, stop_loss: float) -> int:
        """Compute even integer share quantity so 50% partial exit splits cleanly."""
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0 or entry_price <= 0:
            return 0
        qty_by_risk = int(config.RISK_PER_TRADE_INR / risk_per_share)
        qty_by_cap = int(config.MAX_CAPITAL_PER_TRADE_INR / entry_price)
        raw_qty = max(2, min(qty_by_risk, qty_by_cap))
        # Round down to even integer (minimum 2 shares)
        even_qty = max(2, raw_qty - (raw_qty % 2))
        return even_qty

    def _build_alert_candle(
        self, symbol: str, direction: str, ts_str: str, row: pd.Series
    ) -> Optional[AlertCandle]:
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        v = int(row["volume"])
        if c <= 0 or v <= 0:
            return None

        size_pct = ((h - l) / c) * 100.0
        if size_pct > config.MAX_ALERT_CANDLE_PCT:
            return None

        if direction == "LONG":
            trigger_price = round(h, 2)
            stop_loss = round(l - config.SL_BUFFER_POINTS, 2)
            risk = trigger_price - stop_loss
            if risk <= 0 or stop_loss <= 0:
                return None
            target_1 = round(trigger_price + config.TARGET_RR_RATIO * risk, 2)
        else:
            trigger_price = round(l, 2)
            stop_loss = round(h + config.SL_BUFFER_POINTS, 2)
            risk = stop_loss - trigger_price
            if risk <= 0:
                return None
            target_1 = round(trigger_price - config.TARGET_RR_RATIO * risk, 2)

        return AlertCandle(
            symbol=symbol,
            direction=direction,
            timestamp=ts_str,
            open=round(o, 2),
            high=round(h, 2),
            low=round(l, 2),
            close=round(c, 2),
            volume=v,
            size_pct=round(size_pct, 3),
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            target_1=target_1,
        )

    def _enter_trade(self, alert: AlertCandle, entry_price: float, entry_time: str):
        qty = self._compute_quantity(entry_price, alert.stop_loss)
        if qty < 2:
            logger.warning(f"Skipping {alert.symbol}: computed qty < 2")
            return

        risk = round(abs(entry_price - alert.stop_loss), 2)
        target_1 = (
            round(entry_price + config.TARGET_RR_RATIO * risk, 2)
            if alert.direction == "LONG"
            else round(entry_price - config.TARGET_RR_RATIO * risk, 2)
        )

        self.active_trade = {
            "trade_id": f"STK_{datetime.now(IST).strftime('%H%M%S')}_{alert.symbol}",
            "symbol": alert.symbol,
            "direction": alert.direction,
            "entry_time": entry_time,
            "entry_price": round(entry_price, 2),
            "stop_loss": alert.stop_loss,
            "target_1": target_1,
            "risk_per_share": risk,
            "initial_qty": qty,
            "remaining_qty": qty,
            "t1_booked": False,
            "alert_candle_time": alert.timestamp,
            "alert_candle_vol": alert.volume,
            "alert_size_pct": alert.size_pct,
        }
        self.trades_taken_today += 1

        logger.info(
            f"[ENTRY] {alert.direction} {alert.symbol} | Qty={qty} @ {entry_price:.2f} | "
            f"SL={alert.stop_loss:.2f} | T1={target_1:.2f}"
        )
        notifier.notify_entry(self.active_trade)

    def _handle_t1_booking(self, exit_price: float, exit_time: str):
        pos = self.active_trade
        if not pos or pos["t1_booked"]:
            return

        book_qty = pos["initial_qty"] // 2
        if book_qty <= 0:
            return

        pts = (
            (exit_price - pos["entry_price"])
            if pos["direction"] == "LONG"
            else (pos["entry_price"] - exit_price)
        )
        pnl_inr = round(pts * book_qty, 2)
        ret_pct = (pts / pos["entry_price"]) * 100.0

        pos["remaining_qty"] -= book_qty
        pos["t1_booked"] = True

        leg = {
            "trade_id": f"{pos['trade_id']}_T1",
            "date": self.date_str,
            "time": exit_time,
            "symbol": pos["symbol"],
            "direction": pos["direction"],
            "action": f"{pos['direction']} T1 (50%)",
            "quantity": book_qty,
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 2),
            "stop_loss": pos["stop_loss"],
            "target_1": pos["target_1"],
            "pnl_pts": round(pts, 2),
            "pnl_inr": pnl_inr,
            "return_pct": round(ret_pct, 2),
            "exit_reason": "Target 1 (1:2 RR) 50% Booked",
            "remarks": f"Alert {pos['alert_candle_time']} Vol={pos['alert_candle_vol']}",
        }
        self.closed_trades.append(leg)
        logger_excel.log_trade_event(leg)
        notifier.notify_target_1(pos, book_qty, exit_price, pnl_inr, exit_time)

    def _close_remaining_position(self, exit_price: float, exit_time: str, reason: str):
        pos = self.active_trade
        if not pos or pos["remaining_qty"] <= 0:
            self.active_trade = None
            return

        rem_qty = pos["remaining_qty"]
        pts = (
            (exit_price - pos["entry_price"])
            if pos["direction"] == "LONG"
            else (pos["entry_price"] - exit_price)
        )
        pnl_inr = round(pts * rem_qty, 2)
        ret_pct = (pts / pos["entry_price"]) * 100.0

        leg = {
            "trade_id": f"{pos['trade_id']}_FINAL",
            "date": self.date_str,
            "time": exit_time,
            "symbol": pos["symbol"],
            "direction": pos["direction"],
            "action": f"{pos['direction']} EXIT",
            "quantity": rem_qty,
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 2),
            "stop_loss": pos["stop_loss"],
            "target_1": pos["target_1"],
            "pnl_pts": round(pts, 2),
            "pnl_inr": pnl_inr,
            "return_pct": round(ret_pct, 2),
            "exit_reason": reason,
            "remarks": f"Alert {pos['alert_candle_time']} Size={pos['alert_size_pct']}%",
        }
        self.closed_trades.append(leg)
        logger_excel.log_trade_event(leg)
        notifier.notify_exit(pos, rem_qty, exit_price, pnl_inr, reason, exit_time)
        self.active_trade = None

    def process_completed_bar(self, symbol: str, ts: pd.Timestamp, row: pd.Series, notify_alerts: bool = True):
        """
        Process one completed 5-minute bar strictly bar-by-bar (zero lookahead).
        """
        ts_str = ts.strftime("%H:%M")
        scanner = self.scanners.get(symbol)
        if scanner is None:
            return

        if scanner.last_bar_ts is not None and ts.isoformat() <= scanner.last_bar_ts:
            return

        vol = int(row["volume"])
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])

        # 1. Monitor active trade if it belongs to this symbol
        if self.active_trade and self.active_trade["symbol"] == symbol:
            pos = self.active_trade
            if pos["direction"] == "LONG":
                if l <= pos["stop_loss"]:
                    self._close_remaining_position(pos["stop_loss"], ts_str, "Stop Loss Hit")
                elif (not pos["t1_booked"]) and h >= pos["target_1"]:
                    self._handle_t1_booking(pos["target_1"], ts_str)
            else:
                if h >= pos["stop_loss"]:
                    self._close_remaining_position(pos["stop_loss"], ts_str, "Stop Loss Hit")
                elif (not pos["t1_booked"]) and l <= pos["target_1"]:
                    self._handle_t1_booking(pos["target_1"], ts_str)

            if self.active_trade and ts_str >= config.SQUAREOFF_TIME:
                self._close_remaining_position(c, ts_str, "15:15 PM Intraday Exit")

            scanner.last_bar_ts = ts.isoformat()
            scanner.processed_bars += 1
            if vol > 0:
                scanner.min_day_vol = vol if scanner.min_day_vol is None else min(scanner.min_day_vol, vol)
            return

        # 2. Ignore first 3 candles (09:15, 09:20, 09:25) for setup scanning, but record lowest volume
        if scanner.processed_bars < config.IGNORE_FIRST_CANDLES or ts_str < config.PRICE_ACTION_START_TIME:
            scanner.last_bar_ts = ts.isoformat()
            scanner.processed_bars += 1
            if vol > 0:
                scanner.min_day_vol = vol if scanner.min_day_vol is None else min(scanner.min_day_vol, vol)
            return

        # 3. Stop scanning for new entries if daily trade limit reached or past 15:10
        if self.trades_taken_today >= config.MAX_TRADES_PER_DAY or ts_str >= "15:10":
            scanner.last_bar_ts = ts.isoformat()
            scanner.processed_bars += 1
            if vol > 0:
                scanner.min_day_vol = vol if scanner.min_day_vol is None else min(scanner.min_day_vol, vol)
            return

        # 4. Check breakout of armed Alert Candle
        entered_now = False
        if scanner.alert_candle is not None:
            ac = scanner.alert_candle
            if ac.direction == "LONG" and h > ac.high:
                entry_px = max(ac.high, o)
                self._enter_trade(ac, entry_px, ts_str)
                # Also check same-bar SL/T1 conservatively after entry
                if self.active_trade:
                    if l <= self.active_trade["stop_loss"]:
                        self._close_remaining_position(self.active_trade["stop_loss"], ts_str, "Stop Loss Hit")
                    elif h >= self.active_trade["target_1"]:
                        self._handle_t1_booking(self.active_trade["target_1"], ts_str)
                entered_now = True
            elif ac.direction == "SHORT" and l < ac.low:
                entry_px = min(ac.low, o)
                self._enter_trade(ac, entry_px, ts_str)
                if self.active_trade:
                    if h >= self.active_trade["stop_loss"]:
                        self._close_remaining_position(self.active_trade["stop_loss"], ts_str, "Stop Loss Hit")
                    elif l <= self.active_trade["target_1"]:
                        self._handle_t1_booking(self.active_trade["target_1"], ts_str)
                entered_now = True

        # 5. If no entry triggered, check if this bar is a new (or even lower-volume) Alert Candle
        if not entered_now:
            is_valid_color = (c < o) if scanner.direction == "LONG" else (c > o)
            is_lowest_vol = (vol > 0) and (scanner.min_day_vol is None or vol < scanner.min_day_vol)
            if is_valid_color and is_lowest_vol:
                candidate = self._build_alert_candle(symbol, scanner.direction, ts_str, row)
                if candidate is not None:
                    shifted = scanner.alert_candle is not None
                    prev_vol = scanner.min_day_vol
                    scanner.alert_candle = candidate
                    if notify_alerts:
                        notifier.notify_alert_candle(candidate, shifted=shifted, prev_min_vol=prev_vol)

        scanner.last_bar_ts = ts.isoformat()
        scanner.processed_bars += 1
        if vol > 0:
            scanner.min_day_vol = vol if scanner.min_day_vol is None else min(scanner.min_day_vol, vol)

    def check_forming_bar_live(self, symbol: str, row: pd.Series, now_str: str):
        """
        Check live intrabar high/low/ltp on the currently forming 5-minute candle
        for instant Alert Candle breakout entry and instant SL / Target 1 execution.
        """
        h, l, ltp = float(row["high"]), float(row["low"]), float(row["close"])
        scanner = self.scanners.get(symbol)
        if scanner is None:
            return

        if self.active_trade and self.active_trade["symbol"] == symbol:
            pos = self.active_trade
            if pos["direction"] == "LONG":
                if l <= pos["stop_loss"]:
                    self._close_remaining_position(pos["stop_loss"], now_str, "Stop Loss Hit (Live)")
                elif (not pos["t1_booked"]) and h >= pos["target_1"]:
                    self._handle_t1_booking(pos["target_1"], now_str)
            else:
                if h >= pos["stop_loss"]:
                    self._close_remaining_position(pos["stop_loss"], now_str, "Stop Loss Hit (Live)")
                elif (not pos["t1_booked"]) and l <= pos["target_1"]:
                    self._handle_t1_booking(pos["target_1"], now_str)
            return

        if (
            self.trades_taken_today < config.MAX_TRADES_PER_DAY
            and scanner.alert_candle is not None
            and now_str >= config.PRICE_ACTION_START_TIME
            and now_str < "15:10"
        ):
            ac = scanner.alert_candle
            if ac.direction == "LONG" and h > ac.high:
                self._enter_trade(ac, ac.high, now_str)
            elif ac.direction == "SHORT" and l < ac.low:
                self._enter_trade(ac, ac.low, now_str)

    def poll_live_market(self):
        """
        Called periodically (every ~20s) between 09:30 and 15:15 IST in live mode.
        Processes newly completed 5m bars chronologically across watchlist stocks,
        then checks the forming 5m bar for immediate breakouts/exits.
        """
        if not self.scanners:
            return

        now_ist = datetime.now(IST)
        now_str = now_ist.strftime("%H:%M")
        minute_floor = (now_ist.minute // 5) * 5
        current_bucket = now_ist.replace(minute=minute_floor, second=0, microsecond=0)

        # Fetch 5m data for all selected stocks (or active trade stock if already entered)
        symbols_to_poll = (
            [self.active_trade["symbol"]]
            if self.active_trade
            else list(self.scanners.keys())
        )

        completed_events = []
        forming_bars = {}

        for sym in symbols_to_poll:
            df = self.data_feed.fetch_intraday_5m_candles(sym, target_date=now_ist.date())
            if df.empty:
                continue
            completed_df = df[df.index < current_bucket]
            for ts, row in completed_df.iterrows():
                completed_events.append((ts, sym, row))
            forming_df = df[df.index == current_bucket]
            if not forming_df.empty:
                forming_bars[sym] = forming_df.iloc[-1]

        # Chronological across all watchlist stocks so first setup wins cleanly
        completed_events.sort(key=lambda item: item[0])
        for ts, sym, row in completed_events:
            self.process_completed_bar(sym, ts, row, notify_alerts=True)

        # Check live forming candle
        for sym, row in forming_bars.items():
            self.check_forming_bar_live(sym, row, now_str)

    def finalize_eod(self):
        """15:15 PM IST: Square off any remaining open position and send Excel report."""
        if self.eod_completed:
            return

        now_str = datetime.now(IST).strftime("%H:%M")
        if self.active_trade:
            sym = self.active_trade["symbol"]
            exit_px = self.active_trade["entry_price"]
            df = self.data_feed.fetch_intraday_5m_candles(sym)
            if not df.empty:
                exit_px = float(df.iloc[-1]["close"])
            self._close_remaining_position(exit_px, now_str, "15:15 PM Intraday Exit")

        bias = self.selection_result.bias if self.selection_result else "NEUTRAL"
        selected = ", ".join(self.selection_result.selected_symbols) if self.selection_result else "None"
        total_pnl = sum(t.get("pnl_inr", 0.0) for t in self.closed_trades)
        status = f"Done ({len(self.closed_trades)} legs, P&L ₹{total_pnl:,.2f})"

        logger_excel.log_daily_summary(
            date_str=self.date_str,
            bias=bias,
            selected_stocks=selected,
            trades_list=self.closed_trades,
            day_status=status,
        )
        notifier.notify_daily_summary(
            date_str=self.date_str,
            bias=bias,
            selected_stocks=selected,
            trades_list=self.closed_trades,
        )
        if config.EXCEL_LOG_PATH.exists():
            notifier.send_document(
                config.EXCEL_LOG_PATH,
                caption=f"📊 <b>NSE Stocks Intraday Paper Trade Log ({self.date_str})</b>",
            )
        self.eod_completed = True

    def run_simulation(self, bias_scenario: str = "LONG", outcome: str = "win"):
        """
        Full end-to-end simulation of 09:07 -> 09:25 -> 09:30..15:15 -> EOD Report.
        """
        logger.info(f"Starting Full Simulation | Bias={bias_scenario} | Outcome={outcome}")
        self.data_feed.mode = "simulated"

        # 1. 09:07 Pre-Open Breadth
        if bias_scenario == "SHORT":
            self.preopen_breadth = {
                "advances": 480,
                "declines": 1620,
                "unchanged": 190,
                "bias": "SHORT",
                "timestamp": datetime.now(IST).strftime("%d-%b-%Y 09:07:30"),
                "source": "SIMULATED_BEARISH",
            }
            notifier.notify_preopen_breadth(self.preopen_breadth)
        else:
            self.step_0907_preopen()

        # 2. 09:25 Stock Selection
        if bias_scenario == "SHORT":
            # Ensure simulated OI stocks match Top 5 Losers for bearish test
            orig_oi = self.data_feed.fetch_top_oi_change_stocks
            self.data_feed.fetch_top_oi_change_stocks = lambda top_n=5: [
                {"symbol": "TATASTEEL", "oi_change_pct": 44.10, "ltp": 152.0},
                {"symbol": "SAIL", "oi_change_pct": 38.20, "ltp": 114.0},
                {"symbol": "HINDALCO", "oi_change_pct": 31.50, "ltp": 645.0},
                {"symbol": "DABUR", "oi_change_pct": 24.10, "ltp": 430.0},
                {"symbol": "ZEEL", "oi_change_pct": 21.80, "ltp": 128.0},
            ][:top_n]
            self.step_0925_selection()
            self.data_feed.fetch_top_oi_change_stocks = orig_oi
        else:
            self.step_0925_selection()

        # 3. 09:30 -> 15:15 Bar-by-bar multi-stock replay
        sim_frames: Dict[str, pd.DataFrame] = {}
        for idx, sym in enumerate(self.selection_result.selected_symbols):
            base_px = 935.0 if sym == "MARICO" else (430.0 if sym == "DABUR" else 650.0)
            sim_frames[sym] = self.data_feed.generate_simulated_5m_candles(
                symbol=sym,
                direction=self.selection_result.bias,
                start_price=base_px,
                trigger_setup=(idx == 0),  # First stock triggers setup first
                scenario=outcome,
            )

        all_events = []
        for sym, df in sim_frames.items():
            for ts, row in df.iterrows():
                all_events.append((ts, sym, row))
        all_events.sort(key=lambda x: x[0])

        for ts, sym, row in all_events:
            self.process_completed_bar(sym, ts, row, notify_alerts=True)

        # 4. EOD Summary & Excel
        self.finalize_eod()
        return self.closed_trades
