#!/usr/bin/env python3
"""
Experiment Lab - Generator Agent
=================================
Renders executable strategy scripts from templates into outbox/non_processed/<main_filename>_<short_id>.py.
Faithfully mirrors RM_TM_TF_Combo generator_agent.py: completely strategy-agnostic.
All parameter values from config.yaml are passed directly into template placeholders.

Naming format: <main_filename>_<short_unique_id> (e.g. xgboost_regressor_strategy_001.py)
Direct execution without flags: `python generator_agent.py`
"""

import copy
import shutil
import itertools
import json
import logging
from pathlib import Path
import pprint
import sys
from typing import Any, Dict, List, Tuple
from jinja2 import Environment, FileSystemLoader
import yaml

# ─────────────────────────────────────────────
# PATHS CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
LOG_FILE = BASE_DIR / "generator_agent.log"

TEMPLATES_DIR = BASE_DIR / "strategies_templates"
INBOX = TEMPLATES_DIR / "non_processed"
PROCESSED_TEMPLATES = TEMPLATES_DIR / "processed"

OUTBOX = BASE_DIR / "outbox"
OUTBOX_NON_PROCESSED = OUTBOX / "non_processed"
OUTBOX_PROCESSED = OUTBOX / "processed"
OUTBOX_ERRONOUS = OUTBOX / "Errornous_scripts"

for d in (INBOX, PROCESSED_TEMPLATES, OUTBOX, OUTBOX_NON_PROCESSED, OUTBOX_PROCESSED, OUTBOX_ERRONOUS):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logger = logging.getLogger("generator_agent")
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


def load_lab_config(config_path: Path = CONFIG_FILE) -> Dict[str, Any]:
    """Load configuration from config.yaml."""
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg or {}


def _flatten_variations(d: Dict[str, Any], prefix: Tuple[str, ...] = ()) -> List[Tuple[Tuple[str, ...], List[Any]]]:
    """Recursively collect leaf parameter paths and variation lists."""
    items = []
    for k, v in d.items():
        curr_path = prefix + (k,)
        if isinstance(v, dict):
            items.extend(_flatten_variations(v, curr_path))
        elif isinstance(v, list):
            items.append((curr_path, v))
        else:
            items.append((curr_path, [v]))
    return items


def _set_nested(d: Dict[str, Any], path: Tuple[str, ...], value: Any):
    curr = d
    for key in path[:-1]:
        if key not in curr or not isinstance(curr[key], dict):
            curr[key] = {}
        curr = curr[key]
    curr[path[-1]] = value


def _get_nested(d: Dict[str, Any], path: Tuple[str, ...], default: Any = None) -> Any:
    curr = d
    for key in path:
        if isinstance(curr, dict) and key in curr:
            curr = curr[key]
        else:
            return default
    return curr


def generate_combinations_from_config(cfg: Dict[str, Any], main_filename: str) -> List[Dict[str, Any]]:
    """Generate combinations based on mode ('ofat', 'grid', or 'explicit') without hardcoded fallbacks."""
    mode = str(cfg.get("mode", "ofat")).lower().strip()

    baseline = copy.deepcopy(cfg.get("baseline", {}))
    variations = copy.deepcopy(cfg.get("variations", {}))
    explicit_combos = cfg.get("explicit_combinations", [])

    results: List[Dict[str, Any]] = []

    if mode == "explicit":
        if not explicit_combos:
            explicit_combos = [{}]
        for idx, item in enumerate(explicit_combos, 1):
            combo = copy.deepcopy(baseline)
            for k, v in item.items():
                if isinstance(v, dict) and k in combo and isinstance(combo[k], dict):
                    combo[k].update(v)
                else:
                    combo[k] = v
            combo["run_id"] = f"{main_filename}_{idx:03d}"
            results.append(combo)

    elif mode == "ofat":
        var_items = _flatten_variations(variations)

        # Baseline run
        run_idx = 1
        b_combo = copy.deepcopy(baseline)
        b_combo["run_id"] = f"{main_filename}_{run_idx:03d}"
        results.append(b_combo)

        for path, values in var_items:
            base_val = _get_nested(baseline, path)
            for val in values:
                if val == base_val:
                    continue
                run_idx += 1
                combo = copy.deepcopy(baseline)
                _set_nested(combo, path, val)
                combo["run_id"] = f"{main_filename}_{run_idx:03d}"
                results.append(combo)

    else:  # Grid
        var_items = _flatten_variations(variations)
        paths = [item[0] for item in var_items]
        value_lists = [item[1] for item in var_items]

        if not value_lists:
            b_combo = copy.deepcopy(baseline)
            b_combo["run_id"] = f"{main_filename}_001"
            results.append(b_combo)
        else:
            for idx, combo_values in enumerate(itertools.product(*value_lists), 1):
                combo = copy.deepcopy(baseline)
                for path, val in zip(paths, combo_values):
                    _set_nested(combo, path, val)
                combo["run_id"] = f"{main_filename}_{idx:03d}"
                results.append(combo)

    return results


