"""
qbeast_exec/books.py
------------------------------
ORDER BOOK + TRADE BOOK — portfolio-wise and stock-wise — straight from NautilusTrader's own records.
NautilusTrader is the ONLY execution engine here, so these come from its real order + fill events: they
carry true order STATUS (FILLED / DENIED / CANCELED / ...), venue order ids, and per-fill commissions.
Shared by nautilus_run.py (backtest) and nautilus_live.py (paper/live) via export_books(engine, tag).

Writes to reports/:
  orderbook_<tag>.csv     every order, chronological, with status + filled qty + avg price + commission
  tradebook_<tag>.csv     every fill (trade), chronological, with price / qty / value / commission
  book_by_stock_<tag>.csv STOCK-WISE roll-up: per symbol #orders/#trades, buy/sell qty+value, commission, realized PnL
  book_summary_<tag>.txt  PORTFOLIO-WISE totals + order-status breakdown
"""
from __future__ import annotations

import pandas as pd


def _money(x) -> float:
    if x is None:
        return 0.0
    if isinstance(x, (list, tuple)):
        return sum(_money(i) for i in x)
    try:
        return float(x.as_double())
    except Exception:
        try:
            return float(str(x).split()[0].replace(",", ""))
        except Exception:
            return 0.0


def _sym(x) -> str:
    return str(x).split(".")[0]


def _num(series):
    return pd.to_numeric(series.astype(str), errors="coerce")


# Column dtypes matter for the EMPTY case, not just for tidiness: an all-object empty frame makes the
# per-symbol running position below fail with "cumsum is not supported for object dtype".
_OB_COLS = {"date": "object", "time": "datetime64[ns]", "symbol": "object", "side": "object",
            "type": "object", "qty": "float64", "filled_qty": "float64", "avg_px": "float64",
            "commission": "float64", "status": "object", "order_id": "object"}
_TB_COLS = {"date": "object", "time": "datetime64[ns]", "symbol": "object", "side": "object",
            "qty": "float64", "price": "float64", "commission": "float64", "trade_id": "object",
            "value": "float64"}


def _empty(cols: dict) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d in cols.items()})


def reprice_to_open(df, date_col: str, px_cols, opens, symbol_col: str = "symbol", mask=None):
    """Reprice rows to the OPEN of their own session. Returns (frame, rows_changed).

    WHY A BOOK EVER NEEDS THIS: under bar execution, a market order submitted before the session's own
    bar has been processed is matched against the newest bar the engine HAS -- the previous session's.
    The resulting price is not executable: that session has closed. Repricing to the open of the fill
    date is what the order could actually have transacted at.

    `opens` is a DataFrame of opens indexed by date with symbols as columns. Rows whose symbol or date
    is absent keep the price they came with, so a partial panel degrades instead of corrupting.

    `mask` is an optional boolean Series selecting the rows eligible to be repriced; rows outside it
    keep their price untouched. It exists for the ORDER book, where a rejected or cancelled order
    carries `avg_px` 0 -- repricing that would stamp a real open price on an order that never filled,
    inventing a trade. Pass `filled_qty > 0` there. The trade book needs no mask: a fill row exists
    only because it filled."""
    import numpy as np
    if opens is None or df is None or not len(df):
        return df, 0
    if date_col not in df.columns:
        # Silently repricing nothing is how this fix would look applied while doing nothing at all.
        raise KeyError(f"reprice_to_open: no {date_col!r} column; have {list(df.columns)}")
    if mask is not None:
        mask = pd.Series(mask, index=df.index).fillna(False).astype(bool)
    changed = 0
    for col in ([px_cols] if isinstance(px_cols, str) else px_cols):
        if col not in df.columns:
            continue
        new_px = []
        for i, r in df.iterrows():
            px = float(r[col]) if r[col] is not None else float("nan")
            if mask is not None and not bool(mask.loc[i]):
                new_px.append(px)                              # not eligible: never filled
                continue
            try:
                # Nautilus stamps fills in UTC; the price panels are tz-naive. Dropping the zone before
                # the lookup is required -- without it every .loc raises and the reprice silently does
                # nothing, which is how this landed "applied" but inert the first time.
                d = pd.Timestamp(r[date_col])
                d = (d.tz_convert(None) if d.tzinfo is not None else d).normalize()
                o = float(opens.loc[d].get(str(r[symbol_col]).upper(), float("nan")))
            except (KeyError, ValueError, TypeError):              # date or symbol absent from the panel
                o = float("nan")
            if np.isfinite(o) and o > 0 and np.isfinite(px) and abs(o - px) > 1e-9:
                changed += 1
                new_px.append(round(o, 2))
            else:
                new_px.append(px)
        df[col] = new_px
    return df, changed


