"""
qbeast_backtest/nautilus_books.py
------------------------------
MOVED. The implementation now lives in `qbeast_exec/books.py`, so that the shared library is a single
self-contained bundle: tax, charges, slippage, price bands, the market-wide breaker, the Nautilus fee and
fill models, and the order/trade book exporter all arrive with one import.

This file is kept as a re-export because five call sites in this project import it by name, and breaking
them to save one file would be a bad trade. New code should use:

    from qbeast_exec import export_books
"""
from __future__ import annotations

from qbeast_exec.books import export_books  # noqa: F401

__all__ = ["export_books"]
