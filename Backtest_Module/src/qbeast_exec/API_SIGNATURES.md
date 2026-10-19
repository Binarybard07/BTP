# qbeast_exec — API signatures

**Version 1.1.1** · 40 exports · generated from the source by `python -m qbeast_exec.gen_signatures`.

> Generated, not written. If this disagrees with the code, the code is right and this file is stale — regenerate it.

## Signatures that mislead

These were each got wrong while writing the isolation audit, **with the source open**. Read this section before the table.

| Member | What to watch |
| --- | --- |
| `ChargesConfig.blended` | a factory that **requires** the blended rate: `blended(0.0020)`. |
| `Dashboard` | a **dataclass**, not a builder. `Dashboard(nav, benchmark=, tax=, slippage=, charges=, trades=, title=, subtitle=, periods_per_year=)`. `title` and the rest are **fields** -- `dash.title('x')` raises "'str' object is not callable". Only `stats()`, `to_html()` and `write(path)` are methods. |
| `DeferredSubmit.queue` | `queue(day, {})` is legitimate and means **flatten to cash**. `queue(day, None)` raises -- that is a programming error. |
| `MarketWideBreaker.reopen_gap` | needs the move: `reopen_gap(date, pct_move, recovery=0.5)`. |
| `MarketWideBreaker.stress` | needs the move: `stress(date, pct_move)`. |
| `PriceBandModel.clamp` | argument order is `(symbol, price, prev_close, date=None)` -- price **before** prev_close, and the date last and optional. |
| `PriceBandModel.infer_band` | takes a **return series**, not a symbol: `infer_band(closes['AAA'].pct_change())`. |
| `PriceBandModel.scan` | takes **no** date -- it scans the whole panel. |
| `SlippageModel.capacity` | takes a **list** of symbols and a **list** of portfolio sizes: `capacity(['AAA'], date, [1e6], top_n=20)`. |
| `SlippageModel.realised` | takes **no** arguments. It reports the fills the model has already priced; it does not price one. |
| `SlippageModel.reference_price` | requires an explicit `fallback` price -- there is no default. |
| `StaircaseFlag.summary` | takes **no** date (`table()` and `report()` do). |
| `TaxEngine.apply_fills` | keys on `date` / `price` by default, **not** `ts` / `px`. Pass the column names if yours differ: `apply_fills(f, date='ts', price='px')`. |
| `TaxEngine.assess_all` | takes **no** date. `assess(date)` is the one that advances the FY assessment; `assess_all()` returns the whole table. |
| `export_books` | **always pass `opens=`**. Without it both books report the previous session's close -- a price that was never executable. |
| `make_bars` | `offset_ns=0` for the CLOCK instrument and `offset_ns=1` for tradables. Equal offsets make the fill price depend on feed order, i.e. undefined. |
| `replay_fills` | **always pass `opens=`**, for the same reason. `fills` is a sequence of `dict(ts=, symbol=, qty=, px=, comm=)`. |
| `reprice_to_open` | pass `mask=filled_qty > 0` for an ORDER book -- without it a rejected order's `avg_px` of 0 is stamped with a real open price, inventing a trade. |

## Every public export

### Functions

| Function | Signature |
| --- | --- |
| `NautilusFeeModel` | `NautilusFeeModel(config: 'ChargesConfig | None' = None, extra_rate: 'float | dict[str, float]' = 0.0)` |
| `NautilusFillModel` | `NautilusFillModel(slippage: 'SlippageModel', prob_fill_on_limit: 'float' = 1.0, random_seed: 'int | None' = 7)` |
| `aftertax_nav` | `aftertax_nav(fills, closes, cal, initial, **kw) -> 'pd.Series'` |
| `apply_fill` | `apply_fill(led: 'Ledger', date, symbol: 'str', qty: 'int', px: 'float', comm: 'float', _warned: 'list | None' = None) -> 'None'` |
| `assert_no_look_ahead` | `assert_no_look_ahead(membership: "'Membership'", start) -> 'None'` |
| `atr_pct` | `atr_pct(d: 'pd.DataFrame', n: 'int' = 21) -> 'pd.Series'` |
| `corwin_schultz` | `corwin_schultz(high: 'pd.Series', low: 'pd.Series', window: 'int' = 21) -> 'pd.Series'` |
| `drawdown_table` | `drawdown_table(nav: 'pd.Series', top: 'int' = 10) -> 'pd.DataFrame'` |
| `export_books` | `export_books(engine, tag: 'str', outdir, order_dates=None, opens=None)` |
| `find_membership_file` | `find_membership_file(path, extra_dirs=()) -> 'Path | None'` |
| `load_membership` | `load_membership(path, config: 'SurvivorshipConfig | None' = None, extra_dirs=()) -> 'list[tuple[pd.Timestamp, set[str]]]'` |
| `make_bars` | `make_bars(instrument, o, h, l, c, v, offset_ns: 'int' = 0, precision: 'int' = 2)` |
| `make_equity` | `make_equity(symbol: 'str', venue: 'str' = 'NSE', currency: 'str' = 'INR', tick: 'str' = '0.05', precision: 'int' = 2)` |
| `normalise` | `normalise(symbol: 'str') -> 'str'` |
| `performance_stats` | `performance_stats(nav: 'pd.Series', periods_per_year: 'int' = 252, rf: 'float' = 0.0) -> 'dict'` |
| `replay_fills` | `replay_fills(fills, closes: 'pd.DataFrame', cal, initial: 'float', *, opens=None, tax: 'bool' = True, tax_config: 'TaxConfig | None' = None, charges_config: 'ChargesConfig | None' = None, cash_yield: 'float' = 0.0, verbose: 'bool' = True) -> 'Ledger'` |
| `split_commission` | `split_commission(calc: 'ChargesCalculator', qty: 'int', px: 'float', comm: 'float', _warned: 'list | None' = None) -> 'tuple[float, float]'` |

