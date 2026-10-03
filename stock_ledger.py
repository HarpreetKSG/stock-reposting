"""
stock_ledger.py

This is the main engine. It processes stock transactions, maintains
the Stock Ledger Entries (like ERPNext's Stock Ledger Entry doctype),
and handles reposting when someone edits a past transaction.

The reposting logic here mirrors what ERPNext does in
erpnext/stock/doctype/repost_item_valuation — but simplified
so you can actually follow what's happening without digging
through 15 files.

Key decisions:
Each item has its own FIFO queue
When we repost, we wipe SLEs from the edit point and replay
  transactions from scratch (same approach ERPNext uses)
Manufacturing creates SLEs for BOTH the raw material and the
  finished good, and reposting cascades across them
"""

import copy
from datetime import date
from typing import Dict, List, Optional
from models import (
    Item, Transaction, TransactionType,
    StockLedgerEntry, BOM
)
from fifo_queue import FIFOQueue


class StockLedger:

    def __init__(self):
        self.items: Dict[str, Item] = {}
        self.transactions: List[Transaction] = []
        self.sle_entries: List[StockLedgerEntry] = []
        self.fifo_queues: Dict[str, FIFOQueue] = {}
        self.bom_registry: List[BOM] = []
        self.opening_stock: Dict[str, List[List[float]]] = {}
        self._sle_counter = 0

  

    def register_item(self, name: str, uom: str) -> None:
        self.items[name] = Item(name=name, uom=uom)
        self.fifo_queues[name] = FIFOQueue()

    def register_bom(self, raw_item: str, raw_qty: float,
                     produced_item: str, produced_qty: float) -> None:
        self.bom_registry.append(BOM(
            raw_item=raw_item, raw_qty=raw_qty,
            produced_item=produced_item, produced_qty=produced_qty
        ))

    def set_opening_stock(self, item: str, qty: float, rate: float) -> None:
        if item not in self.fifo_queues:
            raise ValueError(f"Item '{item}' not registered")
        self.fifo_queues[item].add(qty, rate)
        # save this so we can restore it when reposting wipes everything
        if item not in self.opening_stock:
            self.opening_stock[item] = []
        self.opening_stock[item].append([qty, rate])

    # ── processing transactions ──

    def process_transaction(self, txn: Transaction) -> List[StockLedgerEntry]:
        """Process a transaction for the first time and store its SLEs."""
        self.transactions.append(txn)
        self.transactions.sort(key=lambda t: (t.txn_date, t.txn_id))

        created_sles = self._execute_transaction(txn)

        self.sle_entries.extend(created_sles)
        self.sle_entries.sort(key=lambda s: (s.txn_date, s.txn_id))
        return created_sles

    def _execute_transaction(self, txn: Transaction) -> List[StockLedgerEntry]:
        """
        Routes a transaction to the right handler.
        
        This is the ONLY place where "what type → what handler" mapping lives.
        Both first-time processing and reposting use this same method.
        (DRY — we don't want two copies of this switch logic.)
        """
        handlers = {
            TransactionType.PURCHASE_RECEIPT: lambda: [self._process_purchase(txn)],
            TransactionType.DELIVERY_NOTE: lambda: [self._process_delivery(txn)],
            TransactionType.MANUFACTURE: lambda: self._process_manufacture(txn),
        }

        handler = handlers.get(txn.txn_type)
        if not handler:
            raise ValueError(f"Unknown transaction type: {txn.txn_type}")
        return handler()

    def _process_purchase(self, txn: Transaction) -> StockLedgerEntry:
        """Stock coming in. Just add a bucket to the FIFO queue."""
        queue = self.fifo_queues[txn.item]
        queue.add(txn.qty, txn.rate)

        return self._create_sle(
            txn=txn, item=txn.item,
            qty_change=txn.qty, incoming_rate=txn.rate,
            outgoing_rate=0, queue=queue
        )

    def _process_delivery(self, txn: Transaction) -> StockLedgerEntry:
        """Stock going out. FIFO decides which buckets get consumed."""
        queue = self.fifo_queues[txn.item]
        total_cost = queue.remove(txn.qty)
        outgoing_rate = round(total_cost / txn.qty, 2) if txn.qty else 0

        return self._create_sle(
            txn=txn, item=txn.item,
            qty_change=-txn.qty, incoming_rate=0,
            outgoing_rate=outgoing_rate, queue=queue
        )

    def _process_manufacture(self, txn: Transaction) -> List[StockLedgerEntry]:
        """
        Two things happen:
        1. Raw material gets consumed (outgoing SLE for Milk)
        2. Finished good gets produced (incoming SLE for Milk Powder)
        
        The finished good's cost = whatever the raw material cost.
        This is why changing Milk's price affects Milk Powder.
        """
        sles = []

        # consume raw material
        raw_queue = self.fifo_queues[txn.item]
        consumption_cost = raw_queue.remove(txn.qty)
        raw_outgoing_rate = round(consumption_cost / txn.qty, 2)

        sles.append(self._create_sle(
            txn=txn, item=txn.item,
            qty_change=-txn.qty, incoming_rate=0,
            outgoing_rate=raw_outgoing_rate, queue=raw_queue
        ))

        # produce finished good — its rate comes from the raw material cost
        produced_rate = round(consumption_cost / txn.produced_qty, 2)
        produced_queue = self.fifo_queues[txn.produced_item]
        produced_queue.add(txn.produced_qty, produced_rate)

        sles.append(self._create_sle(
            txn=txn, item=txn.produced_item,
            qty_change=txn.produced_qty, incoming_rate=produced_rate,
            outgoing_rate=0, queue=produced_queue
        ))

        return sles

    def _create_sle(self, txn, item, qty_change, incoming_rate,
                    outgoing_rate, queue) -> StockLedgerEntry:
        """Build a Stock Ledger Entry with a snapshot of the current queue."""
        self._sle_counter += 1
        return StockLedgerEntry(
            sle_id=f"SLE-{self._sle_counter:04d}",
            txn_id=txn.txn_id,
            txn_date=txn.txn_date,
            txn_type=txn.txn_type,
            item=item,
            qty_change=qty_change,
            incoming_rate=incoming_rate,
            outgoing_rate=outgoing_rate,
            qty_after_transaction=round(queue.total_qty(), 2),
            stock_value=round(queue.total_value(), 2),
            valuation_rate=queue.valuation_rate(),
            fifo_queue_state=queue.get_state()
        )

    # ── reposting ──
    #
    # When someone edits a past transaction,
    # we need to recalculate everything from that point forward.
    # ERPNext does this via the Repost Item Valuation doctype.
    # Here's a simplified version of that same idea.

    def edit_transaction(self, txn_id: str, new_rate: float) -> dict:
        """
        The main entry point for reposting.
        Change a transaction's rate and fix everything downstream.
        """
        txn = self._find_transaction(txn_id)
        if not txn:
            raise ValueError(f"Transaction '{txn_id}' not found")

        old_rate = txn.rate
        txn.rate = new_rate

        # figure out which items are affected (including manufactured ones)
        items_to_repost = self._find_affected_items(txn.item)

        report = self._repost(txn.item, txn.txn_date, items_to_repost)
        report["edited_transaction"] = txn_id
        report["old_rate"] = old_rate
        report["new_rate"] = new_rate
        return report

    def _find_transaction(self, txn_id: str) -> Optional[Transaction]:
        for txn in self.transactions:
            if txn.txn_id == txn_id:
                return txn
        return None

    def _find_affected_items(self, source_item: str) -> List[str]:
        """
        If Milk's price changes, Milk Powder is affected too
        (because it's manufactured from Milk). This finds all
        such downstream items via the BOM registry.
        """
        affected = [source_item]
        for bom in self.bom_registry:
            if bom.raw_item == source_item and bom.produced_item not in affected:
                affected.append(bom.produced_item)
        return affected

    def _repost(self, primary_item: str, from_date: date,
                items_to_repost: List[str]) -> dict:
        """
        The actual reposting algorithm:
        
        1. Save the old SLE values (so we can report what changed)
        2. Rewind each item's FIFO queue to the state before the edit
        3. Wipe all SLEs from the edit date onward
        4. Replay every transaction in order
        5. Compare old vs new and build a change report
        
        This is basically what ERPNext's repost_future_sle() does,
        minus the database and the GL entry updates.
        """
        report = {"changes": []}

        # 1. snapshot old values for comparison later
        old_sles = {
            sle.sle_id: {
                "item": sle.item, "txn_id": sle.txn_id,
                "incoming_rate": sle.incoming_rate,
                "outgoing_rate": sle.outgoing_rate,
                "stock_value": sle.stock_value,
                "valuation_rate": sle.valuation_rate,
            }
            for sle in self.sle_entries
            if sle.item in items_to_repost and sle.txn_date >= from_date
        }

        # 2. rewind — reset each item's queue to what it was before the edit
        for item in items_to_repost:
            queue_state = self._get_queue_state_before(item, from_date)
            self.fifo_queues[item] = FIFOQueue(queue_state)

        # 3. wipe the old SLEs — we're going to regenerate them
        self.sle_entries = [
            sle for sle in self.sle_entries
            if not (sle.item in items_to_repost and sle.txn_date >= from_date)
        ]

        # 4. replay everything in chronological order
        txns_to_replay = sorted(
            [t for t in self.transactions if t.txn_date >= from_date],
            key=lambda t: (t.txn_date, t.txn_id)
        )

        for txn in txns_to_replay:
            # skip transactions that aren't for affected items
            # (but don't skip a manufacture that produces an affected item)
            if txn.item not in items_to_repost:
                if not (txn.txn_type == TransactionType.MANUFACTURE
                        and txn.produced_item in items_to_repost):
                    continue

            new_sles = self._execute_transaction(txn)
            self.sle_entries.extend(new_sles)

        self.sle_entries.sort(key=lambda s: (s.txn_date, s.txn_id))

        # 5. figure out what actually changed
        new_sles_map = {
            sle.txn_id + "|" + sle.item: sle
            for sle in self.sle_entries
            if sle.item in items_to_repost and sle.txn_date >= from_date
        }

        for old_id, old in old_sles.items():
            key = old["txn_id"] + "|" + old["item"]
            if key in new_sles_map:
                new = new_sles_map[key]
                if (old["stock_value"] != new.stock_value
                        or old["outgoing_rate"] != new.outgoing_rate
                        or old["incoming_rate"] != new.incoming_rate):
                    report["changes"].append({
                        "txn_id": old["txn_id"],
                        "item": old["item"],
                        "old_stock_value": old["stock_value"],
                        "new_stock_value": new.stock_value,
                        "old_valuation_rate": old["valuation_rate"],
                        "new_valuation_rate": new.valuation_rate,
                        "old_incoming_rate": old["incoming_rate"],
                        "new_incoming_rate": new.incoming_rate,
                        "old_outgoing_rate": old["outgoing_rate"],
                        "new_outgoing_rate": new.outgoing_rate,
                    })

        return report

    def _get_queue_state_before(self, item: str, before_date: date) -> list:
        """
        Find the FIFO queue snapshot just before a given date.
        
        If there are no SLEs before that date (like when the first
        transaction IS the edited one), fall back to the opening stock.
        Without this, reposting would start with an empty queue and
        you'd lose the opening balance.
        """
        last_state = None
        for sle in self.sle_entries:
            if sle.item == item and sle.txn_date < before_date:
                last_state = sle.fifo_queue_state

        if last_state is None:
            return copy.deepcopy(self.opening_stock.get(item, []))

        return last_state

    # ── querying / debug helpers ──

    def get_sles_for_item(self, item: str) -> List[StockLedgerEntry]:
        return [sle for sle in self.sle_entries if sle.item == item]

    def get_current_stock(self, item: str) -> dict:
        queue = self.fifo_queues[item]
        return {
            "item": item,
            "qty": round(queue.total_qty(), 2),
            "value": round(queue.total_value(), 2),
            "valuation_rate": queue.valuation_rate(),
            "fifo_queue": queue.get_state()
        }

    def print_sles(self, item: Optional[str] = None) -> None:
        entries = self.sle_entries
        if item:
            entries = [s for s in entries if s.item == item]

        print(f"\n{'='*90}")
        print(f"{'Date':<12} {'TxnID':<10} {'Item':<15} {'Type':<20} "
              f"{'Qty':>6} {'Rate':>8} {'Value':>10} {'Balance':>8}")
        print(f"{'='*90}")

        for sle in entries:
            rate = sle.incoming_rate if sle.qty_change > 0 else sle.outgoing_rate
            print(
                f"{str(sle.txn_date):<12} {sle.txn_id:<10} {sle.item:<15} "
                f"{sle.txn_type.value:<20} {sle.qty_change:>6.1f} "
                f"{rate:>8.2f} {sle.stock_value:>10.2f} "
                f"{sle.qty_after_transaction:>8.1f}"
            )

        print(f"{'='*90}\n")
