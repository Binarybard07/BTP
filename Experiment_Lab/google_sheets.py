#!/usr/bin/env python3
"""
Experiment Lab - Google Sheets Sync Agent
=========================================
Synchronizes Backtest_Results/metrics.xlsx ('Metrics' sheet) to Google Sheets.
- Authenticates via ServiceAccountCredentials from SERVICE_JSON (.env)
- Applies sheet formatting: Freeze row 1, bold headers
- Handles missing credentials gracefully without breaking local pipeline
- Direct execution without flags: `python google_sheets.py`
"""

import logging
import os
from pathlib import Path
import string
import sys
import time
from typing import Optional
from dotenv import load_dotenv
import gspread
from gspread.exceptions import WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

load_dotenv()

# ─────────────────────────────────────────────
# PATHS CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
BACKTEST_DIR = BASE_DIR / "Backtest_Results"
METRICS_FILE = BACKTEST_DIR / "metrics.xlsx"
LOG_FILE = BASE_DIR / "google_sheets.log"

SERVICE_JSON = os.getenv("SERVICE_JSON")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Metrics")

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logger = logging.getLogger("google_sheets")
logger.setLevel(logging.INFO)

if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)


def get_gspread_client() -> Optional[gspread.Client]:
    """Authenticate and return gspread client, or None if credentials are not configured."""
    if not SERVICE_JSON:
        logger.info("SERVICE_JSON not defined in environment. Remote Google Sheets sync is disabled.")
        return None

    key_path = Path(SERVICE_JSON)
    if not key_path.is_absolute():
        key_path = PROJECT_ROOT / key_path

    if not key_path.exists():
        logger.warning(f"Google Sheets service account key file not found at: {key_path}. Skipping remote sync.")
        return None

    if not SPREADSHEET_ID:
        logger.warning("SPREADSHEET_ID not defined in environment. Skipping remote sync.")
        return None

    try:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(str(key_path), scope)
        client = gspread.authorize(creds)
        logger.info("Successfully authenticated with Google Sheets API.")
        return client
    except Exception as e:
        logger.error(f"Failed authenticating with Google Sheets API: {e}")
        return None


def idx_to_col(col_idx: int) -> str:
    """Turn 0-based column index to A, B, ... Z, AA, etc."""
    letters = []
    while True:
        col_idx, rem = divmod(col_idx, 26)
        letters.append(string.ascii_uppercase[rem])
        if col_idx == 0:
            break
        col_idx -= 1
    return "".join(reversed(letters))


def format_google_sheet(sheet: gspread.Spreadsheet, wks: gspread.Worksheet, df: pd.DataFrame):
    """
    Apply sheet formatting:
      1) Freeze header row
      2) Bold header row
      3) Highlight rows where Score > 0 in light green (#C6EFCE)
    """
    try:
        props = wks._properties
        sheet_id = props["sheetId"]
        ncols = len(df.columns)
        headers = list(df.columns)

        requests = [
            # 1. Freeze header row
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            # 2. Bold header row
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.horizontalAlignment",
                }
            },
        ]

        # Clear existing conditional formatting rules on this sheet to prevent duplication
        try:
            meta = sheet.fetch_sheet_metadata({"includeGridData": False})
            for s in meta.get("sheets", []):
                if s.get("properties", {}).get("sheetId") == sheet_id:
                    num_cf = len(s.get("conditionalFormats", []))
                    for _ in range(num_cf):
                        requests.append({
                            "deleteConditionalFormatRule": {
                                "sheetId": sheet_id,
                                "index": 0,
                            }
                        })
                    break
        except Exception as e:
            logger.debug(f"Could not inspect existing conditional format rules: {e}")

        # 3. Add conditional formatting rule: highlight row light-green if Score > 0
        if "Score" in headers:
            score_idx = headers.index("Score")
            col_letter = idx_to_col(score_idx)
            cf_range = {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": ncols,
            }
            cf_rule = {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [cf_range],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{"userEnteredValue": f"=${col_letter}2>0"}],
                            },
                            "format": {
                                "backgroundColor": {
                                    "red": 0.776,
                                    "green": 0.937,
                                    "blue": 0.808,
                                }
                            },
                        },
                    },
                    "index": 0,
                }
            }
            requests.append(cf_rule)

        sheet.batch_update({"requests": requests})
        logger.info(f"Installed sheet formatting for '{wks.title}' (freeze, bold, Score > 0 green highlight).")
    except Exception as e:
        logger.warning(f"Could not apply sheet formatting on {wks.title}: {e}")


