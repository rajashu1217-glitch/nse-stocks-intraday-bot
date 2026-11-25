"""
Excel Paper-Trading Logger for NSE Stocks Intraday Setup
Maintains 'stocks_papertrade_log.xlsx' with:
  1. Trade_Log sheet (every Entry/T1/SL/EOD leg)
  2. Daily_Summary sheet (daily bias, watchlist, total P&L)
"""

from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from config import IST, config


class ExcelPaperTradeLogger:
    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or config.EXCEL_LOG_PATH
        self._init_workbook()

    def _init_workbook(self):
        if not self.file_path.exists():
            wb = openpyxl.Workbook()
            ws_trades = wb.active
            ws_trades.title = "Trade_Log"
            self._apply_sheet_headers(
                ws_trades,
                [
                    "Trade ID", "Date", "Time (IST)", "Stock", "Direction",
                    "Action", "Quantity", "Entry Price (₹)", "Exit Price (₹)",
                    "Stop Loss (₹)", "Target 1 (₹)", "P&L Pts", "P&L INR (₹)",
                    "Return %", "Exit Reason", "Remarks",
                ],
            )
            ws_summary = wb.create_sheet(title="Daily_Summary")
            self._apply_sheet_headers(
                ws_summary,
                [
                    "Date", "Market Bias", "Selected Watchlist", "Total Legs",
                    "Wins", "Losses", "Total P&L (₹)", "Day Outcome",
                ],
            )
            wb.save(self.file_path)

    def _apply_sheet_headers(self, sheet, headers: List[str]):
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")
        border = Border(
            left=Side(style="thin", color="CCCCCC"),
            right=Side(style="thin", color="CCCCCC"),
            top=Side(style="thin", color="CCCCCC"),
            bottom=Side(style="medium", color="1B365D"),
        )
        sheet.append(headers)
        for col_num in range(1, len(headers) + 1):
            cell = sheet.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border
        sheet.row_dimensions[1].height = 28

    def log_trade_event(self, trade: Dict):
        wb = openpyxl.load_workbook(self.file_path)
        ws = wb["Trade_Log"]

        now_ist = datetime.now(IST)
        row_data = [
            trade.get("trade_id", ""),
            trade.get("date", now_ist.strftime("%Y-%m-%d")),
            trade.get("time", now_ist.strftime("%H:%M:%S")),
            trade.get("symbol", ""),
            trade.get("direction", ""),
            trade.get("action", ""),
            trade.get("quantity", 0),
            round(trade.get("entry_price", 0.0), 2),
            round(trade.get("exit_price", 0.0), 2) if trade.get("exit_price") is not None else "-",
            round(trade.get("stop_loss", 0.0), 2),
            round(trade.get("target_1", 0.0), 2),
            round(trade.get("pnl_pts", 0.0), 2),
            round(trade.get("pnl_inr", 0.0), 2),
            f"{trade.get('return_pct', 0.0):+.2f}%",
            trade.get("exit_reason", ""),
            trade.get("remarks", ""),
        ]
        ws.append(row_data)
        row_idx = ws.max_row
        ws.row_dimensions[row_idx].height = 22

        pnl_inr = trade.get("pnl_inr", 0.0)
        win_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
        loss_fill = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")
        win_font = Font(name="Calibri", size=10, bold=True, color="2E7D32")
        loss_font = Font(name="Calibri", size=10, bold=True, color="C62828")
        reg_font = Font(name="Calibri", size=10)

        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = reg_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if col_idx in (12, 13) and pnl_inr != 0.0:
                cell.fill = win_fill if pnl_inr > 0 else loss_fill
                cell.font = win_font if pnl_inr > 0 else loss_font

        self._auto_fit_columns(ws)
        wb.save(self.file_path)

    def log_daily_summary(
        self,
        date_str: str,
        bias: str,
        selected_stocks: str,
        trades_list: List[Dict],
        day_status: str,
    ):
        wb = openpyxl.load_workbook(self.file_path)
        ws = wb["Daily_Summary"]

        wins = sum(1 for t in trades_list if t.get("pnl_inr", 0.0) > 0)
        losses = sum(1 for t in trades_list if t.get("pnl_inr", 0.0) < 0)
        total_pnl = round(sum(t.get("pnl_inr", 0.0) for t in trades_list), 2)

        row_data = [
            date_str,
            bias,
            selected_stocks,
            len(trades_list),
            wins,
            losses,
            total_pnl,
            day_status,
        ]
        ws.append(row_data)
        row_idx = ws.max_row
        ws.row_dimensions[row_idx].height = 24

        win_fill = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
        loss_fill = PatternFill(start_color="FFEBEE", end_color="FFEBEE", fill_type="solid")
        win_font = Font(name="Calibri", size=10, bold=True, color="2E7D32")
        loss_font = Font(name="Calibri", size=10, bold=True, color="C62828")

        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if col_idx == 7 and total_pnl != 0:
                cell.fill = win_fill if total_pnl > 0 else loss_fill
                cell.font = win_font if total_pnl > 0 else loss_font

        self._auto_fit_columns(ws)
        wb.save(self.file_path)

    def _auto_fit_columns(self, sheet):
        for col in sheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                max_len = max(max_len, len(str(cell.value or "")))
            sheet.column_dimensions[col_letter].width = max(max_len + 4, 12)


logger_excel = ExcelPaperTradeLogger()
