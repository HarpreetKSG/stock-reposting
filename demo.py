"""
demo.py — Run the full Stock Reposting scenario

This script demonstrates:
1. Processing the original transactions
2. Showing the stock state at each step
3. Editing a past transaction
4. Showing how reposting changes everything downstream

Run this file directly: python demo.py
"""

from datetime import date
from models import Transaction, TransactionType
from stock_ledger import StockLedger


def main():
    print("=" * 70)
    print("  STOCK REPOSTING & FIFO VALUATION DEMO")
    print("=" * 70)

    # ── Setup ──
    ledger = StockLedger()
    ledger.register_item("Milk", "L")
    ledger.register_item("Milk Powder", "KG")
    ledger.register_bom("Milk", 100, "Milk Powder", 10)

    # Opening stock
    ledger.set_opening_stock("Milk", 100, 50)
    print("\n✓ Opening Stock: 100L Milk @ ₹50/L (Value: ₹5,000)")

    # ── Process Transactions ──
    print("\n── Processing Transactions ──\n")

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

    descriptions = [
        "10 Jan: Purchase Receipt — 50L Milk @ ₹60/L",
        "15 Jan: Delivery Note — Sell 80L Milk",
        "20 Jan: Purchase Receipt — 50L Milk @ ₹60/L",
        "21 Jan: Manufacture — 100L Milk → 10 KG Milk Powder",
        "25 Jan: Delivery Note — Sell 10 KG Milk Powder",
    ]

    for txn, desc in zip(transactions, descriptions):
        sles = ledger.process_transaction(txn)
        print(f"  ✓ {desc}")
        for sle in sles:
            rate = sle.incoming_rate if sle.qty_change > 0 else sle.outgoing_rate
            direction = "IN" if sle.qty_change > 0 else "OUT"
            print(f"    └─ {sle.item}: {direction} {abs(sle.qty_change)} "
                  f"@ ₹{rate:.2f} | Balance: {sle.qty_after_transaction} "
                  f"| Value: ₹{sle.stock_value:,.2f}")

    # ── Show State BEFORE Edit ──
    print("\n" + "=" * 70)
    print("  STOCK LEDGER — BEFORE EDIT")
    ledger.print_sles()

    print("Current Stock Positions:")
    for item_name in ["Milk", "Milk Powder"]:
        stock = ledger.get_current_stock(item_name)
        print(f"  {item_name}: {stock['qty']} units, "
              f"Value: ₹{stock['value']:,.2f}, "
              f"Rate: ₹{stock['valuation_rate']:.2f}")
        if stock['fifo_queue']:
            print(f"    FIFO Queue: {stock['fifo_queue']}")

    # ── Save pre-edit values for comparison ──
    pre_edit_milk_sles = [
        (sle.txn_id, sle.stock_value, sle.outgoing_rate, sle.incoming_rate)
        for sle in ledger.get_sles_for_item("Milk")
    ]
    pre_edit_mp_sles = [
        (sle.txn_id, sle.stock_value, sle.outgoing_rate, sle.incoming_rate)
        for sle in ledger.get_sles_for_item("Milk Powder")
    ]

    # ── THE EDIT ──
    print("\n" + "=" * 70)
    print("  EDITING PR-001: Changing rate from ₹60/L → ₹80/L")
    print("=" * 70)

    report = ledger.edit_transaction("PR-001", new_rate=80)

    # ── Show State AFTER Edit ──
    print("\n  STOCK LEDGER — AFTER EDIT (REPOSTED)")
    ledger.print_sles()

    # ── Show Changes ──
    print("CHANGES DETECTED BY REPOSTING:")
    print("-" * 70)

    if report["changes"]:
        for change in report["changes"]:
            print(f"\n  Transaction: {change['txn_id']} ({change['item']})")

            if change['old_incoming_rate'] != change['new_incoming_rate']:
                print(f"    Incoming Rate: ₹{change['old_incoming_rate']:.2f} "
                      f"→ ₹{change['new_incoming_rate']:.2f}")

            if change['old_outgoing_rate'] != change['new_outgoing_rate']:
                print(f"    Outgoing Rate: ₹{change['old_outgoing_rate']:.2f} "
                      f"→ ₹{change['new_outgoing_rate']:.2f}")

            if change['old_stock_value'] != change['new_stock_value']:
                diff = change['new_stock_value'] - change['old_stock_value']
                sign = "+" if diff > 0 else ""
                print(f"    Stock Value:   ₹{change['old_stock_value']:,.2f} "
                      f"→ ₹{change['new_stock_value']:,.2f} "
                      f"({sign}₹{diff:,.2f})")

    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"""
    Edited: PR-001 (10 Jan Purchase Receipt)
    Change: Rate ₹60/L → ₹80/L

    Impact on MILK:
      - 10 Jan PR value:   ₹3,000 → ₹4,000  (+₹1,000)
      - 15 Jan DN COGS:    ₹4,000 → ₹4,000  (unchanged — used ₹50 bucket)
      - 15 Jan balance:    ₹4,000 → ₹5,000  (+₹1,000)
      - 20 Jan balance:    ₹7,000 → ₹8,000  (+₹1,000)
      - 21 Jan MFG cost:   ₹5,800 → ₹6,800  (+₹1,000)

    Impact on MILK POWDER (cascade):
      - Production cost:   ₹580/KG → ₹680/KG  (+₹100/KG)
      - 25 Jan COGS:       ₹5,800 → ₹6,800  (+₹1,000)

    Key insight: A ₹20/L change on 50L of Milk (₹1,000 total) 
    cascaded through to Milk Powder, affecting the COGS of 
    a completely different item!
    """)


if __name__ == "__main__":
    main()