### Classes

#### `BandConfig`  *(dataclass — these are FIELDS, not methods)*

```python
BandConfig(candidate_bands: 'tuple' = (0.02, 0.05, 0.1, 0.2), tol: 'float' = 0.004, max_slack: 'float' = 1.05, quantile: 'float' = 0.999, min_history: 'int' = 100, min_price: 'float' = 5.0, tick: 'float' = 0.05, block_buy_on_upper: 'bool' = True, block_sell_on_lower: 'bool' = True, clamp_fills_to_band: 'bool' = False) -> None
```

Fields: `candidate_bands`, `tol`, `max_slack`, `quantile`, `min_history`, `min_price`, `tick`, `block_buy_on_upper`, `block_sell_on_lower`, `clamp_fills_to_band`

#### `ChargesCalculator`

```python
ChargesCalculator(config: 'ChargesConfig | None' = None) -> 'None'
```

| Method | Signature |
| --- | --- |
| `breakdown` | `breakdown(self, side, qty: 'float', price: 'float') -> 'dict[str, float]'` |
| `effective_rate` | `effective_rate(self, side, price: 'float' = 1000.0, qty: 'float' = 1000.0) -> 'float'` |
| `schedule` | `schedule(self, price: 'float' = 1000.0, qty: 'float' = 1000.0) -> 'pd.DataFrame'` |
| `total` | `total(self, side, qty: 'float', price: 'float') -> 'float'` |

#### `ChargesConfig`  *(dataclass — these are FIELDS, not methods)*

```python
ChargesConfig(name: 'str' = 'equity_delivery', brokerage_pct: 'float' = 0.0, brokerage_flat_max: 'float' = 0.0, stt_buy: 'float' = 0.001, stt_sell: 'float' = 0.001, exchange_txn: 'float' = 2.97e-05, sebi_fee: 'float' = 1e-06, stamp_duty_buy: 'float' = 0.00015, stamp_duty_sell: 'float' = 0.0, gst_rate: 'float' = 0.18, dp_charge_flat_sell: 'float' = 0.0, currency: 'str' = 'INR') -> None
```

Fields: `name`, `brokerage_pct`, `brokerage_flat_max`, `stt_buy`, `stt_sell`, `exchange_txn`, `sebi_fee`, `stamp_duty_buy`, `stamp_duty_sell`, `gst_rate`, `dp_charge_flat_sell`, `currency`

| Method | Signature |
| --- | --- |
| `blended` | `blended(rate: 'float', **kw) -> "'ChargesConfig'"` |
| `equity_delivery` | `equity_delivery(**kw) -> "'ChargesConfig'"` |
| `equity_intraday` | `equity_intraday(**kw) -> "'ChargesConfig'"` |
| `futures` | `futures(**kw) -> "'ChargesConfig'"` |
| `options` | `options(**kw) -> "'ChargesConfig'"` |

#### `Dashboard`  *(dataclass — these are FIELDS, not methods)*

```python
Dashboard(nav: 'pd.Series', benchmark: 'pd.Series | None' = None, tax: 'object | None' = None, slippage: 'object | None' = None, charges: 'object | None' = None, trades: 'pd.DataFrame | None' = None, title: 'str' = 'Strategy report', subtitle: 'str' = '', periods_per_year: 'int' = 252) -> None
```

