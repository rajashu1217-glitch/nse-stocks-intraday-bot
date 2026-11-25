"""
Render Cloud Web Service + Live IST Intraday Scheduler
Runs 24/7 in Indian Standard Time (Asia/Kolkata):
  - 09:07 AM IST: NSE Pre-Open Advances vs Declines check -> Telegram Alert #1
  - 09:25 AM IST: Top 5 OI % + Sector/Mover Stock Selection -> Telegram Alert #2
  - 09:30 AM - 15:15 PM IST: 5m Lowest-Volume Alert Candle Scanner & Paper Execution
  - 15:15 PM IST: Auto Square-Off + Excel Report -> Telegram EOD Summary
  - Mobile-friendly Dashboard at '/', '/health' (GET/HEAD), '/download', '/test-simulation', '/live-scan'
"""

import os
import threading
import time
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
import pandas as pd
import uvicorn

from config import IST, config
from strategy_engine import NseStocksStrategyEngine

app = FastAPI(title="NSE Stocks Intraday Bot (IST)")

bot_state = {
    "status": "Initializing IST Scheduler...",
    "session_date": None,
    "bias": "Pending (09:07 IST)",
    "advances": 0,
    "declines": 0,
    "watchlist": [],
    "alert_candles": {},
    "active_trade": None,
    "last_ist_check": None,
    "is_simulation_running": False,
}

live_engine: NseStocksStrategyEngine | None = None
engine_lock = threading.Lock()


def get_ist_now() -> datetime:
    return datetime.now(IST)


def _sync_state(engine: NseStocksStrategyEngine):
    if engine.preopen_breadth:
        bot_state["bias"] = engine.preopen_breadth.get("bias", "NEUTRAL")
        bot_state["advances"] = engine.preopen_breadth.get("advances", 0)
        bot_state["declines"] = engine.preopen_breadth.get("declines", 0)
    if engine.selection_result:
        bot_state["watchlist"] = list(engine.selection_result.selected_symbols)
    bot_state["alert_candles"] = {
        sym: {
            "time": sc.alert_candle.timestamp,
            "vol": sc.alert_candle.volume,
            "size_pct": sc.alert_candle.size_pct,
            "trigger": sc.alert_candle.trigger_price,
            "sl": sc.alert_candle.stop_loss,
        }
        for sym, sc in engine.scanners.items()
        if sc.alert_candle is not None
    }
    bot_state["active_trade"] = engine.active_trade


