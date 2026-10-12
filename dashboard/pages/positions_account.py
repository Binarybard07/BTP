from typing import Any, Dict
import pandas as pd
import streamlit as st

from data_loader import load_csv


def render_positions_account(run_dir: str, metadata: Dict[str, Any]) -> None:
    st.header("💼 Positions & Account Balances")

    positions_df = load_csv(run_dir, "positions.csv")
    account_df = load_csv(run_dir, "account.csv")

    tab1, tab2 = st.tabs(["Positions Ledger", "Account State"])

    with tab1:
        st.subheader("Positions History")
        if positions_df.empty:
            st.info("No positions recorded in positions.csv.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                inst_col = "instrument_id" if "instrument_id" in positions_df.columns else "symbol"
                if inst_col in positions_df.columns:
                    syms = ["ALL"] + sorted([str(s) for s in positions_df[inst_col].unique()])
                    selected_pos_sym = st.selectbox("Filter by Instrument", syms, key="pos_sym_filter")
                else:
                    selected_pos_sym = "ALL"
            with col2:
                side_col = "side" if "side" in positions_df.columns else None
                if side_col:
                    sides = ["ALL"] + sorted([str(s) for s in positions_df[side_col].unique()])
                    selected_pos_side = st.selectbox("Filter by Side", sides, key="pos_side_filter")
                else:
                    selected_pos_side = "ALL"

            filtered_pos = positions_df.copy()
            if selected_pos_sym != "ALL" and inst_col in filtered_pos.columns:
                filtered_pos = filtered_pos[filtered_pos[inst_col] == selected_pos_sym]
            if selected_pos_side != "ALL" and side_col in filtered_pos.columns:
                filtered_pos = filtered_pos[filtered_pos[side_col] == selected_pos_side]

            st.caption(f"Showing {len(filtered_pos)} of {len(positions_df)} positions")
            st.dataframe(filtered_pos, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Account Balances & Equity Timeline")
        if account_df.empty:
            st.info("No account records found in account.csv.")
        else:
            num_cols = [c for c in ["total", "balance", "free", "locked"] if c in account_df.columns]
            time_col = None
            for col in ["timestamp", "ts_event", "date", "time"]:
                if col in account_df.columns:
                    time_col = col
                    break

            if num_cols:
                chart_df = account_df.copy()
                if time_col:
                    chart_df["Time"] = pd.to_datetime(chart_df[time_col])
                    chart_df = chart_df.sort_values(by="Time").set_index("Time")
                st.line_chart(chart_df[num_cols], use_container_width=True)
                st.caption("Account Currency Balances (INR)")

            st.subheader("Raw Account Snapshots")
            st.dataframe(account_df, use_container_width=True, hide_index=True)