def sync_tab(sheet: gspread.Spreadsheet, title: str, df: pd.DataFrame):
    """Sync a single DataFrame into a designated Google Sheets worksheet."""
    if df.empty:
        logger.info(f"DataFrame for tab '{title}' is empty, skipping upload.")
        return

    try:
        try:
            wks = sheet.worksheet(title)
        except WorksheetNotFound:
            wks = sheet.add_worksheet(title=title, rows=len(df) + 10, cols=len(df.columns) + 5)
            logger.info(f"Created new worksheet: '{title}'")

        clean_df = df.fillna("")
        values = [clean_df.columns.values.tolist()] + clean_df.values.tolist()

        wks.clear()
        wks.update(range_name="A1", values=values)
        format_google_sheet(sheet, wks, clean_df)
        logger.info(f"Successfully uploaded {len(clean_df)} rows to tab '{title}'.")

    except Exception as e:
        logger.exception(f"Error syncing tab '{title}': {e}")


def sync_metrics_to_sheets() -> bool:
    """Full resync of metrics.xlsx to Google Sheets."""
    if not METRICS_FILE.exists() or METRICS_FILE.stat().st_size == 0:
        logger.info(f"Local {METRICS_FILE.name} does not exist yet. Nothing to sync.")
        return False

    client = get_gspread_client()
    if client is None:
        return False

    try:
        sheet = client.open_by_key(SPREADSHEET_ID)
        logger.info(f"Opened target Google Spreadsheet: '{sheet.title}' ({SPREADSHEET_ID})")

        # Sync Main Metrics tab
        try:
            df_metrics = pd.read_excel(METRICS_FILE, sheet_name="Metrics", engine="openpyxl")
            sync_tab(sheet, WORKSHEET_NAME, df_metrics)
        except Exception as e:
            logger.warning(f"Could not read sheet 'Metrics' from {METRICS_FILE}: {e}")

        return True

    except Exception as e:
        logger.exception(f"Failed full resync to Google Sheets: {e}")
        return False


class MetricsHandler(FileSystemEventHandler):
    """Watches Backtest_Results/ directory for modifications to metrics.xlsx."""

    def __init__(self):
        super().__init__()
        self._last_sync = 0.0

    def on_modified(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).name == "metrics.xlsx":
            now = time.time()
            if now - self._last_sync < 2.0:
                return
            self._last_sync = now
            logger.info("Detected change to metrics.xlsx; syncing to Google Sheets...")
            time.sleep(1.0)
            sync_metrics_to_sheets()

    def on_created(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).name == "metrics.xlsx":
            logger.info("Detected creation of metrics.xlsx; syncing to Google Sheets...")
            time.sleep(1.0)
            sync_metrics_to_sheets()


def main():
    """Run Google Sheets sync agent in continuous observer mode, syncing pending metrics first."""
    logger.info("Google Sheets agent starting up...")
    # Initial sync if metrics.xlsx already exists
    if METRICS_FILE.exists() and METRICS_FILE.stat().st_size > 0:
        logger.info(f"Initial sync of existing {METRICS_FILE.name}...")
        sync_metrics_to_sheets()
    else:
        logger.info(f"No existing {METRICS_FILE.name} found at startup. Waiting for updates...")

    logger.info(f"Watching for changes to {METRICS_FILE.name} in {BACKTEST_DIR}/ ...")
    handler = MetricsHandler()
    observer = Observer()
    observer.schedule(handler, str(BACKTEST_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested, stopping Google Sheets agent.")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
