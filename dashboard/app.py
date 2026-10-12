import pathlib
import streamlit as st

from data_loader import get_available_runs, load_metadata
from pages.logs_exports import render_logs_exports
from pages.market_data import render_market_data
from pages.model_decisions import render_model_decisions
from pages.orders_fills import render_orders_fills
from pages.overview import render_overview
from pages.positions_account import render_positions_account

st.set_page_config(
    page_title="BTP Backtest Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    st.sidebar.title("📈 BTP Backtest Suite")
    st.sidebar.caption("Quantitative Equity Strategy Dashboard (Read-Only)")

    # Discover available runs
    runs = get_available_runs(base_dir="strategy_results")

    if not runs:
        st.title("BTP Backtest Dashboard")
        st.warning("No backtest runs found in `strategy_results/`.")
        st.info(
            "Execute a backtest run using `XGBoostRegressorStrategy.run(...)` to populate "
            "artifact directories in `strategy_results/<unique_id>/`."
        )
        return

    # Build dropdown options
    run_options = {
        f"{r['run_id']} ({r.get('status', 'unknown')})": r
        for r in runs
    }

    selected_label = st.sidebar.selectbox(
        "Select Backtest Run",
        options=list(run_options.keys()),
        index=0,
    )
    selected_run = run_options[selected_label]
    run_id = selected_run["run_id"]
    run_dir = str(pathlib.Path("strategy_results") / run_id)

    # Load freshest metadata manifest
    metadata = load_metadata(run_dir) or selected_run

    # Display Run Metadata Badge in Sidebar
    status = metadata.get("status", "unknown")
    status_icon = "🟢" if status == "completed" else ("🔴" if status == "failed" else "🟡")
    st.sidebar.markdown(f"**Status:** {status_icon} `{status.upper()}`")
    st.sidebar.markdown(f"**Run ID:** `{run_id}`")
    if metadata.get("start_time"):
        st.sidebar.markdown(f"**Started:** {metadata['start_time'][:19].replace('T', ' ')}")
    if metadata.get("duration_seconds") is not None:
        st.sidebar.markdown(f"**Duration:** {metadata['duration_seconds']}s")

    st.sidebar.divider()

    # Navigation menu
    nav_choice = st.sidebar.radio(
        "Navigation",
        [
            "📊 Overview",
            "📝 Orders & Fills",
            "💼 Positions & Account",
            "📈 Market Data",
            "🧠 Model Decisions",
            "📜 Logs & Cost Reports",
        ],
        index=0,
    )

    st.sidebar.divider()
    st.sidebar.caption("🔒 Read-Only Mode: Live backtests cannot be triggered from this UI.")

    # Render selected page
    if nav_choice == "📊 Overview":
        render_overview(run_dir=run_dir, metadata=metadata)
    elif nav_choice == "📝 Orders & Fills":
        render_orders_fills(run_dir=run_dir, metadata=metadata)
    elif nav_choice == "💼 Positions & Account":
        render_positions_account(run_dir=run_dir, metadata=metadata)
    elif nav_choice == "📈 Market Data":
        render_market_data(run_dir=run_dir, metadata=metadata)
    elif nav_choice == "🧠 Model Decisions":
        render_model_decisions(run_dir=run_dir, metadata=metadata)
    elif nav_choice == "📜 Logs & Cost Reports":
        render_logs_exports(run_dir=run_dir, metadata=metadata)


if __name__ == "__main__":
    main()
