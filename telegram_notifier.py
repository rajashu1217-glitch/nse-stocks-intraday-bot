"""
Telegram Notifier for NSE Stocks Intraday Setup
Sends structured HTML alerts in IST for:
  1. 09:07 AM Pre-Open Breadth (Advances vs Declines)
  2. 09:25 AM Stock Selection (Top 5 OI %, Sectors, Gainers/Losers, Final Watchlist)
  3. 09:30+ AM Alert Candle Armed / Shifted (Lowest Volume + <= 0.5% size)
  4. Breakout Entry Executed
  5. Target 1 (1:2 RR) 50% Qty Booked
  6. SL Hit / 15:15 PM Final Exit + Daily Summary & Excel Attachment
"""

import logging
import requests
from typing import Dict, List, Optional
from config import config

logger = logging.getLogger("TelegramNotifier")


class TelegramNotifier:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        self.bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        self.enabled = enabled if enabled is not None else config.TELEGRAM_ENABLED
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage" if self.bot_token else ""

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        logger.info(f"\n[TELEGRAM ALERT]\n{text}\n")
        if not self.enabled or not (self.bot_token and self.chat_id):
            return False
        try:
            resp = requests.post(
                self.api_url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                return True
            logger.error(f"Telegram API error: {data.get('description')}")
            return False
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def send_document(self, file_path, caption: str = "") -> bool:
        if not self.enabled or not (self.bot_token and self.chat_id):
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
            with open(file_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"},
                    files={"document": f},
                    timeout=25,
                )
            data = resp.json()
            if data.get("ok"):
                return True
            logger.error(f"Telegram sendDocument error: {data.get('description')}")
            return False
        except Exception as e:
            logger.error(f"Telegram sendDocument failed: {e}")
            return False

    def notify_preopen_breadth(self, breadth: Dict) -> bool:
        adv = breadth.get("advances", 0)
        dec = breadth.get("declines", 0)
        unch = breadth.get("unchanged", 0)
        bias = breadth.get("bias", "NEUTRAL")
        emoji = "🟢 BULLISH (LONG)" if bias == "LONG" else ("🔴 BEARISH (SHORT)" if bias == "SHORT" else "⚪ NEUTRAL")
        msg = (
            f"<b>⏰ [09:07 IST] NSE PRE-OPEN BREADTH</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Advances:</b> <code>{adv}</code>\n"
            f"📉 <b>Declines:</b> <code>{dec}</code>\n"
            f"➖ <b>Unchanged:</b> <code>{unch}</code>\n\n"
            f"🧭 <b>Market Bias:</b> <b>{emoji}</b>\n"
            f"🕒 <i>Source: {breadth.get('source', 'NSE')} | {breadth.get('timestamp', '')}</i>\n"
            f"⏭️ Next: 09:25 AM OI % + Sector/Mover Stock Selection"
        )
        return self.send_message(msg)

    def notify_stage1_watchlist(self, stage1) -> bool:
        lines = []
        for s in stage1.shortlist:
            lines.append(
                f"  • <b>{s.symbol}</b> [{s.tag}] | Vol: <code>{s.vol_ratio:.2f}x</code> | "
                f"ATR: <code>{s.atr_pct:.2f}%</code> | {s.sectors[0] if s.sectors else 'N/A'}"
            )
        block = "\n".join(lines) if lines else "  • None"
        msg = (
            f"<b>🎯 [STAGE 1: 08:55 IST] PRE-MARKET F&amp;O WATCHLIST ({stage1.elapsed_seconds}s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Scanned:</b> {stage1.total_fo_scanned} F&amp;O Stocks | "
            f"<b>NR4+Vol≥1.3x:</b> {stage1.nr4_count} | <b>ATR Backup:</b> {stage1.backup_count}\n\n"
            f"📝 <b>Shortlisted ({len(stage1.shortlist)}):</b>\n{block}\n\n"
            f"⏭️ Next: 09:07 Pre-Open + 09:25 Live Sniper Filter"
        )
        return self.send_message(msg)

    def notify_stock_selection(self, res) -> bool:
        sec_lines = "\n".join(
            f"  • {s['index']}: <code>{s['percentChange']:+.2f}%</code>"
            for s in res.top_sectors[:3]
        ) or "  • N/A"

        cand_lines = []
        for c in res.candidates:
            mark = "✅" if c.selected else ("⭐" if c.reason.startswith("A+") else "❌")
            cand_lines.append(
                f"{mark} <b>{c.symbol}</b> ({c.stock_pct:+.2f}%) | {c.best_sector} ({c.sector_pct:+.2f}%)\n"
                f"   <i>{c.reason}</i>"
            )
        cand_block = "\n".join(cand_lines) if cand_lines else "No candidates passed Stage 1 / Double Breadth Lock."
        watchlist = ", ".join(res.selected_symbols) if res.selected_symbols else "NONE (No Trade)"
        lock_txt = "LOCKED ✅" if res.breadth_locked else ("MISMATCH ⚠️ (Half Qty)" if res.qty_multiplier == 0.5 else "MISMATCH ❌ (Skip Day)")

        msg = (
            f"<b>🎯 [STAGE 2: 09:25 IST] SNIPER WATCHLIST ({res.elapsed_seconds}s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔐 <b>Double Breadth Lock:</b> <b>{lock_txt}</b>\n"
            f"  • 09:07 Pre-Open: Adv {res.preopen_adv} vs Dec {res.preopen_dec}\n"
            f"  • 09:25 Live Mkt: Adv {res.live_adv} vs Dec {res.live_dec}\n"
            f"🧭 <b>Bias:</b> <b>{res.bias}</b> | <b>Nifty 50:</b> <code>{res.nifty50_pct:+.2f}%</code>\n\n"
            f"🏭 <b>Top Qualifying Sectors:</b>\n{sec_lines}\n\n"
            f"🔍 <b>Stage 1 ({res.stage1_count}) → Stage 2 Sniper Audit:</b>\n{cand_block}\n\n"
            f"🏆 <b>Final Sniper Watchlist (1-2 Stocks):</b> <b>{watchlist}</b>\n"
            f"⚙️ <i>Execution: Ignore first 3 5m bars (09:15-09:25) → Lowest-Vol Candle ≤ 0.5%</i>"
        )
        return self.send_message(msg)

    def notify_alert_candle(self, ac, shifted: bool = False, prev_min_vol: Optional[int] = None) -> bool:
        tag = "🔄 ALERT CANDLE SHIFTED (Lower Vol)" if shifted else "👀 ALERT CANDLE ARMED"
        color_lbl = "Red Candle" if ac.direction == "LONG" else "Green Candle"
        brk_lbl = f"High > ₹{ac.trigger_price:.2f}" if ac.direction == "LONG" else f"Low < ₹{ac.trigger_price:.2f}"
        prev_txt = f" (prev min: {prev_min_vol:,})" if prev_min_vol else ""
        msg = (
            f"<b>{tag} — {ac.symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🕯️ <b>5m {color_lbl} @ {ac.timestamp} IST</b>\n"
            f"📊 <b>Day's Lowest Vol:</b> <code>{ac.volume:,}</code>{prev_txt}\n"
            f"📏 <b>Candle Size:</b> <code>{ac.size_pct:.3f}%</code> (≤ 0.5% ✅)\n"
            f"🎯 <b>Trigger ({ac.direction}):</b> {brk_lbl}\n"
            f"🛡️ <b>Planned SL:</b> <code>₹{ac.stop_loss:.2f}</code> | <b>1:2 T1:</b> <code>₹{ac.target_1:.2f}</code>"
        )
        return self.send_message(msg)

    def notify_entry(self, pos: Dict) -> bool:
        msg = (
            f"<b>🚀 [ENTRY EXECUTED] {pos['direction']} {pos['symbol']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Entry Time:</b> {pos['entry_time']} IST\n"
            f"💰 <b>Entry Price:</b> <code>₹{pos['entry_price']:.2f}</code>\n"
            f"📦 <b>Quantity:</b> <code>{pos['initial_qty']} shares</code>\n"
            f"🛡️ <b>Stop Loss:</b> <code>₹{pos['stop_loss']:.2f}</code> (Risk ₹{pos['risk_per_share']:.2f}/sh)\n"
            f"🎯 <b>Target 1 (1:2 RR):</b> <code>₹{pos['target_1']:.2f}</code> (Book 50%)\n"
            f"🕒 <b>Rest 50% Hold:</b> Until 15:15 PM IST\n"
            f"🕯️ <i>Alert Candle: {pos['alert_candle_time']} | Vol: {pos['alert_candle_vol']:,} | Size: {pos['alert_size_pct']}%</i>"
        )
        return self.send_message(msg)

    def notify_target_1(self, pos: Dict, book_qty: int, exit_price: float, pnl_inr: float, exit_time: str) -> bool:
        msg = (
            f"<b>💰 [TARGET 1 HIT - 1:2 RR] {pos['symbol']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Time:</b> {exit_time} IST\n"
            f"📦 <b>Booked 50% Qty:</b> <code>{book_qty} shares</code> @ <code>₹{exit_price:.2f}</code>\n"
            f"💵 <b>Realized P&L:</b> <code>₹{pnl_inr:+,.2f}</code>\n"
            f"⏳ <b>Remaining Qty:</b> <code>{pos['remaining_qty']} shares</code> (Hold till 15:15 IST | SL ₹{pos['stop_loss']:.2f})"
        )
        return self.send_message(msg)

    def notify_exit(
        self, pos: Dict, qty: int, exit_price: float, pnl_inr: float, reason: str, exit_time: str
    ) -> bool:
        emoji = "✅" if pnl_inr >= 0 else "🛑"
        msg = (
            f"<b>{emoji} [POSITION CLOSED] {pos['symbol']} ({pos['direction']})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ <b>Exit Time:</b> {exit_time} IST\n"
            f"📌 <b>Reason:</b> {reason}\n"
            f"📦 <b>Qty Exited:</b> <code>{qty} shares</code> @ <code>₹{exit_price:.2f}</code>\n"
            f"💵 <b>Leg P&L:</b> <code>₹{pnl_inr:+,.2f}</code>"
        )
        return self.send_message(msg)

    def notify_daily_summary(
        self, date_str: str, bias: str, selected_stocks: str, trades_list: List[Dict]
    ) -> bool:
        total_pnl = sum(t.get("pnl_inr", 0.0) for t in trades_list)
        emoji = "🟢" if total_pnl > 0 else ("🔴" if total_pnl < 0 else "⚪")
        msg = (
            f"<b>📋 [15:15 IST EOD SUMMARY] {date_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🧭 <b>Day Bias:</b> {bias}\n"
            f"👀 <b>Watchlist:</b> {selected_stocks}\n"
            f"🔢 <b>Executed Legs:</b> {len(trades_list)}\n"
            f"{emoji} <b>Total Day P&L:</b> <code>₹{total_pnl:+,.2f}</code>"
        )
        return self.send_message(msg)


notifier = TelegramNotifier()
