from typing import Any, Dict
import pandas as pd
import streamlit as st

from data_loader import load_csv


def render_model_decisions(run_dir: str, metadata: Dict[str, Any]) -> None:
    st.header("🧠 Machine Learning Decisions & Portfolio Construction")

    rebalance_df = load_csv(run_dir, "rebalance_history.csv")
    predictions_df = load_csv(run_dir, "predictions.csv")
    selected_df = load_csv(run_dir, "selected_stocks.csv")
    weights_df = load_csv(run_dir, "target_weights.csv")

    if rebalance_df.empty and predictions_df.empty and selected_df.empty and weights_df.empty:
        st.info("No strategy decision artifacts recorded for this backtest run.")
        return

    # Rebalance schedule overview
    st.subheader("Monthly Rebalance Schedule")
    if not rebalance_df.empty:
        st.dataframe(rebalance_df, use_container_width=True, hide_index=True)
    else:
        st.info("No rebalance history logged.")

    st.divider()

    # Rebalance session inspection
    available_dates = []
    if not rebalance_df.empty and "decision_date" in rebalance_df.columns:
        available_dates = [str(d) for d in rebalance_df["decision_date"].unique()]
    elif not selected_df.empty and "date" in selected_df.columns:
        available_dates = [str(d) for d in selected_df["date"].unique()]
    elif not predictions_df.empty and "date" in predictions_df.columns:
        available_dates = [str(d) for d in predictions_df["date"].unique()]

    if available_dates:
        selected_date = st.selectbox("Inspect Rebalance Session (Decision Date)", available_dates, index=len(available_dates) - 1)

        tab1, tab2, tab3 = st.tabs(["Selected Universe (Top-N)", "Model Predictions", "Target Weights"])

        with tab1:
            st.subheader(f"Selected Stocks: {selected_date}")
            if not selected_df.empty and "date" in selected_df.columns:
                date_selected = selected_df[selected_df["date"] == selected_date]
                if not date_selected.empty:
                    st.caption(f"Top {len(date_selected)} selected equities")
                    st.dataframe(date_selected, use_container_width=True, hide_index=True)
                else:
                    st.info("No selection recorded for this date.")
            else:
                st.info("No selected stocks dataset available.")

        with tab2:
            st.subheader(f"XGBoost Model Predictions: {selected_date}")
            if not predictions_df.empty and "date" in predictions_df.columns:
                date_preds = predictions_df[predictions_df["date"] == selected_date]
                if not date_preds.empty:
                    st.caption(f"Predictions for {len(date_preds)} instruments")
                    st.dataframe(date_preds, use_container_width=True, hide_index=True)
                else:
                    st.info("No predictions recorded for this date.")
            else:
                st.info("No predictions dataset available.")

        with tab3:
            st.subheader(f"Target Portfolio Weights: {selected_date}")
            if not weights_df.empty and "date" in weights_df.columns:
                date_weights = weights_df[weights_df["date"] == selected_date]
                if not date_weights.empty:
                    st.caption(f"Target weights for {len(date_weights)} instruments (Sum: {date_weights['target_weight'].sum():.4f})")
                    st.dataframe(date_weights, use_container_width=True, hide_index=True)
                else:
                    st.info("No target weights recorded for this date.")
            else:
                st.info("No target weights dataset available.")

    # Target weights evolution across backtest
    if not weights_df.empty and "date" in weights_df.columns and "symbol" in weights_df.columns and "target_weight" in weights_df.columns:
        st.divider()
        st.subheader("Target Weights Evolution Across Time")
        try:
            pivot_weights = weights_df.pivot(index="date", columns="symbol", values="target_weight").fillna(0.0)
            pivot_weights.index = pd.to_datetime(pivot_weights.index)
            st.area_chart(pivot_weights, use_container_width=True)
            st.caption("Historical target portfolio weights by instrument")
        except Exception:
            pass
