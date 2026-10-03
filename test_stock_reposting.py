"""
test_stock_reposting.py — Test Cases for Stock Reposting System

Tests cover:
1. Basic FIFO queue operations
2. The exact scenario from the task (Milk + Milk Powder)
3. Reposting after editing a past transaction
4. Cascade reposting (Milk changes -> Milk Powder recalculated)
5. Edge cases
"""

import pytest
from datetime import date
from models import Transaction, TransactionType
from fifo_queue import FIFOQueue
from stock_ledger import StockLedger


# ══════════════════════════════════════════════════
# SHARED HELPER — builds the scenario from the task
# (used by multiple test classes, so we keep it DRY)
# ══════════════════════════════════════════════════

def build_dairy_scenario() -> StockLedger:
    """
    Build the exact scenario from the task:
      Opening: 100L Milk @ 50/L
      10 Jan: Buy 50L @ 60
      15 Jan: Sell 80L
      20 Jan: Buy 50L @ 60
      21 Jan: Manufacture 100L Milk -> 10 KG Milk Powder
      25 Jan: Sell 10 KG Milk Powder

    Returns a fully processed StockLedger.
    """
    ledger = StockLedger()

    ledger.register_item("Milk", "L")
    ledger.register_item("Milk Powder", "KG")
    ledger.register_bom("Milk", 100, "Milk Powder", 10)
    ledger.set_opening_stock("Milk", 100, 50)

    transactions = [
        Transaction("PR-001", date(2025, 1, 10),
                    TransactionType.PURCHASE_RECEIPT, "Milk", 50, rate=60),
        Transaction("DN-001", date(2025, 1, 15),
                    TransactionType.DELIVERY_NOTE, "Milk", 80),
        Transaction("PR-002", date(2025, 1, 20),
                    TransactionType.PURCHASE_RECEIPT, "Milk", 50, rate=60),
        Transaction("MFG-001", date(2025, 1, 21),
                    TransactionType.MANUFACTURE, "Milk", 100,
                    produced_item="Milk Powder", produced_qty=10),
        Transaction("DN-002", date(2025, 1, 25),
                    TransactionType.DELIVERY_NOTE, "Milk Powder", 10),
    ]

    for txn in transactions:
        ledger.process_transaction(txn)

    return ledger


# ══════════════════════════════════════════════════
# TEST 1: FIFO Queue Basics
# ══════════════════════════════════════════════════

class TestFIFOQueue:
    """Test the FIFO queue in isolation."""

    def test_add_stock(self):
        """Adding stock creates buckets."""
        q = FIFOQueue()
        q.add(100, 50)
        assert q.total_qty() == 100
        assert q.total_value() == 5000

    def test_add_multiple_batches(self):
        """Multiple purchases create separate buckets."""
        q = FIFOQueue()
        q.add(100, 50)
        q.add(50, 60)
        assert q.total_qty() == 150
        assert q.total_value() == 8000  # 5000 + 3000

    def test_fifo_removal_single_bucket(self):
        """Removing from one bucket uses oldest first."""
        q = FIFOQueue()
        q.add(100, 50)
        q.add(50, 60)
        cost = q.remove(80)
        assert cost == 4000  # 80 x 50
        assert q.total_qty() == 70
        assert q.total_value() == 4000  # 20x50 + 50x60

    def test_fifo_removal_across_buckets(self):
        """Removing across multiple buckets uses FIFO order."""
        q = FIFOQueue()
        q.add(20, 50)
        q.add(50, 80)
        q.add(50, 60)
        cost = q.remove(100)
        # 20x50 + 50x80 + 30x60 = 1000 + 4000 + 1800 = 6800
        assert cost == 6800

    def test_remove_exact_qty(self):
        """Removing exact quantity empties the queue."""
        q = FIFOQueue()
        q.add(100, 50)
        cost = q.remove(100)
        assert cost == 5000
        assert q.total_qty() == 0

    def test_remove_more_than_available_raises_error(self):
        """Cannot remove more stock than available."""
        q = FIFOQueue()
        q.add(10, 50)
        with pytest.raises(ValueError, match="Cannot remove"):
            q.remove(20)

    def test_valuation_rate(self):
        """Weighted average valuation rate."""
        q = FIFOQueue()
        q.add(100, 50)
        q.add(50, 80)
        # Total value = 5000 + 4000 = 9000, qty = 150, rate = 60
        assert q.valuation_rate() == 60.0

    def test_get_state_returns_copy(self):
        """get_state returns a deep copy, not a reference."""
        q = FIFOQueue()
        q.add(100, 50)
        state = q.get_state()
        state[0][0] = 999  # Modify the copy
        assert q.total_qty() == 100  # Original unchanged