Fields: `nav`, `benchmark`, `tax`, `slippage`, `charges`, `trades`, `title`, `subtitle`, `periods_per_year`

| Method | Signature |
| --- | --- |
| `stats` | `stats(self) -> 'dict'` |
| `to_html` | `to_html(self) -> 'str'` |
| `write` | `write(self, path) -> 'str'` |

#### `DeferredSubmit`

```python
DeferredSubmit() -> 'None'
```

| Method | Signature |
| --- | --- |
| `queue` | `queue(self, day, target) -> 'None'` |
| `release` | `release(self)` |

Properties: `released`, `waiting`

#### `Ledger`

```python
Ledger(capital: 'float', *, tax_config: 'TaxConfig | None' = None, charges_config: 'ChargesConfig | None' = None, cash_yield: 'float' = 0.0, tax: 'bool' = True, bands=None, staircase=None) -> 'None'
```

| Method | Signature |
| --- | --- |
| `equity_curve` | `equity_curve(self) -> 'pd.Series'` |
| `mark` | `mark(self, date: 'pd.Timestamp', close_row: 'pd.Series') -> 'float'` |
| `nav_at` | `nav_at(self, price_row: 'pd.Series') -> 'float'` |

Properties: `assessed`, `lot_closes`, `lots`, `realized`

#### `Lot`  *(dataclass — these are FIELDS, not methods)*

```python
Lot(date: 'pd.Timestamp', qty: 'float', price: 'float') -> None
```

Fields: `date`, `qty`, `price`

#### `LotClose`  *(dataclass — these are FIELDS, not methods)*

```python
LotClose(symbol: 'str', buy_date: 'pd.Timestamp', sell_date: 'pd.Timestamp', days_held: 'int', qty: 'float', buy_price: 'float', sell_price: 'float', gain: 'float', term: 'str', regime: 'str') -> None
```

Fields: `symbol`, `buy_date`, `sell_date`, `days_held`, `qty`, `buy_price`, `sell_price`, `gain`, `term`, `regime`

#### `MWCBConfig`  *(dataclass — these are FIELDS, not methods)*

```python
MWCBConfig(levels: 'tuple' = (0.1, 0.15, 0.2), halts: 'dict' = <factory>, reopen_auction_minutes: 'int' = 15) -> None
```

Fields: `levels`, `halts`, `reopen_auction_minutes`

#### `MarketWideBreaker`

```python
MarketWideBreaker(index_close: "'pd.Series'", config: 'MWCBConfig | None' = None, index_name: 'str' = 'NIFTY 50') -> 'None'
```

| Method | Signature |
| --- | --- |
| `clear_stress` | `clear_stress(self) -> 'int'` |
| `halt_minutes` | `halt_minutes(self, level: 'float', when: 'str' = 'pre1300')` |
| `near_misses` | `near_misses(self, floor: 'float' = 0.05) -> "'pd.DataFrame'"` |
| `reopen_gap` | `reopen_gap(self, date, pct_move: 'float', recovery: 'float' = 0.5) -> 'dict'` |
| `report` | `report(self) -> 'str'` |
| `scan` | `scan(self) -> "'pd.DataFrame'"` |
| `state` | `state(self, date, when: 'str' = 'pre1300') -> 'dict'` |
| `stress` | `stress(self, date, pct_move: 'float') -> 'dict'` |
| `summary` | `summary(self) -> 'dict'` |

#### `Membership`

```python
Membership(path=None, available: 'set[str] | None' = None, config: 'SurvivorshipConfig | None' = None, extra_dirs=()) -> 'None'
```

| Method | Signature |
| --- | --- |
| `audit` | `audit(self, start=None, end=None) -> 'dict'` |
| `coverage` | `coverage(self) -> 'pd.DataFrame'` |
| `eligible` | `eligible(self, date) -> 'set[str]'` |
| `members` | `members(self, date) -> 'set[str]'` |
| `missing` | `missing(self, date=None) -> 'set[str]'` |
| `report` | `report(self, start=None, end=None) -> 'str'` |

Properties: `enabled`, `first_date`, `last_date`

#### `PriceBandModel`

```python
PriceBandModel(frames: 'dict[str, pd.DataFrame] | None' = None, config: 'BandConfig | None' = None, causal: 'bool' = False) -> 'None'
```