def export_books(engine, tag: str, outdir, order_dates=None, opens=None):
    tr = engine.trader
    orders, fills, pos = (tr.generate_orders_report(), tr.generate_fills_report(),
                          tr.generate_positions_report())

    # A run that placed NO orders is a legitimate outcome, not a failure: a window the trend gate held
    # fully in cash, a window too short to reach a rebalance, or capital too small to afford one share.
    # Nautilus reports that as a DataFrame with no rows AND NO COLUMNS, so reading orders["ts_last"]
    # below used to raise KeyError and take the whole backtest down with it. Build empty books with the
    # right columns instead, so every downstream consumer sees the normal shape with zero rows.
    if not len(orders):
        ob = _empty(_OB_COLS)
        if order_dates:
            ob = ob.rename(columns={"date": "fill_date"})
            ob.insert(0, "order_date", pd.Series(dtype=object))
    else:
        # ---- ORDER BOOK (portfolio-wise) — every order and its status
        ob = pd.DataFrame({
            "time": [pd.Timestamp(t) for t in orders["ts_last"]],
            "symbol": [_sym(s) for s in orders["instrument_id"]],
            "side": orders["side"].astype(str).values,
            "type": orders["type"].astype(str).values,
            "qty": _num(orders["quantity"]).values,
            "filled_qty": _num(orders["filled_qty"]).values,
            "avg_px": _num(orders["avg_px"]).round(2).values,
            "commission": [round(_money(c), 2) for c in orders["commissions"]],
            "status": orders["status"].astype(str).values,
            "order_id": orders.index.astype(str),
        }).sort_values("time").reset_index(drop=True)
        # Reprice avg_px the same way the trade book is repriced, and for the same reason. This was
        # MISSED when the fill-price fix went in: the ledger and the trade book were corrected, the
        # order book was not, so 79.5% of avg_px values were still the PREVIOUS session's close while
        # tradebook.price beside them held the fill-day open. Two files describing the same fills
        # disagreed, and avg_px is the one an operator opens to ask "what price did I get".
        # Masked to filled rows: a rejected order's avg_px is 0 and must stay 0.
        ob, _n_ob = reprice_to_open(ob, "time", "avg_px", opens,
                                    mask=_num(ob["filled_qty"]) > 0)
        if _n_ob:
            print(f"  books[{tag}]: repriced {_n_ob:,} order-book avg_px to the fill-day open",
                  flush=True)
        ob.insert(0, "date", pd.to_datetime(ob["time"]).dt.strftime("%Y-%m-%d"))  # ts_last = FILL date (t+1)
        if order_dates:                                      # decision date (t): order PLACED on t, fills t+1
            ob.insert(0, "order_date", ob["order_id"].map(order_dates).fillna(ob["date"]))
            ob.rename(columns={"date": "fill_date"}, inplace=True)

    # ---- TRADE BOOK (portfolio-wise) — every fill
    if not len(fills):
        tb = _empty(_TB_COLS)
    else:
        tb = pd.DataFrame({
            "time": [pd.Timestamp(t) for t in fills["ts_event"]],
            "symbol": [_sym(s) for s in fills["instrument_id"]],
            "side": fills["order_side"].astype(str).values,
            "qty": _num(fills["last_qty"]).values,
            "price": _num(fills["last_px"]).round(2).values,
            "commission": [round(_money(c), 2) for c in fills["commission"]],
            "trade_id": fills["trade_id"].astype(str).values,
        }).sort_values("time").reset_index(drop=True)
        # Reprice to the session's own open BEFORE deriving value, so the book agrees with the ledger
        # built from the same fills. Without `opens` nothing moves and the engine's prices stand.
        tb, _n_tb = reprice_to_open(tb, "time", "price", opens)   # "date" is inserted below, not yet
        tb["value"] = (tb["qty"] * tb["price"]).round(2)
        if _n_tb:
            print(f"  books[{tag}]: repriced {_n_tb:,} trade-book row(s) to the fill-day open",
                  flush=True)
        tb.insert(0, "date", pd.to_datetime(tb["time"]).dt.strftime("%Y-%m-%d"))  # date-sequential ledger

    # ---- STOCK-WISE roll-up
    buy, sell = tb["side"].str.contains("BUY"), tb["side"].str.contains("SELL")
    stock = pd.DataFrame(index=sorted(set(tb["symbol"])))
    stock["orders"] = ob.groupby("symbol").size()
    stock["trades"] = tb.groupby("symbol").size()
    stock["buy_qty"] = tb[buy].groupby("symbol")["qty"].sum()
    stock["sell_qty"] = tb[sell].groupby("symbol")["qty"].sum()
    stock["buy_value"] = tb[buy].groupby("symbol")["value"].sum()
    stock["sell_value"] = tb[sell].groupby("symbol")["value"].sum()
    stock["commission"] = tb.groupby("symbol")["commission"].sum()
    if len(pos):
        p = pos.copy()
        p["symbol"] = [_sym(s) for s in p["instrument_id"]]
        p["rpnl"] = [_money(x) for x in p["realized_pnl"]]
        stock["realized_pnl"] = p.groupby("symbol")["rpnl"].sum()
    stock = stock.fillna(0).round(2).sort_values("trades", ascending=False)
    stock.index.name = "symbol"

    # ---- SYMBOL-WISE order & trade books: every order / every fill grouped BY SYMBOL, date-wise, with a
    # per-symbol running position — so any stock's full order/trade history reads top-to-bottom in one file.
    ob_sym = ob.sort_values(["symbol", "time"]).copy()      # 'date' column is inherited from ob (already first)
    tb_sym = tb.sort_values(["symbol", "time"]).copy()
    _sgn = tb_sym["side"].str.contains("BUY").map({True: 1, False: -1})
    tb_sym["position_after"] = (tb_sym["qty"] * _sgn).groupby(tb_sym["symbol"]).cumsum().astype(int)

    outdir.mkdir(parents=True, exist_ok=True)

    def _write(df, name, **kw):                              # resilient: a locked report (open in Excel)
        try:                                                 # must not crash the whole backtest
            df.to_csv(outdir / name, **kw)
        except PermissionError:
            import time
            alt = name.replace(".csv", f"_{time.strftime('%H%M%S')}.csv")
            df.to_csv(outdir / alt, **kw)
            print(f"  WARN: {name} is locked (open elsewhere?) -> wrote {alt} instead", flush=True)
    _write(ob, f"orderbook_{tag}.csv", index=False)
    _write(tb, f"tradebook_{tag}.csv", index=False)
    _write(stock, f"book_by_stock_{tag}.csv")
    _write(ob_sym, f"orderbook_by_symbol_{tag}.csv", index=False)
    _write(tb_sym, f"tradebook_by_symbol_{tag}.csv", index=False)

    status = ", ".join(f"{k}={v}" for k, v in ob["status"].value_counts().items())
    rpnl = stock["realized_pnl"].sum() if "realized_pnl" in stock else 0.0
    lines = [
        f"NAUTILUS ORDER / TRADE BOOK  —  {tag}   (execution engine: NautilusTrader)",
        "=" * 72,
        f"  PORTFOLIO-WISE",
        f"    orders placed : {len(ob)}   (status: {status})",
        f"    trades (fills): {len(tb)}   across {tb['symbol'].nunique()} stocks",
        f"    buy notional  : Rs {tb.loc[buy, 'value'].sum():,.2f}",
        f"    sell notional : Rs {tb.loc[sell, 'value'].sum():,.2f}",
        f"    commission    : Rs {tb['commission'].sum():,.2f}",
        f"    realized PnL  : Rs {rpnl:,.2f}   (closed lots)",
        "",
        f"  STOCK-WISE (top 12 by trades) — full table in book_by_stock_{tag}.csv",
        stock.head(12).to_string(),
    ]
    (outdir / f"book_summary_{tag}.txt").write_text("\n".join(lines), encoding="utf-8")
    # name the ACTUAL folder: reports/ is partitioned per universe now, so a bare "reports/..." here
    # would point at a path that no longer exists and hide which universe the books belong to.
    print(f"  books[{tag}]: orderbook {len(ob)} rows, tradebook {len(tb)} rows, "
          f"{tb['symbol'].nunique()} stocks -> {outdir}\\ orderbook_{tag}.csv, tradebook_{tag}.csv, "
          f"book_by_stock_{tag}.csv, book_summary_{tag}.txt", flush=True)
    return ob, tb, stock
