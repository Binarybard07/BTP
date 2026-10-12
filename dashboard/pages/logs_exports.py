import pathlib
from typing import Any, Dict
import pandas as pd
import streamlit as st

from data_loader import load_csv, load_text


def render_logs_exports(run_dir: str, metadata: Dict[str, Any]) -> None:
    st.header("📜 Logs, Execution Costs & Artifacts")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Execution Costs Breakdown",
        "Cost Audit Report",
        "Nautilus Engine Log",
        "Artifact File Inventory",
    ])

    with tab1:
        st.subheader("Itemised Execution Costs & Slippage")
        costs_df = load_csv(run_dir, "execution_costs.csv")
        if costs_df.empty:
            st.info("No execution costs recorded in execution_costs.csv.")
        else:
            col1, col2, col3 = st.columns(3)
            with col1:
                comm_sum = costs_df["total_commission"].sum() if "total_commission" in costs_df.columns else 0.0
                st.metric("Total Commissions (INR)", f"₹{comm_sum:,.2f}")
            with col2:
                slip_sum = costs_df["total_slippage_cost"].sum() if "total_slippage_cost" in costs_df.columns else 0.0
                st.metric("Total Slippage Drag (INR)", f"₹{slip_sum:,.2f}")
            with col3:
                drag_sum = costs_df["total_execution_drag"].sum() if "total_execution_drag" in costs_df.columns else (comm_sum + slip_sum)
                st.metric("Grand Total Friction (INR)", f"₹{drag_sum:,.2f}")

            st.caption(f"Showing {len(costs_df)} fill execution cost records")
            st.dataframe(costs_df, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Execution Cost Text Summary")
        cost_txt = load_text(run_dir, "cost_report.txt")
        if cost_txt:
            st.code(cost_txt, language="text")
        else:
            st.info("No cost_report.txt available for this run.")

    with tab3:
        st.subheader("NautilusTrader Engine Log (nautilus.log)")
        log_txt = load_text(run_dir, "nautilus.log")
        if log_txt:
            search_query = st.text_input("Filter log lines (regex/substring):", "")
            lines = log_txt.splitlines()

            if search_query:
                filtered_lines = [l for l in lines if search_query.lower() in l.lower()]
                st.caption(f"Showing {len(filtered_lines)} of {len(lines)} log lines matching '{search_query}'")
                display_txt = "\n".join(filtered_lines[-500:])
            else:
                max_lines = st.slider("Max lines to display:", 100, 2000, 500, step=100)
                st.caption(f"Showing last {min(max_lines, len(lines))} of {len(lines)} log lines")
                display_txt = "\n".join(lines[-max_lines:])

            st.code(display_txt, language="log")
        else:
            st.info("No nautilus.log found in this run directory.")

    with tab4:
        st.subheader("Stored Run Artifacts")
        artifacts = metadata.get("artifacts", {})
        if artifacts:
            art_rows = []
            for name, meta in sorted(artifacts.items()):
                size_kb = round(meta.get("size_bytes", 0) / 1024, 2)
                art_rows.append({
                    "Artifact Name": name,
                    "Size (KB)": size_kb,
                    "Last Modified": meta.get("modified_time", "N/A"),
                })
            st.dataframe(pd.DataFrame(art_rows), use_container_width=True, hide_index=True)
        else:
            run_p = pathlib.Path(run_dir)
            if run_p.exists():
                art_rows = []
                for p in sorted(run_p.iterdir()):
                    if p.is_file():
                        art_rows.append({
                            "Artifact Name": p.name,
                            "Size (KB)": round(p.stat().st_size / 1024, 2),
                        })
                st.dataframe(pd.DataFrame(art_rows), use_container_width=True, hide_index=True)
            else:
                st.info("Run directory does not exist on disk.")
