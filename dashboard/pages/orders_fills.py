from typing import Any, Dict
import pandas as pd
import streamlit as st

from data_loader import load_csv


def render_orders_fills(run_dir: str, metadata: Dict[str, Any]) -> None:
    st.header("📝 Orders & Execution Fills")

    orders_df = load_csv(run_dir, "orders.csv")
    fills_df = load_csv(run_dir, "fills.csv")
    order_fills_df = load_csv(run_dir, "order_fills.csv")

    if orders_df.empty and fills_df.empty and order_fills_df.empty:
        st.info("No orders or fills recorded for this backtest run.")
        return

    # Tabs for Orders, Fills, and Order-Fill Linkage
    tab1, tab2, tab3 = st.tabs(["Orders Report", "Fills Report", "Order-Fills Mapping"])

    with tab1:
        st.subheader("Submitted Orders")
        if orders_df.empty:
            st.info("No orders found in orders.csv.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                symbols = ["ALL"] + sorted([str(s) for s in orders_df["instrument_id"].unique()]) if "instrument_id" in orders_df.columns else ["ALL"]
                selected_sym = st.selectbox("Filter by Instrument", symbols, key="order_sym_filter")
            with col2:
                side_options = ["ALL"] + sorted([str(s) for s in orders_df["side"].unique()]) if "side" in orders_df.columns else ["ALL"]
                selected_side = st.selectbox("Filter by Side", side_options, key="order_side_filter")

            filtered_orders = orders_df.copy()
            if selected_sym != "ALL":
                filtered_orders = filtered_orders[filtered_orders["instrument_id"] == selected_sym]
            if selected_side != "ALL":
                filtered_orders = filtered_orders[filtered_orders["side"] == selected_side]

            st.caption(f"Showing {len(filtered_orders)} of {len(orders_df)} orders")
            st.dataframe(filtered_orders, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("Execution Fills")
        if fills_df.empty:
            st.info("No fills found in fills.csv.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                fill_syms = ["ALL"] + sorted([str(s) for s in fills_df["instrument_id"].unique()]) if "instrument_id" in fills_df.columns else ["ALL"]
                selected_fill_sym = st.selectbox("Filter by Instrument", fill_syms, key="fill_sym_filter")
            with col2:
                fill_sides = ["ALL"] + sorted([str(s) for s in fills_df["side"].unique()]) if "side" in fills_df.columns else ["ALL"]
                selected_fill_side = st.selectbox("Filter by Side", fill_sides, key="fill_side_filter")

            filtered_fills = fills_df.copy()
            if selected_fill_sym != "ALL":
                filtered_fills = filtered_fills[filtered_fills["instrument_id"] == selected_fill_sym]
            if selected_fill_side != "ALL":
                filtered_fills = filtered_fills[filtered_fills["side"] == selected_fill_side]

            st.caption(f"Showing {len(filtered_fills)} of {len(fills_df)} fills")
            st.dataframe(filtered_fills, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("Order-Fills Mapping")
        if order_fills_df.empty:
            st.info("No order fills linkage found in order_fills.csv.")
        else:
            st.caption(f"Total mappings: {len(order_fills_df)}")
            st.dataframe(order_fills_df, use_container_width=True, hide_index=True)