| Method | Signature |
| --- | --- |
| `band` | `band(self, symbol: 'str', date=None) -> 'float | None'` |
| `bands_table` | `bands_table(self) -> 'pd.DataFrame'` |
| `blocked_table` | `blocked_table(self) -> 'pd.DataFrame'` |
| `can_trade` | `can_trade(self, symbol: 'str', date, side) -> 'tuple[bool, str]'` |
| `check` | `check(self, symbol: 'str', date, side, qty: 'float' = 0.0) -> 'bool'` |
| `clamp` | `clamp(self, symbol: 'str', price: 'float', prev_close: 'float', date=None) -> 'float'` |
| `infer_band` | `infer_band(self, ret: 'pd.Series') -> 'float | None'` |
| `scan` | `scan(self) -> 'pd.DataFrame'` |
| `state` | `state(self, symbol: 'str', date) -> 'str'` |
| `summary` | `summary(self) -> 'dict'` |

#### `SlippageConfig`  *(dataclass — these are FIELDS, not methods)*

```python
SlippageConfig(mode: 'str' = 'dynamic', fixed_rate: 'float' = 0.0005, rank_min: 'float' = 0.0005, rank_max: 'float' = 0.0025, y_impact: 'float' = 0.5, c_atr: 'float' = 0.1, tick: 'float' = 0.05, max_participation: 'float' = 0.1, participation_cap: 'float' = 1.0, cs_window: 'int' = 21, vol_window: 'int' = 21, adv_window: 'int' = 21, atr_n: 'int' = 21, seed: 'int' = 7) -> None
```

Fields: `mode`, `fixed_rate`, `rank_min`, `rank_max`, `y_impact`, `c_atr`, `tick`, `max_participation`, `participation_cap`, `cs_window`, `vol_window`, `adv_window`, `atr_n`, `seed`

#### `SlippageModel`

```python
SlippageModel(frames: 'dict[str, pd.DataFrame] | None' = None, config: 'SlippageConfig | None' = None) -> 'None'
```

| Method | Signature |
| --- | --- |
| `capacity` | `capacity(self, symbols, date, portfolios, top_n: 'int' = 20, prices: 'dict | None' = None) -> 'pd.DataFrame'` |
| `draw` | `draw(self, symbol: 'str', date, side, qty: 'float' = 0.0, price: 'float' = 0.0) -> 'float'` |
| `fill_price` | `fill_price(self, symbol: 'str', date, side, qty: 'float', reference: 'float') -> 'float'` |
| `realised` | `realised(self) -> 'pd.DataFrame'` |
| `reference_price` | `reference_price(self, symbol: 'str', date, fallback: 'float') -> 'float'` |
| `summary` | `summary(self) -> 'dict'` |
| `terms` | `terms(self, symbol: 'str', date, qty: 'float' = 0.0, price: 'float' = 0.0) -> 'dict'` |

#### `StaircaseConfig`  *(dataclass — these are FIELDS, not methods)*

```python
StaircaseConfig(window: 'int' = 20, min_hits: 'int' = 3, min_run: 'int' = 3, direction: 'str' = 'up', min_price: 'float' = 5.0, block_buy: 'bool' = True, block_sell: 'bool' = False, require_lock: 'bool' = False) -> None
```

Fields: `window`, `min_hits`, `min_run`, `direction`, `min_price`, `block_buy`, `block_sell`, `require_lock`

#### `StaircaseFlag`

```python
StaircaseFlag(frames: 'dict | None' = None, config: 'StaircaseConfig | None' = None, bands: "'PriceBandModel | None'" = None) -> 'None'
```

| Method | Signature |
| --- | --- |
| `blocked_table` | `blocked_table(self) -> 'pd.DataFrame'` |
| `can_trade` | `can_trade(self, symbol: 'str', date, side) -> 'tuple[bool, str]'` |
| `check` | `check(self, symbol: 'str', date, side, qty: 'float' = 0.0) -> 'bool'` |
| `flagged` | `flagged(self, symbol: 'str', date) -> 'bool'` |
| `flagged_symbols` | `flagged_symbols(self, date) -> 'list[str]'` |
| `report` | `report(self, date, held=None) -> 'str'` |
| `scan` | `scan(self) -> 'pd.DataFrame'` |
| `steps` | `steps(self, symbol: 'str', date) -> 'tuple[int, int]'` |
| `summary` | `summary(self) -> 'dict'` |
| `table` | `table(self, date, held=None) -> 'pd.DataFrame'` |

#### `SurvivorshipConfig`  *(dataclass — these are FIELDS, not methods)*

```python
SurvivorshipConfig(alias: 'dict[str, str]' = <factory>, junk: 'frozenset[str]' = frozenset({'722HPCL29', 'NIFTY'}), strict: 'bool' = True, date_column: 'str' = 'effective_date', symbols_column: 'str' = 'symbols') -> None
```

