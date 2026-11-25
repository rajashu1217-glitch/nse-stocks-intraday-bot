"""
CLI Entrypoint for NSE Stocks Intraday Bot
Usage:
  python main.py --mode test          # Full simulated LONG run + Telegram + Excel
  python main.py --mode test-short    # Full simulated SHORT run + Telegram + Excel
  python main.py --test-selection     # Run live NSE 09:07 + 09:25 selection now
  python main.py --test-telegram      # Verify Telegram bot connection
  python main.py --mode live          # Start FastAPI server + IST scheduler
"""

import argparse
import logging
import os
import uvicorn

from config import config
from strategy_engine import NseStocksStrategyEngine
from telegram_notifier import notifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    parser = argparse.ArgumentParser(description="NSE Stocks Intraday Bot (IST)")
    parser.add_argument("--mode", choices=["test", "test-short", "live"], default="test")
    parser.add_argument("--test-selection", action="store_true", help="Run live NSE stock selection snapshot")
    parser.add_argument("--test-telegram", action="store_true", help="Send a test Telegram message")
    args = parser.parse_args()

    if args.test_telegram:
        ok = notifier.send_message("✅ <b>NSE Stocks Intraday Bot</b> connected successfully (IST)!")
        print("Telegram OK:" if ok else "Telegram failed (check token/chat_id)")
        return

    if args.test_selection:
        engine = NseStocksStrategyEngine(mode="live")
        engine.step_0907_preopen()
        res = engine.step_0925_selection()
        print(f"Selection finished in {res.elapsed_seconds}s -> {res.selected_symbols}")
        return

    if args.mode == "test":
        engine = NseStocksStrategyEngine(mode="simulated")
        trades = engine.run_simulation(bias_scenario="LONG", outcome="win")
        print(f"LONG simulation complete. Trade legs logged: {len(trades)}")
    elif args.mode == "test-short":
        engine = NseStocksStrategyEngine(mode="simulated")
        trades = engine.run_simulation(bias_scenario="SHORT", outcome="win")
        print(f"SHORT simulation complete. Trade legs logged: {len(trades)}")
    elif args.mode == "live":
        port = int(os.environ.get("PORT", 10000))
        uvicorn.run("server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
