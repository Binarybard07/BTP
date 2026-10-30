#!/usr/bin/env python3
"""
Experiment Lab - Result Agent
=============================
Parses Backtest_Module results from summary.json, ranks runs by composite Score,
generates a single clean 'Metrics' sheet in metrics.xlsx, and archives all run components.

Scoring Formula:
  Score = 0.5 * (CAGR - Benchmark_CAGR) + 0.5 * (MaxDD - Benchmark_MaxDD)

Direct execution without flags: `python result_agent.py`
"""

import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ─────────────────────────────────────────────
# PATHS CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
RESULT_LOG_FILE = BASE_DIR / "result_agent.log"

BACKTEST_DIR = BASE_DIR / "Backtest_Results"
NEW_DIR = BACKTEST_DIR / "non_processed"
DONE_DIR = BACKTEST_DIR / "processed"
OUTPUT_XLSX = BACKTEST_DIR / "metrics.xlsx"

for d in (BACKTEST_DIR, NEW_DIR, DONE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logger = logging.getLogger("result_agent")
logger.setLevel(logging.INFO)

if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    fh = logging.FileHandler(RESULT_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)

METRIC_COLUMNS = [
    "Run_ID",
    "Score",
    "CAGR [%]",
    "Max Drawdown [%]",
    "Avg Drawdown [%]",
    "Sharpe Ratio",
    "Calmar Ratio",
    "Benchmark",
    "Benchmark CAGR [%]",
    "Benchmark MaxDD [%]",
    "Benchmark AvgDD [%]",
    "Benchmark Sharpe",
    "Window_Start",
    "Window_End",
    "Universe",
    "Survivorship",
    "Summary_Path",
    "Report_Folder",
]


def extract_base_universe(u: Any) -> str:
    """Extract base universe by stripping _ddq suffix dynamically without hardcoded names."""
    if u is None or str(u).strip() == "":
        return "unknown"
    s = str(u).strip()
    if s.lower().endswith("_ddq"):
        return s[:-4].strip().lower()
    return s.strip().lower()


def clean_universe_display(u_raw: Any) -> str:
    """Dynamically clean and format any universe identifier without hardcoded name mappings."""
    if u_raw is None or str(u_raw).strip() == "":
        return "Unknown"
    s = str(u_raw).strip()
    if s.lower().endswith("_ddq"):
        s = s[:-4].strip()
    import re
    m = re.match(r"^([a-zA-Z_]+)\s*(\d+)$", s)
    if m:
        prefix, num = m.groups()
        prefix = prefix.replace("_", " ").strip()
        if prefix.lower() == "nifty":
            return f"NIFTY {num}"
        return f"{prefix.title()} {num}"
    return s.replace("_", " ").title()


def calculate_apex_score(cagr: float, maxdd: float, bench_cagr: float, bench_maxdd: float) -> float:
    """
    Apex Alpha Score (Dual-Hurdle Gated Calmar Alpha)
    ==================================================
    4 Strict Green Criteria:
      1. CAGR > 20.0%
      2. CAGR > Benchmark_CAGR
      3. Drawdown depth < 20.0% (signed MaxDD > -20.0%)
      4. Drawdown shallower than Benchmark (signed MaxDD > Benchmark_MaxDD)

    Passing (Apex Tier):
      Score = (CAGR / |MaxDD|) * (1 + (CAGR - Benchmark_CAGR) / 100) > 1.0 > 0
    Failing (Sub-Apex Tier):
      Score = min(CAGR - Hurdle_return, MaxDD - Strictest_MaxDD) <= 0
    """
    h_return = max(20.0, bench_cagr)
    strictest_maxdd = max(-20.0, bench_maxdd)

    surplus_return = cagr - h_return
    surplus_dd = maxdd - strictest_maxdd

    if surplus_return > 0 and surplus_dd > 0:
        dd_mag = abs(maxdd)
        calmar = cagr / dd_mag if dd_mag > 0.0001 else cagr
        excess_cagr = cagr - bench_cagr
        score = calmar * (1.0 + excess_cagr / 100.0)
    else:
        score = min(surplus_return, surplus_dd)

    return round(score, 4)


def extract_run_data(folder_path: Path) -> Optional[Dict[str, Any]]:
    """Extract metrics from summary.json and metadata JSON inside folder."""
    summary_path = folder_path / "summary.json"
    if not summary_path.exists() or summary_path.stat().st_size == 0:
        logger.warning(f"No valid summary.json found in {folder_path.name}")
        return None

    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except Exception as e:
        logger.error(f"Error parsing {summary_path}: {e}")
        return None

    meta_files = [p for p in folder_path.glob("*.json") if p.name != "summary.json"]
    meta_data = {}
    if meta_files:
        try:
            with open(meta_files[0], "r", encoding="utf-8") as f:
                meta_data = json.load(f) or {}
        except Exception:
            pass

    cagr = float(summary.get("cagr", 0.0))
    maxdd = float(summary.get("maxdd", 0.0))
    bench_cagr = float(summary.get("benchmark_cagr", 0.0))
    bench_maxdd = float(summary.get("benchmark_maxdd", 0.0))
    score = calculate_apex_score(cagr, maxdd, bench_cagr, bench_maxdd)

    data = {
        "Run_ID": folder_path.name,
        "Score": score,
        "CAGR [%]": round(cagr, 3),
        "Max Drawdown [%]": round(maxdd, 3),
        "Avg Drawdown [%]": round(float(summary.get("avgdd", 0.0)), 3),
        "Sharpe Ratio": round(float(summary.get("sharpe", 0.0)), 3),
        "Calmar Ratio": round(float(summary.get("calmar", 0.0)), 3),
        "Benchmark": summary.get("benchmark_name", "N/A"),
        "Benchmark CAGR [%]": round(bench_cagr, 3),
        "Benchmark MaxDD [%]": round(bench_maxdd, 3),
        "Benchmark AvgDD [%]": round(float(summary.get("benchmark_avgdd", 0.0)), 3),
        "Benchmark Sharpe": round(float(summary.get("benchmark_sharpe", 0.0)), 3),
        "Window_Start": str(summary.get("window_start", meta_data.get("start_date", ""))),
        "Window_End": str(summary.get("window_end", meta_data.get("end_date", ""))),
        "Universe": summary.get("universe", meta_data.get("universe", "unknown")),
        "Survivorship": bool(summary.get("survivorship", False)),
        "Summary_Path": str(summary_path),
        "Report_Folder": str(folder_path),
    }
    return data


def load_existing_df() -> pd.DataFrame:
    """Load existing metrics from OUTPUT_XLSX or return empty DataFrame."""
    if OUTPUT_XLSX.exists():
        try:
            df = pd.read_excel(OUTPUT_XLSX, sheet_name="Metrics", engine="openpyxl")
            for c in METRIC_COLUMNS:
                if c not in df.columns:
                    df[c] = pd.NA
            return df[METRIC_COLUMNS]
        except Exception as e:
            logger.warning(f"Could not read existing {OUTPUT_XLSX}: {e}")
    return pd.DataFrame(columns=METRIC_COLUMNS)


def build_matrix_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    Build structured matrix rows purely dynamically from unique values present in df:
      - Unique universes discovered strictly from df["Universe"]
      - Unique From (Window_Start) dates discovered strictly from df["Window_Start"]
      - Unique Survivorship settings discovered strictly from df["Survivorship"]
    Zero hardcoded universe names, zero hardcoded dates.
    """
    if df.empty:
        return []

    df_copy = df.copy()
    df_copy["base_universe"] = df_copy["Universe"].apply(extract_base_universe)

    matrix_rows = []
    # Discover unique base universes strictly from the data
    unique_base_universes = df_copy["base_universe"].dropna().unique()

    for base_u in unique_base_universes:
        u_df = df_copy[df_copy["base_universe"] == base_u]
        display_name = clean_universe_display(base_u)

        # Discover unique From dates strictly from this universe's data
        unique_from_dates = u_df["Window_Start"].dropna().unique()
        if len(unique_from_dates) == 0:
            unique_from_dates = ["N/A"]

        for from_date in unique_from_dates:
            uf_df = u_df[u_df["Window_Start"] == from_date] if from_date != "N/A" else u_df

            # Discover unique survivorship settings for this universe/from-date
            surv_in_data = uf_df["Survivorship"].dropna().unique()
            is_boolean_surv = any(isinstance(x, (bool, int)) or str(x).lower() in ("true", "false") for x in surv_in_data)

            if is_boolean_surv:
                surv_pairs = [(True, "TRUE"), (False, "FALSE")]
            else:
                surv_pairs = [(val, str(val).upper()) for val in sorted(surv_in_data)]

            for surv_key, surv_label in surv_pairs:
                if is_boolean_surv:
                    sub = uf_df[uf_df["Survivorship"].apply(lambda x: bool(x) is bool(surv_key))]
                else:
                    sub = uf_df[uf_df["Survivorship"] == surv_key]

                pos = sub[sub["Score"] > 0] if not sub.empty else pd.DataFrame()
                if not pos.empty:
                    best = pos.sort_values("Score", ascending=False).iloc[0]
                    row = {
                        "Universe": display_name,
                        "Survivorship": surv_label,
                        "From": from_date,
                        "Run_ID": str(best["Run_ID"]),
                        "Score": float(best["Score"]),
                        "Strategy_CAGR": float(best["CAGR [%]"]),
                        "Strategy_Drawdown": float(best["Max Drawdown [%]"]),
                        "Index_CAGR": float(best["Benchmark CAGR [%]"]),
                        "Index_Drawdown": float(best["Benchmark MaxDD [%]"]),
                    }
                else:
                    bench_cagr = sub.iloc[0]["Benchmark CAGR [%]"] if not sub.empty else (uf_df.iloc[0]["Benchmark CAGR [%]"] if not uf_df.empty else "N/A")
                    bench_maxdd = sub.iloc[0]["Benchmark MaxDD [%]"] if not sub.empty else (uf_df.iloc[0]["Benchmark MaxDD [%]"] if not uf_df.empty else "N/A")
                    row = {
                        "Universe": display_name,
                        "Survivorship": surv_label,
                        "From": from_date,
                        "Run_ID": "N/A",
                        "Score": "N/A",
                        "Strategy_CAGR": "N/A",
                        "Strategy_Drawdown": "N/A",
                        "Index_CAGR": bench_cagr if bench_cagr == "N/A" else float(bench_cagr),
                        "Index_Drawdown": bench_maxdd if bench_maxdd == "N/A" else float(bench_maxdd),
                    }
                matrix_rows.append(row)

    return matrix_rows


def get_cagr_style(val: Any) -> Tuple[PatternFill, Font]:
    """Rich gradient style for CAGR: Soft pastel mint -> deep emerald green (Choice 1C)."""
    if val == "N/A" or val is None or str(val).strip() == "":
        return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF", italic=True)
    try:
        v = float(val)
        if v >= 24.0:
            return PatternFill("solid", fgColor="C6F6D5"), Font(name="Calibri", size=10, bold=True, color="047857")
        elif v >= 20.0:
            return PatternFill("solid", fgColor="D1FAE5"), Font(name="Calibri", size=10, bold=True, color="065F46")
        elif v >= 15.0:
            return PatternFill("solid", fgColor="FEF3C7"), Font(name="Calibri", size=10, color="92400E")
        else:
            return PatternFill("solid", fgColor="FEE2E2"), Font(name="Calibri", size=10, color="991B1B")
    except (ValueError, TypeError):
        return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF")


def get_drawdown_style(val: Any) -> Tuple[PatternFill, Font]:
    """Rich gradient style for Drawdown: Soft mint (-15%) -> amber (-20%) -> coral red (-35%+) (Choice 1C)."""
    if val == "N/A" or val is None or str(val).strip() == "":
        return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF", italic=True)
    try:
        v = float(val)
        if v >= -15.0:
            return PatternFill("solid", fgColor="D1FAE5"), Font(name="Calibri", size=10, bold=True, color="065F46")
        elif v >= -20.0:
            return PatternFill("solid", fgColor="FEF3C7"), Font(name="Calibri", size=10, color="92400E")
        else:
            return PatternFill("solid", fgColor="FEE2E2"), Font(name="Calibri", size=10, color="991B1B")
    except (ValueError, TypeError):
        return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF")


def get_score_style(val: Any) -> Tuple[PatternFill, Font]:
    """Rich highlight for Score: Soft mint if > 0, subtle muted gray if <= 0 or N/A (Choice 1C)."""
    if val == "N/A" or val is None or str(val).strip() == "":
        return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF", italic=True)
    try:
        v = float(val)
        if v > 0.0:
            return PatternFill("solid", fgColor="D1FAE5"), Font(name="Calibri", size=10, bold=True, color="065F46")
        else:
            return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF", italic=True)
    except (ValueError, TypeError):
        return PatternFill("solid", fgColor="F9FAFB"), Font(name="Calibri", size=10, color="9CA3AF")


def format_metrics_sheet(ws, df: pd.DataFrame):
    """Freeze row 1, bold dark-navy header, and highlight rows with Score > 0 in soft green (#C6EFCE)."""
    ws.freeze_panes = "A2"
    header_fill = PatternFill("solid", fgColor="1B365D")
    header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    try:
        score_idx = df.columns.get_loc("Score") + 1
        for r_idx in range(2, len(df) + 2):
            val = ws.cell(row=r_idx, column=score_idx).value
            if val is not None:
                try:
                    if float(val) > 0.0:
                        for col_idx in range(1, len(df.columns) + 1):
                            ws.cell(row=r_idx, column=col_idx).fill = green_fill
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass


def format_matrix_sheet(ws, matrix_rows: List[Dict[str, Any]]):
    """
    Format the Matrix sheet with:
      - 2-tier headers: Universe, Survivorship, From, Run_ID, Score, Strategy (CAGR, DD), Index (CAGR, DD)
      - Vertically merged 'From' column per universe pair
      - Peach/cream universe column styling
      - Solid borders separating universe blocks
      - Gradient coloring on numerical performance metrics
    """
    ws.freeze_panes = "A3"

    # Row 1 Super-headers
    ws.merge_cells("A1:A2")
    ws.merge_cells("B1:B2")
    ws.merge_cells("C1:C2")
    ws.merge_cells("D1:D2")
    ws.merge_cells("E1:E2")
    ws.merge_cells("F1:G1")
    ws.merge_cells("H1:I1")

    ws["A1"] = "Universe"
    ws["B1"] = "Survivorship"
    ws["C1"] = "From"
    ws["D1"] = "Run_ID"
    ws["E1"] = "Score"
    ws["F1"] = "Strategy"
    ws["H1"] = "Index"

    # Row 2 Sub-headers
    ws["F2"] = "CAGR [%]"
    ws["G2"] = "Drawdown [%]"
    ws["H2"] = "CAGR [%]"
    ws["I2"] = "Drawdown [%]"

    # Styles for Choice 1C: Steel Graphite & Frost
    fill_frosted_hdr = PatternFill("solid", fgColor="E5E7EB")       # Frosted Silver Gray
    font_frosted_hdr = Font(name="Calibri", size=10, bold=True, color="111827") # Dark Graphite

    fill_strat_super = PatternFill("solid", fgColor="E0E7FF")       # Subtle Frosted Indigo
    font_strat_super = Font(name="Calibri", size=10, bold=True, color="3730A3") # Deep Indigo

    fill_strat_sub = PatternFill("solid", fgColor="EEF2FF")         # Pale Indigo
    font_strat_sub = Font(name="Calibri", size=10, bold=True, color="4338CA")   # Indigo

    fill_idx_super = PatternFill("solid", fgColor="F4F4F5")         # Neutral Zinc
    font_idx_super = Font(name="Calibri", size=10, bold=True, color="27272A")   # Deep Zinc

    fill_idx_sub = PatternFill("solid", fgColor="FAFAFA")           # Pale Zinc
    font_idx_sub = Font(name="Calibri", size=10, bold=True, color="52525B")     # Zinc

    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col in ("A", "B", "C", "D", "E"):
        for r in (1, 2):
            cell = ws[f"{col}{r}"]
            cell.font = font_frosted_hdr
            cell.fill = fill_frosted_hdr
            cell.alignment = align_center

    ws["F1"].font = font_strat_super
    ws["F1"].fill = fill_strat_super
    ws["F1"].alignment = align_center
    ws["G1"].fill = fill_strat_super

    ws["H1"].font = font_idx_super
    ws["H1"].fill = fill_idx_super
    ws["H1"].alignment = align_center
    ws["I1"].fill = fill_idx_super

    ws["F2"].font = font_strat_sub
    ws["F2"].fill = fill_strat_sub
    ws["F2"].alignment = align_center

    ws["G2"].font = font_strat_sub
    ws["G2"].fill = fill_strat_sub
    ws["G2"].alignment = align_center

    ws["H2"].font = font_idx_sub
    ws["H2"].fill = fill_idx_sub
    ws["H2"].alignment = align_center

    ws["I2"].font = font_idx_sub
    ws["I2"].fill = fill_idx_sub
    ws["I2"].alignment = align_center

    # Thin & Medium borders (Choice 1C)
    thin_side = Side(style="thin", color="E5E7EB")
    hdr_bottom = Side(style="medium", color="4B5563")
    block_divider = Side(style="medium", color="6B7280")

    for col_idx in range(1, 10):
        ws.cell(row=2, column=col_idx).border = Border(bottom=hdr_bottom, top=thin_side, left=thin_side, right=thin_side)

    # Fill data rows (Choice 1C)
    universe_fill = PatternFill("solid", fgColor="F3F4F6") # Frost White / Soft Slate
    universe_font = Font(name="Calibri", size=10, bold=True, color="1F2937") # Dark Bold Slate
    surv_font = Font(name="Calibri", size=10, bold=True, color="374151")
    runid_font = Font(name="Consolas", size=9, color="374151")

    curr_row = 3
    for i in range(0, len(matrix_rows), 2):
        pair = matrix_rows[i:i+2]
        r1 = curr_row
        r2 = curr_row + 1

        for idx, row_data in enumerate(pair):
            r = curr_row + idx

            ws.cell(row=r, column=1, value=row_data["Universe"]).fill = universe_fill
            ws.cell(row=r, column=1).font = universe_font
            ws.cell(row=r, column=1).alignment = align_center

            ws.cell(row=r, column=2, value=row_data["Survivorship"]).font = surv_font
            ws.cell(row=r, column=2).alignment = align_center

            ws.cell(row=r, column=3, value=row_data["From"]).alignment = align_center

            # Run_ID
            c_run = ws.cell(row=r, column=4, value=row_data["Run_ID"])
            c_run.font = runid_font if row_data["Run_ID"] != "N/A" else Font(name="Calibri", size=10, color="7F8C8D", italic=True)
            c_run.alignment = align_center

            # Score
            c_score = ws.cell(row=r, column=5, value=row_data["Score"])
            fill_s, font_s = get_score_style(row_data["Score"])
            c_score.fill = fill_s
            c_score.font = font_s
            c_score.alignment = align_center

            # Strategy CAGR
            c_scagr = ws.cell(row=r, column=6, value=row_data["Strategy_CAGR"])
            fill_sc, font_sc = get_cagr_style(row_data["Strategy_CAGR"])
            c_scagr.fill = fill_sc
            c_scagr.font = font_sc
            c_scagr.alignment = align_center

            # Strategy Drawdown
            c_sdd = ws.cell(row=r, column=7, value=row_data["Strategy_Drawdown"])
            fill_sd, font_sd = get_drawdown_style(row_data["Strategy_Drawdown"])
            c_sdd.fill = fill_sd
            c_sdd.font = font_sd
            c_sdd.alignment = align_center

            # Index CAGR
            c_icagr = ws.cell(row=r, column=8, value=row_data["Index_CAGR"])
            fill_ic, font_ic = get_cagr_style(row_data["Index_CAGR"])
            c_icagr.fill = fill_ic
            c_icagr.font = font_ic
            c_icagr.alignment = align_center

            # Index Drawdown
            c_idd = ws.cell(row=r, column=9, value=row_data["Index_Drawdown"])
            fill_id, font_id = get_drawdown_style(row_data["Index_Drawdown"])
            c_idd.fill = fill_id
            c_idd.font = font_id
            c_idd.alignment = align_center

        # Vertically merge 'From' across the pair if same from date
        if len(pair) == 2 and pair[0]["From"] == pair[1]["From"]:
            ws.merge_cells(start_row=r1, start_column=3, end_row=r2, end_column=3)
            ws.cell(row=r1, column=3).alignment = align_center

        # Borders: inner thin, outer block bottom medium slate (Choice 1C)
        for col_idx in range(1, 10):
            ws.cell(row=r1, column=col_idx).border = Border(top=thin_side, bottom=thin_side, left=thin_side, right=thin_side)
            if len(pair) == 2:
                ws.cell(row=r2, column=col_idx).border = Border(top=thin_side, bottom=block_divider, left=thin_side, right=thin_side)
            else:
                ws.cell(row=r1, column=col_idx).border = Border(top=thin_side, bottom=block_divider, left=thin_side, right=thin_side)

        curr_row += len(pair)

    # Adjust column widths
    col_widths = {
        "A": 18,
        "B": 15,
        "C": 14,
        "D": 32,
        "E": 12,
        "F": 15,
        "G": 15,
        "H": 15,
        "I": 15,
    }
    for col, width in col_widths.items():
        ws.column_dimensions[col].width = width


def write_styled_excel(df: pd.DataFrame):
    """Save metrics to 'Metrics' sheet and best variations to 'Matrix' sheet in metrics.xlsx."""
    if df.empty:
        return

    df = df.drop_duplicates(subset=["Run_ID"], keep="last")
    df_metrics = df.sort_values("Score", ascending=False).reset_index(drop=True)
    matrix_rows = build_matrix_rows(df_metrics)

    try:
        wb = openpyxl.Workbook()
        ws_metrics = wb.active
        ws_metrics.title = "Metrics"

        # Populate Metrics sheet
        ws_metrics.append(METRIC_COLUMNS)
        for _, row in df_metrics.iterrows():
            ws_metrics.append([row[col] for col in METRIC_COLUMNS])
        format_metrics_sheet(ws_metrics, df_metrics)

        # Create Matrix sheet
        ws_matrix = wb.create_sheet(title="Matrix")
        format_matrix_sheet(ws_matrix, matrix_rows)

        wb.save(OUTPUT_XLSX)
        logger.info(f"Saved metrics to {OUTPUT_XLSX} ('Metrics': {len(df_metrics)} rows, 'Matrix': {len(matrix_rows)} rows)")
    except Exception as e:
        logger.exception(f"Failed writing Excel {OUTPUT_XLSX}: {e}")



class BatchHandler(FileSystemEventHandler):
    """Watches Backtest_Results/non_processed/ for new run output folders."""

    def __init__(self):
        super().__init__()
        self._processed = set()

    def on_created(self, event):
        if not event.is_directory:
            return
        folder_path = Path(event.src_path)
        if folder_path.name.startswith("."):
            return
        if folder_path.name in self._processed:
            return

        time.sleep(1.0)  # Allow folder and file copies to settle
        self.process_batch(folder_path)

    def process_batch(self, folder_path: Path) -> bool:
        if not folder_path.exists() or not folder_path.is_dir():
            return False
        if folder_path.name in self._processed:
            return False

        logger.info(f"Processing results folder: {folder_path.name}")
        try:
            data = extract_run_data(folder_path)
            if not data:
                logger.warning(f"No valid run data extracted from {folder_path.name}")
                return False

            df_master = load_existing_df()
            df_new = pd.DataFrame([data])
            df_updated = pd.concat([df_master, df_new], ignore_index=True)
            write_styled_excel(df_updated)

            dest = DONE_DIR / folder_path.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(folder_path), str(dest))
            self._processed.add(folder_path.name)
            logger.info(f"Archived all run components -> {dest}")
            return True
        except Exception as e:
            logger.exception(f"Error processing batch {folder_path.name}: {e}")
            return False


def bootstrap_existing(handler: BatchHandler):
    """Process any folders already in Backtest_Results/non_processed/ at startup in batch."""
    subdirs = sorted([p for p in NEW_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")])
    if not subdirs:
        logger.info(f"No pending results folders in {NEW_DIR}")
        return

    logger.info(f"Bootstrapping {len(subdirs)} existing results folder(s)...")
    df_master = load_existing_df()
    new_rows = []

    for folder in subdirs:
        try:
            data = extract_run_data(folder)
            if data:
                new_rows.append(data)
                dest = DONE_DIR / folder.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.move(str(folder), str(dest))
                handler._processed.add(folder.name)
                logger.info(f"Archived all run components -> {dest}")
        except Exception as e:
            logger.exception(f"Error bootstrapping {folder.name}: {e}")

    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df_updated = pd.concat([df_master, df_new], ignore_index=True)
        write_styled_excel(df_updated)


def process_all_pending() -> int:
    """Process all completed runs waiting in Backtest_Results/non_processed/ (one-pass)."""
    handler = BatchHandler()
    bootstrap_existing(handler)
    return len(handler._processed)


def main():
    """Main result agent entrypoint: process pending folders first, then observe for new ones."""
    logger.info("Result-agent starting up...")
    handler = BatchHandler()
    bootstrap_existing(handler)

    logger.info(f"Watching for new backtest folders in {NEW_DIR}/ ...")
    observer = Observer()
    observer.schedule(handler, str(NEW_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested, stopping result agent.")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
