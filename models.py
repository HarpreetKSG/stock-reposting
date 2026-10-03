"""
models.py

Nothing fancy here — just the data structures we pass around.
Think of these like the DocTypes in ERPNext (Item, Stock Ledger Entry, etc.)
but as plain Python classes.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from enum import Enum


class TransactionType(Enum):
    PURCHASE_RECEIPT = "Purchase Receipt"    # stock coming in
    DELIVERY_NOTE = "Delivery Note"          # stock going out
    MANUFACTURE = "Manufacture"              # consume raw material, produce finished good


@dataclass
class Item:
    name: str
    uom: str                    # unit of measure — L, KG, etc.
    valuation_method: str = "FIFO"


@dataclass
class BOM:
    """
    Bill of Materials — basically a recipe.
    "Use 100L of Milk to make 10 KG of Milk Powder."
    Maps to ERPNext's BOM DocType.
    """
    raw_item: str
    raw_qty: float
    produced_item: str
    produced_qty: float


@dataclass
class Transaction:
    """
    One stock movement. Could be a purchase, a sale, or manufacturing.
    In ERPNext terms, this is the header of a Purchase Receipt,
    Delivery Note, or Stock Entry (manufacture type).
    """
    txn_id: str
    txn_date: date
    txn_type: TransactionType
    item: str
    qty: float
    rate: Optional[float] = None          # only for purchases
    produced_item: Optional[str] = None   # only for manufacturing
    produced_qty: Optional[float] = None  # only for manufacturing


@dataclass
class StockLedgerEntry:
    """
    A snapshot of stock AFTER a transaction happens.
    ERPNext creates one of these for every stock movement.
    
    The running fields (qty_after_transaction, stock_value, valuation_rate)
    are what make reposting necessary — they go stale when you edit
    an older transaction.
    """
    sle_id: str
    txn_id: str
    txn_date: date
    txn_type: TransactionType
    item: str
    qty_change: float                   # how much moved (+in / -out)
    incoming_rate: float                # rate of stock coming in
    outgoing_rate: float                # FIFO-calculated rate of stock going out
    qty_after_transaction: float        # running qty balance
    stock_value: float                  # running total value
    valuation_rate: float               # value per unit at this point
    fifo_queue_state: list = field(default_factory=list)  # snapshot of FIFO buckets for debugging