def run_scheduler_daemon():
    """Background 24/7 IST Trading Scheduler."""
    global live_engine
    print("🚀 NSE Stocks Intraday Scheduler started (Asia/Kolkata IST)...")

    did_0907 = False
    did_0925 = False
    did_1515 = False
    current_day = None

    while True:
        now = get_ist_now()
        today_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")
        weekday = now.weekday()  # 0=Mon .. 4=Fri

        bot_state["last_ist_check"] = now.strftime("%Y-%m-%d %H:%M:%S IST")

        # Reset daily flags on a new calendar date
        if current_day != today_str:
            current_day = today_str
            bot_state["session_date"] = today_str
            did_0907 = False
            did_0925 = False
            did_1515 = False
            with engine_lock:
                live_engine = NseStocksStrategyEngine(mode=config.DATA_MODE)
            bot_state["bias"] = "Pending (09:07 IST)"
            bot_state["watchlist"] = []
            bot_state["alert_candles"] = {}
            bot_state["active_trade"] = None

        # Weekend check
        if weekday >= 5:
            bot_state["status"] = "Weekend (NSE Closed)"
            time.sleep(600)
            continue

        try:
            # Step 1: 09:07 AM IST Pre-open Breadth (catch-up allowed up to 09:24)
            if (not did_0907) and ("09:07" <= time_str < "09:25"):
                bot_state["status"] = "09:07 IST: Fetching NSE Pre-Open Breadth..."
                with engine_lock:
                    live_engine.step_0907_preopen()
                    _sync_state(live_engine)
                did_0907 = True

            # Step 2: 09:25 AM IST Stock Selection (catch-up allowed up to 15:10)
            if (not did_0925) and ("09:25" <= time_str < "15:10"):
                bot_state["status"] = "09:25 IST: Selecting Top OI % + Sector/Mover Stocks..."
                with engine_lock:
                    if not did_0907:
                        live_engine.step_0907_preopen()
                        did_0907 = True
                    live_engine.step_0925_selection()
                    _sync_state(live_engine)
                did_0925 = True

            # Step 3: 09:30 AM - 15:15 PM IST Live 5m Price Action Scanner
            if "09:30" <= time_str < "15:15":
                bot_state["status"] = "09:30-15:15 IST: Live 5m Price Action Scanning"
                with engine_lock:
                    live_engine.poll_live_market()
                    _sync_state(live_engine)
                time.sleep(20)
                continue

            # Step 4: 15:15 PM IST EOD Exit & Excel Summary
            if (not did_1515) and (time_str >= "15:15"):
                bot_state["status"] = "15:15 IST: Finalizing EOD Exits & Report..."
                with engine_lock:
                    if did_0925:
                        live_engine.finalize_eod()
                    _sync_state(live_engine)
                did_1515 = True

            if time_str < "09:07":
                bot_state["status"] = "Idle - Waiting for 09:07 AM IST Pre-Open"
            elif "09:07" <= time_str < "09:25":
                bot_state["status"] = "Pre-Open Done - Waiting for 09:25 AM IST Selection"
            elif "09:25" <= time_str < "09:30":
                bot_state["status"] = "Watchlist Ready - Waiting for 09:30 AM IST Scan"
            else:
                bot_state["status"] = "Market Closed - Waiting for Next Trading Day (09:07 IST)"

        except Exception as exc:
            bot_state["status"] = f"Scheduler Error (retrying): {exc}"

        time.sleep(20)


@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=run_scheduler_daemon, daemon=True)
    t.start()


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {
        "status": "healthy",
        "timestamp_ist": get_ist_now().isoformat(),
        "bot_status": bot_state["status"],
    }