Fields: `alias`, `junk`, `strict`, `date_column`, `symbols_column`

#### `TaxConfig`  *(dataclass — these are FIELDS, not methods)*

```python
TaxConfig(regime_change: 'pd.Timestamp' = Timestamp('2024-07-23 00:00:00'), stcg_rate_old: 'float' = 0.15, stcg_rate_new: 'float' = 0.2, ltcg_rate_old: 'float' = 0.1, ltcg_rate_new: 'float' = 0.125, ltcg_exemption_old: 'float' = 100000.0, ltcg_exemption_new: 'float' = 125000.0, exemption_change_fy: 'int' = 2024, ltcg_hold_months: 'int' = 12, ltcg_hold_days: 'int | None' = None, ltcg_boundary_inclusive: 'bool' = False, fy_start_month: 'int' = 4, carry_forward_losses: 'bool' = False, carry_forward_years: 'int' = 8, cess: 'float' = 0.0, surcharge: 'float' = 0.0) -> None
```

Fields: `regime_change`, `stcg_rate_old`, `stcg_rate_new`, `ltcg_rate_old`, `ltcg_rate_new`, `ltcg_exemption_old`, `ltcg_exemption_new`, `exemption_change_fy`, `ltcg_hold_months`, `ltcg_hold_days`, `ltcg_boundary_inclusive`, `fy_start_month`, `carry_forward_losses`, `carry_forward_years`, `cess`, `surcharge`

| Method | Signature |
| --- | --- |
| `exemption` | `exemption(self, fy: 'int') -> 'float'` |
| `gross_up` | `gross_up(self, tax: 'float') -> 'float'` |
| `is_long_term` | `is_long_term(self, buy_date, sell_date) -> 'bool'` |
| `long_term_on_or_after` | `long_term_on_or_after(self, buy_date) -> 'pd.Timestamp'` |
| `rates` | `rates(self, sale_date) -> 'tuple[float, float]'` |

#### `TaxEngine`

```python
TaxEngine(config: 'TaxConfig | None' = None) -> 'None'
```

| Method | Signature |
| --- | --- |
| `apply_fills` | `apply_fills(self, fills, symbol='symbol', date='date', qty='qty', price='price') -> 'None'` |
| `assess` | `assess(self, date) -> 'float'` |
| `assess_all` | `assess_all(self) -> 'pd.DataFrame'` |
| `buy` | `buy(self, symbol: 'str', date, qty: 'float', price: 'float') -> 'None'` |
| `closed_lots` | `closed_lots(self) -> 'pd.DataFrame'` |
| `fy` | `fy(self, date) -> 'int'` |
| `fy_end` | `fy_end(self, fy: 'int') -> 'pd.Timestamp'` |
| `fy_label` | `fy_label(fy: 'int') -> 'str'` |
| `holding_period_summary` | `holding_period_summary(self) -> 'pd.DataFrame'` |
| `open_lots` | `open_lots(self) -> 'pd.DataFrame'` |
| `position` | `position(self, symbol: 'str') -> 'float'` |
| `sell` | `sell(self, symbol: 'str', date, qty: 'float', price: 'float') -> 'dict[str, float]'` |
| `statement` | `statement(self) -> 'pd.DataFrame'` |
| `tax_for` | `tax_for(self, fy: 'int') -> 'float'` |

#### `UniverseReport`

```python
UniverseReport(root, universe: 'str', create: 'bool' = True) -> 'None'
```

| Method | Signature |
| --- | --- |
| `compare` | `compare(root, universes=None, columns=None) -> 'pd.DataFrame'` |
| `load` | `load(self) -> 'dict'` |
| `path` | `path(self, *parts) -> 'Path'` |
| `save` | `save(self, stats: 'dict', benchmark: 'dict | None' = None, window=None, **meta) -> 'Path'` |
| `table` | `table(root, universes=None, columns=None) -> 'str'` |
| `universes` | `universes(root) -> 'list[str]'` |

### Data

| Name | Type |
| --- | --- |
| `DEFAULT_ALIAS` | `dict` |
| `DEFAULT_JUNK` | `frozenset` |

---

## Verifying a drop-in

After copying `qbeast_exec/` into your project:

```bash
python -m qbeast_exec.selftest          # 190 assertions on the accounting itself
python -m qbeast_exec.isolation_audit   # every public callable, from YOUR project
```

The audit asserts two things the selftest cannot: that **no** module of the originating project is reachable, and that **every** public callable runs from your copy alone. A library that quietly imports someone's `config.py` works perfectly at home and fails on the first machine it is copied to.

