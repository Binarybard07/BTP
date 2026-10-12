from typing import Any, Dict
import pandas as pd
import streamlit as st

from data_loader import load_csv, load_metadata, load_stats_dict


def render_overview(run_dir: str, metadata: Dict[str, Any]) -> None:
    st.header("📊 Strategy Overview & Key Performance Indicators")

    status = metadata.get("status", "unknown")
    if status == "failed":
        st.error(f"⚠️ This backtest run failed with error: {metadata.get('error', 'Unknown error')}")
        if metadata.get("traceback"):
            with st.expander("Error Traceback", expanded=False):
                st.code(metadata["traceback"])

    # High-level run details cards
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Status", status.upper(), delta="Completed" if status == "completed" else None)
    with col2:
        dur = metadata.get("duration_seconds")
        st.metric("Run Duration", f"{dur:.1f}s" if dur is not None else "N/A")
    with col3:
        st.metric("Universe Size", metadata.get("symbols_count", len(metadata.get("symbols", []))))
    with col4:
        config = metadata.get("config", {})
        top_n = config.get("top_n", 20)
        st.metric("Target Top-N", top_n)

    st.divider()

    # KPI Metrics from stats.csv
    stats = load_stats_dict(run_dir)
    st.subheader("Performance Metrics")

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    with kpi1:
        pnl_val = stats.get("PNL (total)", stats.get("pnl", 0.0))
        st.metric("Total PnL (INR)", f"₹{float(pnl_val):,.2f}" if isinstance(pnl_val, (int, float)) else str(pnl_val))
    with kpi2:
        ret_val = stats.get("Return [%]", stats.get("total_return", "N/A"))
        st.metric("Total Return", f"{float(ret_val):.2f}%" if isinstance(ret_val, (int, float)) else str(ret_val))
    with kpi3:
        sharpe = stats.get("Sharpe Ratio (252 days)", stats.get("sharpe_ratio", "N/A"))
        st.metric("Sharpe Ratio", f"{float(sharpe):.3f}" if isinstance(sharpe, (int, float)) else str(sharpe))
    with kpi4:
        max_dd = stats.get("Max Drawdown [%]", stats.get("max_drawdown", "N/A"))
        st.metric("Max Drawdown", f"{float(max_dd):.2f}%" if isinstance(max_dd, (int, float)) else str(max_dd))

    kpi5, kpi6, kpi7, kpi8 = st.columns(4)
    with kpi5:
        sortino = stats.get("Sortino Ratio (252 days)", stats.get("sortino_ratio", "N/A"))
        st.metric("Sortino Ratio", f"{float(sortino):.3f}" if isinstance(sortino, (int, float)) else str(sortino))
    with kpi6:
        win_rate = stats.get("Win Rate [%]", stats.get("win_rate", "N/A"))
        st.metric("Win Rate", f"{float(win_rate):.1f}%" if isinstance(win_rate, (int, float)) else str(win_rate))
    with kpi7:
        tot_orders = stats.get("total_orders", stats.get("Orders count", 0))
        st.metric("Total Orders", f"{tot_orders:,}" if isinstance(tot_orders, (int, float)) else str(tot_orders))
    with kpi8:
        tot_pos = stats.get("total_positions", stats.get("Positions count", 0))
        st.metric("Total Positions", f"{tot_pos:,}" if isinstance(tot_pos, (int, float)) else str(tot_pos))

    st.divider()

    # Equity Curve & Returns Chart
    st.subheader("Equity Curve & Cumulative Returns")

    account_df = load_csv(run_dir, "account.csv")
    returns_df = load_csv(run_dir, "returns_series.csv")

    if not account_df.empty:
        time_col = None
        for col in ["timestamp", "ts_event", "date", "time"]:
            if col in account_df.columns:
                time_col = col
                break

        val_col = None
        for col in ["total", "balance", "equity"]:
            if col in account_df.columns:
                val_col = col
                break

        if val_col:
            plot_df = account_df.copy()
            if time_col:
                plot_df["Time"] = pd.to_datetime(plot_df[time_col])
                plot_df = plot_df.sort_values(by="Time").set_index("Time")
            st.line_chart(plot_df[[val_col]], use_container_width=True)
            st.caption("Account Portfolio Value over time (INR)")
    elif not returns_df.empty and "return" in returns_df.columns:
        ret_series = returns_df["return"].astype(float)
        cum_equity = (1.0 + ret_series).cumprod()
        date_col = "date_ist" if "date_ist" in returns_df.columns else "timestamp_utc"
        chart_data = pd.DataFrame({"Cumulative Return": cum_equity.values})
        if date_col in returns_df.columns:
            chart_data.index = pd.to_datetime(returns_df[date_col])
        st.line_chart(chart_data, use_container_width=True)
        st.caption("Cumulative Returns Series")
    else:
        st.info("No account or return time-series data available for this run.")

    # Detailed statistics table
    stats_df = load_csv(run_dir, "stats.csv")
    if not stats_df.empty:
        with st.expander("Full Backtest Statistics Table", expanded=False):
            st.dataframe(stats_df, use_container_width=True, hide_index=True)