@app.get("/download")
def download_excel():
    if config.EXCEL_LOG_PATH.exists():
        return FileResponse(
            path=config.EXCEL_LOG_PATH,
            filename="stocks_papertrade_log.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    return JSONResponse(status_code=404, content={"error": "Excel log not generated yet."})


@app.post("/test-simulation")
def trigger_simulation(bg_tasks: BackgroundTasks, bias: str = "LONG"):
    if bot_state["is_simulation_running"]:
        return {"status": "Simulation already running."}

    def run_sim():
        bot_state["is_simulation_running"] = True
        try:
            sim_engine = NseStocksStrategyEngine(mode="simulated")
            sim_engine.run_simulation(bias_scenario=bias.upper(), outcome="win")
            _sync_state(sim_engine)
        finally:
            bot_state["is_simulation_running"] = False

    bg_tasks.add_task(run_sim)
    return {"status": f"Simulation ({bias.upper()}) started! Check Telegram in a few seconds."}


@app.post("/live-scan")
def trigger_live_scan(bg_tasks: BackgroundTasks):
    """Manually run a live NSE 09:07 + 09:25 selection snapshot and notify Telegram."""
    def run_scan():
        snap = NseStocksStrategyEngine(mode="live")
        snap.step_0907_preopen()
        snap.step_0925_selection()
        _sync_state(snap)

    bg_tasks.add_task(run_scan)
    return {"status": "Live NSE selection snapshot triggered! Check Telegram."}


@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def dashboard():
    trades_html = "<p class='text-secondary'>No trades recorded yet.</p>"
    if config.EXCEL_LOG_PATH.exists():
        try:
            df = pd.read_excel(config.EXCEL_LOG_PATH, sheet_name="Trade_Log")
            if not df.empty:
                rows = ""
                for _, r in df.tail(10).iloc[::-1].iterrows():
                    pnl = float(r.get("P&L INR (₹)", 0) or 0)
                    color = "#22c55e" if pnl > 0 else ("#ef4444" if pnl < 0 else "#94a3b8")
                    rows += (
                        f"<tr>"
                        f"<td>{r.get('Date', '')} {r.get('Time (IST)', '')}</td>"
                        f"<td><b>{r.get('Stock', '')}</b></td>"
                        f"<td>{r.get('Action', '')}</td>"
                        f"<td>₹{float(r.get('Entry Price (₹)', 0) or 0):.2f}</td>"
                        f"<td>₹{r.get('Exit Price (₹)', '-')}</td>"
                        f"<td style='color:{color};font-weight:bold;'>₹{pnl:+,.2f}</td>"
                        f"<td><small>{r.get('Exit Reason', '')}</small></td>"
                        f"</tr>"
                    )
                trades_html = (
                    "<div class='table-responsive'>"
                    "<table class='table table-dark table-striped align-middle'>"
                    "<thead><tr><th>Time (IST)</th><th>Stock</th><th>Action</th>"
                    "<th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr></thead>"
                    f"<tbody>{rows}</tbody></table></div>"
                )
        except Exception as exc:
            trades_html = f"<p class='text-danger'>Excel read error: {exc}</p>"

    watchlist_str = ", ".join(bot_state["watchlist"]) if bot_state["watchlist"] else "Pending (09:25 IST)"
    ist_now_str = get_ist_now().strftime("%d-%b-%Y %H:%M:%S IST")

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>NSE Stocks Intraday Bot (IST)</title>
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
      <style>
        body {{ background-color: #0f172a; color: #f8fafc; padding: 20px 10px; font-family: system-ui, sans-serif; }}
        .card {{ background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; margin-bottom: 18px; }}
      </style>
    </head>
    <body>
      <div class="container" style="max-width: 860px;">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <div>
            <h3 class="mb-1">📈 NSE Stocks Intraday Bot</h3>
            <span class="badge bg-success">● IST CLOUD 24/7 ACTIVE</span>
            <small class="text-secondary ms-2">{ist_now_str}</small>
          </div>
          <a href="/download" class="btn btn-outline-success btn-sm">📥 Download Excel</a>
        </div>

        <div class="card p-3">
          <h5 class="text-info mb-3">⚙️ Live IST Status</h5>
          <div class="row g-2 text-center">
            <div class="col-6 col-md-3">
              <div class="p-2 border border-secondary rounded">
                <small class="text-secondary">Scheduler State</small>
                <div class="fw-bold text-warning small">{bot_state['status']}</div>
              </div>
            </div>
            <div class="col-6 col-md-3">
              <div class="p-2 border border-secondary rounded">
                <small class="text-secondary">09:07 Bias</small>
                <div class="fw-bold text-info">{bot_state['bias']} (A:{bot_state['advances']} / D:{bot_state['declines']})</div>
              </div>
            </div>
            <div class="col-12 col-md-6">
              <div class="p-2 border border-secondary rounded">
                <small class="text-secondary">09:25 Selected Watchlist</small>
                <div class="fw-bold text-success">{watchlist_str}</div>
              </div>
            </div>
          </div>
          <div class="mt-3 d-flex flex-wrap gap-2 justify-content-center">
            <button onclick="callApi('/test-simulation?bias=LONG')" class="btn btn-primary btn-sm">🧪 Test LONG Simulation</button>
            <button onclick="callApi('/test-simulation?bias=SHORT')" class="btn btn-danger btn-sm">🧪 Test SHORT Simulation</button>
            <button onclick="callApi('/live-scan')" class="btn btn-outline-info btn-sm">🔍 Run Live NSE Scan Now</button>
          </div>
          <div id="apiMsg" class="mt-2 text-center text-success small" style="display:none;"></div>
        </div>

        <div class="card p-3">
          <h5 class="text-info mb-2">📊 Paper Trading Log</h5>
          {trades_html}
        </div>
      </div>
      <script>
        async function callApi(url) {{
          const res = await fetch(url, {{ method: 'POST' }});
          const data = await res.json();
          const el = document.getElementById('apiMsg');
          el.innerText = data.status;
          el.style.display = 'block';
        }}
      </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
