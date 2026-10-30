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
import openpyxl
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
WORKSHEET_NAME_MATRIX = os.getenv("WORKSHEET_NAME_MATRIX", f"{WORKSHEET_NAME}_Matrix" if WORKSHEET_NAME else "Matrix")


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


def sync_matrix_tab(sheet: gspread.Spreadsheet, title: str):
    """Sync the 2-tier formatted Matrix sheet from metrics.xlsx to Google Sheets."""
    if not METRICS_FILE.exists():
        return

    try:
        wb = openpyxl.load_workbook(METRICS_FILE, data_only=True)
        if "Matrix" not in wb.sheetnames:
            logger.info("No 'Matrix' sheet in metrics.xlsx, skipping matrix tab sync.")
            return
        ws = wb["Matrix"]

        matrix_values = []
        for row in ws.iter_rows(values_only=True):
            matrix_values.append(["" if cell is None else cell for cell in row])

        if not matrix_values:
            return

        try:
            wks = sheet.worksheet(title)
        except WorksheetNotFound:
            wks = sheet.add_worksheet(title=title, rows=len(matrix_values) + 15, cols=15)
            logger.info(f"Created new worksheet: '{title}'")

        wks.clear()
        wks.update(range_name="A1", values=matrix_values)

        props = wks._properties
        sheet_id = props["sheetId"]
        num_rows = len(matrix_values)

        requests = [
            # 1. Freeze top 2 header rows
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 2},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
        ]

        # 2. Unmerge any existing merges on this worksheet first
        try:
            meta = sheet.fetch_sheet_metadata({"includeGridData": False})
            for s in meta.get("sheets", []):
                if s.get("properties", {}).get("sheetId") == sheet_id:
                    for m in s.get("merges", []):
                        requests.append({"unmergeCells": {"range": m}})
                    break
        except Exception as e:
            logger.debug(f"Could not inspect merges: {e}")

        # 3. Header Merges
        # Cols A-E (0-4): Rows 1 & 2 merged vertically
        for c in range(5):
            requests.append({
                "mergeCells": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": c, "endColumnIndex": c + 1},
                    "mergeType": "MERGE_ALL"
                }
            })
        # Strategy: Cols F-G (5-7) in Row 1 (index 0)
        requests.append({
            "mergeCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 7},
                "mergeType": "MERGE_ALL"
            }
        })
        # Index: Cols H-I (7-9) in Row 1 (index 0)
        requests.append({
            "mergeCells": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 7, "endColumnIndex": 9},
                "mergeType": "MERGE_ALL"
            }
        })

        # 4. Header Styling (Choice 1C: Steel Graphite & Frost)
        # Frosted Silver Gray for A-E (Cols 0-5, Rows 1-2)
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 5},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.898, "green": 0.906, "blue": 0.922},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.067, "green": 0.094, "blue": 0.153}},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
            }
        })
        # Strategy Super Header (Row 1, Cols 5-7): Subtle Frosted Indigo
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 5, "endColumnIndex": 7},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.878, "green": 0.906, "blue": 1.0},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.216, "green": 0.188, "blue": 0.639}},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
            }
        })
        # Strategy Sub Headers (Row 2, Cols 5-7): Pale Indigo
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 5, "endColumnIndex": 7},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.933, "green": 0.949, "blue": 1.0},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.263, "green": 0.220, "blue": 0.792}},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
            }
        })
        # Index Super Header (Row 1, Cols 7-9): Neutral Zinc
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 7, "endColumnIndex": 9},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.957, "green": 0.957, "blue": 0.961},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.153, "green": 0.153, "blue": 0.165}},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
            }
        })
        # Index Sub Headers (Row 2, Cols 7-9): Pale Zinc
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 7, "endColumnIndex": 9},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.980, "green": 0.980, "blue": 0.980},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.322, "green": 0.322, "blue": 0.357}},
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE"
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)"
            }
        })
        # Header Bottom Border Divider
        requests.append({
            "updateBorders": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 9},
                "bottom": {"style": "SOLID_MEDIUM", "color": {"red": 0.294, "green": 0.333, "blue": 0.388}},
            }
        })

        # 5. Data Rows Formatting (Choice 1C)
        if num_rows > 2:
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": num_rows, "startColumnIndex": 0, "endColumnIndex": 9},
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE"
                        }
                    },
                    "fields": "userEnteredFormat(horizontalAlignment,verticalAlignment)"
                }
            })
            # Universe Column (Col 0): Frost White / Soft Slate #F3F4F6, bold dark slate text
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": num_rows, "startColumnIndex": 0, "endColumnIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.953, "green": 0.957, "blue": 0.965},
                            "textFormat": {"bold": True, "foregroundColor": {"red": 0.122, "green": 0.161, "blue": 0.216}}
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)"
                }
            })
            # Survivorship Column (Col 1): Bold dark text #374151
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": num_rows, "startColumnIndex": 1, "endColumnIndex": 2},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "foregroundColor": {"red": 0.216, "green": 0.255, "blue": 0.318}}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat)"
                }
            })
            # From Date Column (Col 2): Dark neutral text #374151
            requests.append({
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": num_rows, "startColumnIndex": 2, "endColumnIndex": 3},
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": False, "foregroundColor": {"red": 0.216, "green": 0.255, "blue": 0.318}}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat)"
                }
            })

            # Data block merges and borders for every 2 rows
            for r in range(2, num_rows, 2):
                r_end = min(r + 2, num_rows)
                if r + 1 < num_rows and matrix_values[r][2] == matrix_values[r + 1][2] and matrix_values[r][2] != "":
                    requests.append({
                        "mergeCells": {
                            "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r_end, "startColumnIndex": 2, "endColumnIndex": 3},
                            "mergeType": "MERGE_ALL"
                        }
                    })

                requests.append({
                    "updateBorders": {
                        "range": {"sheetId": sheet_id, "startRowIndex": r, "endRowIndex": r_end, "startColumnIndex": 0, "endColumnIndex": 9},
                        "bottom": {"style": "SOLID_MEDIUM", "color": {"red": 0.420, "green": 0.447, "blue": 0.502}},
                        "innerHorizontal": {"style": "SOLID", "color": {"red": 0.898, "green": 0.906, "blue": 0.922}},
                        "innerVertical": {"style": "SOLID", "color": {"red": 0.898, "green": 0.906, "blue": 0.922}},
                    }
                })

            # Gradients & Highlights per data cell
            for r_idx in range(2, num_rows):
                row_vals = matrix_values[r_idx]

                # Score (Col 4): Soft mint when > 0, muted gray when <= 0
                score_val = row_vals[4] if len(row_vals) > 4 else "N/A"
                if score_val != "N/A" and score_val != "":
                    try:
                        s_num = float(score_val)
                        if s_num > 0:
                            requests.append({
                                "repeatCell": {
                                    "range": {"sheetId": sheet_id, "startRowIndex": r_idx, "endRowIndex": r_idx + 1, "startColumnIndex": 4, "endColumnIndex": 5},
                                    "cell": {
                                        "userEnteredFormat": {
                                            "backgroundColor": {"red": 0.820, "green": 0.980, "blue": 0.898},
                                            "textFormat": {"bold": True, "foregroundColor": {"red": 0.024, "green": 0.373, "blue": 0.275}}
                                        }
                                    },
                                    "fields": "userEnteredFormat(backgroundColor,textFormat)"
                                }
                            })
                        else:
                            requests.append({
                                "repeatCell": {
                                    "range": {"sheetId": sheet_id, "startRowIndex": r_idx, "endRowIndex": r_idx + 1, "startColumnIndex": 4, "endColumnIndex": 5},
                                    "cell": {
                                        "userEnteredFormat": {
                                            "textFormat": {"italic": True, "foregroundColor": {"red": 0.612, "green": 0.639, "blue": 0.686}}
                                        }
                                    },
                                    "fields": "userEnteredFormat(textFormat)"
                                }
                            })
                    except (ValueError, TypeError):
                        pass

                # Strategy CAGR (Col 5) and Index CAGR (Col 7)
                for c_col in (5, 7):
                    cagr_val = row_vals[c_col] if len(row_vals) > c_col else "N/A"
                    if cagr_val != "N/A" and cagr_val != "":
                        try:
                            c_num = float(cagr_val)
                            if c_num >= 24.0:
                                bg = {"red": 0.776, "green": 0.965, "blue": 0.835}
                                fg = {"red": 0.016, "green": 0.471, "blue": 0.341}
                            elif c_num >= 20.0:
                                bg = {"red": 0.820, "green": 0.980, "blue": 0.898}
                                fg = {"red": 0.024, "green": 0.373, "blue": 0.275}
                            elif c_num >= 15.0:
                                bg = {"red": 0.996, "green": 0.953, "blue": 0.780}
                                fg = {"red": 0.573, "green": 0.251, "blue": 0.055}
                            else:
                                bg = {"red": 0.996, "green": 0.886, "blue": 0.886}
                                fg = {"red": 0.600, "green": 0.106, "blue": 0.106}
                            requests.append({
                                "repeatCell": {
                                    "range": {"sheetId": sheet_id, "startRowIndex": r_idx, "endRowIndex": r_idx + 1, "startColumnIndex": c_col, "endColumnIndex": c_col + 1},
                                    "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"bold": True, "foregroundColor": fg}}},
                                    "fields": "userEnteredFormat(backgroundColor,textFormat)"
                                }
                            })
                        except (ValueError, TypeError):
                            pass

                # Strategy Drawdown (Col 6) and Index Drawdown (Col 8)
                for d_col in (6, 8):
                    dd_val = row_vals[d_col] if len(row_vals) > d_col else "N/A"
                    if dd_val != "N/A" and dd_val != "":
                        try:
                            dd_num = float(dd_val)
                            if dd_num >= -15.0:
                                bg = {"red": 0.820, "green": 0.980, "blue": 0.898}
                                fg = {"red": 0.024, "green": 0.373, "blue": 0.275}
                            elif dd_num >= -20.0:
                                bg = {"red": 0.996, "green": 0.953, "blue": 0.780}
                                fg = {"red": 0.573, "green": 0.251, "blue": 0.055}
                            else:
                                bg = {"red": 0.996, "green": 0.886, "blue": 0.886}
                                fg = {"red": 0.600, "green": 0.106, "blue": 0.106}
                            requests.append({
                                "repeatCell": {
                                    "range": {"sheetId": sheet_id, "startRowIndex": r_idx, "endRowIndex": r_idx + 1, "startColumnIndex": d_col, "endColumnIndex": d_col + 1},
                                    "cell": {"userEnteredFormat": {"backgroundColor": bg, "textFormat": {"bold": True, "foregroundColor": fg}}},
                                    "fields": "userEnteredFormat(backgroundColor,textFormat)"
                                }
                            })
                        except (ValueError, TypeError):
                            pass

        sheet.batch_update({"requests": requests})
        logger.info(f"Installed sheet formatting for Matrix tab '{title}'.")
    except Exception as e:
        logger.warning(f"Could not apply sheet formatting on Matrix tab '{title}': {e}")


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
    """Full resync of metrics.xlsx to Google Sheets (both Metrics and Matrix sheets)."""
    if not METRICS_FILE.exists() or METRICS_FILE.stat().st_size == 0:
        logger.info(f"Local {METRICS_FILE.name} does not exist yet. Nothing to sync.")
        return False

    client = get_gspread_client()
    if client is None:
        return False

    try:
        sheet = client.open_by_key(SPREADSHEET_ID)
        logger.info(f"Opened target Google Spreadsheet: '{sheet.title}' ({SPREADSHEET_ID})")

        # 1. Sync Main Metrics tab
        try:
            df_metrics = pd.read_excel(METRICS_FILE, sheet_name="Metrics", engine="openpyxl")
            sync_tab(sheet, WORKSHEET_NAME, df_metrics)
        except Exception as e:
            logger.warning(f"Could not read sheet 'Metrics' from {METRICS_FILE}: {e}")

        # 2. Sync Matrix tab
        try:
            sync_matrix_tab(sheet, WORKSHEET_NAME_MATRIX)
        except Exception as e:
            logger.warning(f"Could not sync Matrix tab to Google Sheets: {e}")

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
