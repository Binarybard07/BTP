from typing import Any, Dict
import pandas as pd
import streamlit as st

from data_loader import load_csv


def render_market_data(run_dir: str, metadata: Dict[str, Any]) -> None:
    st.header("📈 Market Data & Synthetic Quotes")

    bars_df = load_csv(run_dir, "bars.csv")
    quotes_df = load_csv(run_dir, "opening_quotes.csv")

    if bars_df.empty and quotes_df.empty:
        st.info("No market data artifacts found in this run directory.")
        return

    # Symbol selector
    all_symbols = []
    if not bars_df.empty and "symbol" in bars_df.columns:
        all_symbols = sorted(list(bars_df["symbol"].unique()))
    elif not quotes_df.empty and "symbol" in quotes_df.columns:
        all_symbols = sorted(list(quotes_df["symbol"].unique()))

    if not all_symbols:
        all_symbols = metadata.get("symbols", [])

    selected_symbol = st.selectbox("Select Instrument Symbol", all_symbols, index=0)

    tab1, tab2 = st.tabs(["Daily Bars (OHLCV)", "Synthetic Opening Quotes (T+1 Open)"])

    with tab1:
        st.subheader(f"OHLCV Bars: {selected_symbol}")
        if bars_df.empty:
            st.info("No bar data available.")
        else:
            sym_bars = bars_df[bars_df["symbol"] == selected_symbol].copy()
            if sym_bars.empty:
                st.warning(f"No bars recorded for symbol {selected_symbol}")
            else:
                if "date" in sym_bars.columns:
                    sym_bars["Date"] = pd.to_datetime(sym_bars["date"])
                    chart_df = sym_bars.sort_values(by="Date").set_index("Date")
                    price_cols = [c for c in ["close", "open", "high", "low"] if c in chart_df.columns]
                    if price_cols:
                        st.line_chart(chart_df[price_cols], use_container_width=True)
                        st.caption(f"{selected_symbol} Price History (INR)")

                st.caption(f"Showing {len(sym_bars)} daily bars")
                st.dataframe(sym_bars, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader(f"Opening Quotes: {selected_symbol}")
        st.info(
            "ℹ️ **Note on Synthetic Quotes**: These opening quotes were generated from session T+1's recorded "
            "opening price at 09:15:00 IST under the simulator's zero-spread assumption to simulate next-session open execution."
        )
        if quotes_df.empty:
            st.info("No opening quotes recorded.")
        else:
            sym_quotes = quotes_df[quotes_df["symbol"] == selected_symbol].copy()
            if sym_quotes.empty:
                st.warning(f"No opening quotes recorded for symbol {selected_symbol}")
            else:
                st.caption(f"Showing {len(sym_quotes)} opening quotes")
                st.dataframe(sym_quotes, use_container_width=True, hide_index=True)
