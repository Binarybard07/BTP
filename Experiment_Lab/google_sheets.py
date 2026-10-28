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
import sys
from typing import Optional
from dotenv import load_dotenv
import gspread
from gspread.exceptions import WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

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


def format_google_sheet(sheet: gspread.Spreadsheet, wks: gspread.Worksheet):
    """Freeze header row and make header bold."""
    try:
        props = wks._properties
        sheet_id = props["sheetId"]

        requests = [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
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
        sheet.batch_update({"requests": requests})
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
        format_google_sheet(sheet, wks)
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


if __name__ == "__main__":
    sync_metrics_to_sheets()
