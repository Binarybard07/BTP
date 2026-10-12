import json
import pathlib
from typing import Any, Dict, List, Optional
import pandas as pd
import streamlit as st

from equity_strategy.reporting.run_store import RunStore


@st.cache_data(ttl=60)
def get_available_runs(base_dir: str = "strategy_results") -> List[Dict[str, Any]]:
    """Discovers and returns all available backtest runs sorted by recency."""
    return RunStore.list_runs(base_dir=base_dir)


@st.cache_data(ttl=300)
def load_metadata(run_dir: str) -> Optional[Dict[str, Any]]:
    """Loads metadata.json manifest for a run."""
    p = pathlib.Path(run_dir) / "metadata.json"
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@st.cache_data(ttl=300)
def load_csv(run_dir: str, filename: str) -> pd.DataFrame:
    """Safely loads a CSV artifact, returning an empty DataFrame if missing."""
    p = pathlib.Path(run_dir) / filename
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_text(run_dir: str, filename: str) -> str:
    """Safely loads a text artifact, returning an empty string if missing."""
    p = pathlib.Path(run_dir) / filename
    if not p.exists():
        return ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


@st.cache_data(ttl=300)
def load_stats_dict(run_dir: str) -> Dict[str, Any]:
    """Parses stats.csv into a structured dictionary of metrics."""
    df = load_csv(run_dir, "stats.csv")
    if df.empty or "metric" not in df.columns or "value" not in df.columns:
        return {}
    res = {}
    for _, row in df.iterrows():
        metric = str(row["metric"])
        val = row["value"]
        try:
            val_float = float(val)
            val = val_float
        except (ValueError, TypeError):
            pass
        res[metric] = val
    return res