# ══════════════════════════════════════════════════
# TEST 2: Full Scenario — Before Edit
# ══════════════════════════════════════════════════

class TestFullScenarioBeforeEdit:
    """Test every step of the scenario BEFORE any edits."""

    @pytest.fixture
    def ledger(self):
        return build_dairy_scenario()

    def test_after_purchase_10_jan(self, ledger):
        """After 10 Jan purchase: 150L, value 8,000."""
        sle = ledger.get_sles_for_item("Milk")[0]
        assert sle.qty_after_transaction == 150
        assert sle.stock_value == 8000

    def test_after_delivery_15_jan(self, ledger):
        """After 15 Jan delivery: 70L, value 4,000. Used the 50 bucket."""
        sle = ledger.get_sles_for_item("Milk")[1]
        assert sle.qty_after_transaction == 70
        assert sle.stock_value == 4000
        assert sle.outgoing_rate == 50.0

    def test_after_purchase_20_jan(self, ledger):
        """After 20 Jan purchase: 120L, value 7,000."""
        sle = ledger.get_sles_for_item("Milk")[2]
        assert sle.qty_after_transaction == 120
        assert sle.stock_value == 7000

    def test_after_manufacture_21_jan_milk(self, ledger):
        """After manufacture: 20L Milk remaining, value 1,200."""
        sle = ledger.get_sles_for_item("Milk")[3]
        assert sle.qty_after_transaction == 20
        assert sle.stock_value == 1200

    def test_after_manufacture_21_jan_milk_powder(self, ledger):
        """After manufacture: 10 KG Milk Powder, value 5,800."""
        sle = ledger.get_sles_for_item("Milk Powder")[0]
        assert sle.qty_after_transaction == 10
        assert sle.stock_value == 5800
        assert sle.incoming_rate == 580

    def test_after_delivery_25_jan(self, ledger):
        """After selling Milk Powder: 0 KG remaining."""
        sle = ledger.get_sles_for_item("Milk Powder")[1]
        assert sle.qty_after_transaction == 0
        assert sle.outgoing_rate == 580.0

    def test_milk_consumption_cost_in_manufacture(self, ledger):
        """Manufacturing consumed 100L at blended FIFO rate of 58/L."""
        mfg_sle = ledger.get_sles_for_item("Milk")[3]
        assert mfg_sle.outgoing_rate == 58.0  # 5800 / 100


# ══════════════════════════════════════════════════
# TEST 3: Reposting After Edit
# ══════════════════════════════════════════════════

class TestReposting:
    """
    The main test — edit PR-001 rate from 60 to 80
    and verify everything downstream recalculates correctly.
    """

    @pytest.fixture
    def ledger_with_edit(self):
        ledger = build_dairy_scenario()
        ledger.edit_transaction("PR-001", new_rate=80)
        return ledger

    def test_repost_purchase_10_jan(self, ledger_with_edit):
        """After edit: 10 Jan purchase now at 80, total value = 9,000."""
        sle = ledger_with_edit.get_sles_for_item("Milk")[0]
        assert sle.incoming_rate == 80
        assert sle.stock_value == 9000

    def test_repost_delivery_15_jan(self, ledger_with_edit):
        """15 Jan delivery still uses the 50 bucket. Balance changes though."""
        sle = ledger_with_edit.get_sles_for_item("Milk")[1]
        assert sle.outgoing_rate == 50.0  # still from the old bucket
        assert sle.stock_value == 5000    # 20x50 + 50x80
        assert sle.qty_after_transaction == 70

    def test_repost_purchase_20_jan(self, ledger_with_edit):
        """20 Jan running balance = 8,000."""
        sle = ledger_with_edit.get_sles_for_item("Milk")[2]
        assert sle.stock_value == 8000
        assert sle.qty_after_transaction == 120

    def test_repost_manufacture_milk_cost(self, ledger_with_edit):
        """
        Manufacture now consumes at updated FIFO rates:
        20L @ 50 + 50L @ 80 + 30L @ 60 = 6,800 (was 5,800)
        """
        sle = ledger_with_edit.get_sles_for_item("Milk")[3]
        assert sle.outgoing_rate == 68.0  # 6800/100
        assert sle.stock_value == 1200
        assert sle.qty_after_transaction == 20

    def test_repost_milk_powder_production_cost(self, ledger_with_edit):
        """Milk Powder cost = 6,800 / 10 = 680/KG (was 580/KG)."""
        sle = ledger_with_edit.get_sles_for_item("Milk Powder")[0]
        assert sle.incoming_rate == 680
        assert sle.stock_value == 6800
        assert sle.qty_after_transaction == 10

    def test_repost_milk_powder_delivery(self, ledger_with_edit):
        """Selling Milk Powder at new cost 680/KG. COGS = 6,800."""
        sle = ledger_with_edit.get_sles_for_item("Milk Powder")[1]
        assert sle.outgoing_rate == 680.0
        assert sle.qty_after_transaction == 0

    def test_final_stock_positions(self, ledger_with_edit):
        """After everything, final positions should be consistent."""
        milk = ledger_with_edit.get_current_stock("Milk")
        assert milk["qty"] == 20
        assert milk["value"] == 1200

        mp = ledger_with_edit.get_current_stock("Milk Powder")
        assert mp["qty"] == 0
        assert mp["value"] == 0