def render_and_write_scripts(combos: List[Dict[str, Any]], tmpl_name: str, jinja_env: Environment) -> int:
    """Render each combination into a runnable .py file in outbox/non_processed/."""
    template = jinja_env.get_template(tmpl_name)
    written = 0

    for combo in combos:
        run_id = combo["run_id"]
        reports_dir = str(PROJECT_ROOT / "Backtest_Module" / "reports" / "experiments" / run_id)

        context = {
            **combo,
            "reports_dir": reports_dir,
        }

        # Pretty-print complex dictionary structures if present
        if "model_params" in context and isinstance(context["model_params"], dict):
            context["model_params"] = pprint.pformat(context["model_params"], indent=4)
        if "features" in context and isinstance(context["features"], dict):
            context["features"] = pprint.pformat(context["features"], indent=4)

        rendered = template.render(**context)

        # Write rendered .py script: <main_filename>_<short_unique_id>.py
        script_path = OUTBOX_NON_PROCESSED / f"{run_id}.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(rendered)

        # Write matching metadata JSON for result parser
        meta_path = OUTBOX_NON_PROCESSED / f"{run_id}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(combo, f, indent=2)

        written += 1
        logger.info(f"Generated script -> {script_path.name}")

    return written


def generate_all() -> int:
    """Execute combination generation for all templates in inbox and move completed templates to processed/."""
    logger.info(f"Loading configuration from {CONFIG_FILE}...")
    cfg = load_lab_config(CONFIG_FILE)
    mode = cfg.get("mode", "ofat")

    jinja_env = Environment(
        loader=FileSystemLoader(str(INBOX)),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    template_files = list(INBOX.glob("*.jinja")) + list(INBOX.glob("*.py"))
    if not template_files:
        logger.info(f"No pending strategy templates found in {INBOX}")
        return 0

    total_written = 0
    for tmpl_file in template_files:
        tmpl_name = tmpl_file.name
        main_filename = tmpl_name.replace(".jinja", "").replace(".py", "")
        logger.info(f"Processing template: '{tmpl_name}' (main_filename: '{main_filename}')")

        combos = generate_combinations_from_config(cfg, main_filename)
        logger.info(f"Mode: '{mode.upper()}' | Generated {len(combos)} distinct experiment combination(s).")

        written = render_and_write_scripts(combos, tmpl_name, jinja_env)
        total_written += written
        logger.info(f"Successfully staged {written} executable strategy script(s) into {OUTBOX_NON_PROCESSED}")

        # Once all combos of a template are generated, move the template to processed folder
        PROCESSED_TEMPLATES.mkdir(parents=True, exist_ok=True)
        dest_template = PROCESSED_TEMPLATES / tmpl_name
        try:
            if dest_template.exists():
                dest_template.unlink()
            shutil.move(str(tmpl_file), str(dest_template))
            logger.info(f"Moved processed template -> {dest_template}")
        except Exception as e:
            logger.exception(f"Failed to move template {tmpl_name} to {PROCESSED_TEMPLATES}: {e}")

    return total_written


if __name__ == "__main__":
    generate_all()