# ══════════════════════════════════════════════════
# TEST 4: Edge Cases
# ══════════════════════════════════════════════════

class TestEdgeCases:
    """Test boundary conditions and error handling."""

    def test_edit_with_no_subsequent_transactions(self):
        """Editing the only transaction should still work fine."""
        ledger = StockLedger()
        ledger.register_item("Milk", "L")
        ledger.set_opening_stock("Milk", 100, 50)

        txn = Transaction("PR-001", date(2025, 1, 10),
                          TransactionType.PURCHASE_RECEIPT, "Milk", 50, rate=60)
        ledger.process_transaction(txn)

        ledger.edit_transaction("PR-001", new_rate=80)

        stock = ledger.get_current_stock("Milk")
        assert stock["qty"] == 150
        assert stock["value"] == 9000  # 100x50 + 50x80

    def test_edit_nonexistent_transaction_raises_error(self):
        """Should blow up if you try to edit something that doesn't exist."""
        ledger = StockLedger()
        ledger.register_item("Milk", "L")

        with pytest.raises(ValueError, match="not found"):
            ledger.edit_transaction("FAKE-001", new_rate=100)

    def test_fifo_queue_add_negative_qty_raises_error(self):
        """Can't add negative quantity to the queue."""
        q = FIFOQueue()
        with pytest.raises(ValueError, match="non-positive"):
            q.add(-10, 50)

    def test_empty_queue_removal_raises_error(self):
        """Can't remove from an empty queue."""
        q = FIFOQueue()
        with pytest.raises(ValueError, match="Cannot remove"):
            q.remove(10)

    def test_multiple_edits(self):
        """Editing the same transaction twice should still give correct results."""
        ledger = StockLedger()
        ledger.register_item("Milk", "L")
        ledger.set_opening_stock("Milk", 100, 50)

        txn = Transaction("PR-001", date(2025, 1, 10),
                          TransactionType.PURCHASE_RECEIPT, "Milk", 50, rate=60)
        ledger.process_transaction(txn)

        # First edit
        ledger.edit_transaction("PR-001", new_rate=80)
        assert ledger.get_current_stock("Milk")["value"] == 9000

        # Second edit
        ledger.edit_transaction("PR-001", new_rate=100)
        assert ledger.get_current_stock("Milk")["value"] == 10000

    def test_opening_stock_preserved_after_repost(self):
        """Reposting should never lose the opening stock."""
        ledger = StockLedger()
        ledger.register_item("Milk", "L")
        ledger.set_opening_stock("Milk", 100, 50)

        txn1 = Transaction("PR-001", date(2025, 1, 10),
                           TransactionType.PURCHASE_RECEIPT, "Milk", 50, rate=60)
        txn2 = Transaction("DN-001", date(2025, 1, 15),
                           TransactionType.DELIVERY_NOTE, "Milk", 30)
        ledger.process_transaction(txn1)
        ledger.process_transaction(txn2)

        ledger.edit_transaction("PR-001", new_rate=80)

        stock = ledger.get_current_stock("Milk")
        assert stock["qty"] == 120  # 100 + 50 - 30


# ══════════════════════════════════════════════════
# TEST 5: Integration / Demo Test
# ══════════════════════════════════════════════════

class TestDemo:
    """Full end-to-end test that also prints the before/after."""

    def test_full_scenario_with_print(self, capsys):
        """Run the complete scenario and verify the cascade works."""
        ledger = build_dairy_scenario()

        # Grab the "before" value
        mp_rate_before = ledger.get_sles_for_item("Milk Powder")[0].incoming_rate
        assert mp_rate_before == 580

        print("\nBEFORE EDIT:")
        ledger.print_sles()

        # Do the edit
        report = ledger.edit_transaction("PR-001", new_rate=80)

        # Grab the "after" value
        mp_rate_after = ledger.get_sles_for_item("Milk Powder")[0].incoming_rate
        assert mp_rate_after == 680

        print("AFTER EDIT:")
        ledger.print_sles()

        # The whole point: cascade worked
        assert mp_rate_before != mp_rate_after
        assert len(report["changes"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
