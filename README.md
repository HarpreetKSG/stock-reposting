# Stock Reposting & FIFO Valuation

This project demonstrates how stock reposting works in an ERP system like ERPNext. The idea is simple — when someone goes back and edits a past purchase price, every transaction after it needs to be recalculated. This gets tricky with FIFO valuation, and even trickier when manufactured items are involved.

I built this as a clean Python simulation. No database, no web framework — just the core logic you can read, run, and test.

---

## The Problem

We have a dairy business with two items: **Milk** and **Milk Powder** (made from Milk). Both use FIFO valuation.

Here's the timeline:

| Date | What happened |
|------|---------------|
| Opening | Had 100L Milk @ ₹50/L |
| 10 Jan | Bought 50L Milk @ ₹60/L |
| 15 Jan | Sold 80L Milk |
| 20 Jan | Bought 50L Milk @ ₹60/L |
| 21 Jan | Converted 100L Milk into 10 KG Milk Powder |
| 25 Jan | Sold 10 KG Milk Powder |

Then the user edits the 10 Jan purchase — changes the rate from **₹60 to ₹80**. Now what?

---

## What gets affected?

Everything after 10 Jan needs recalculation. And since Milk Powder is made from Milk, it gets hit too:

**Milk (direct):**
- 15 Jan sale — running balance changes
- 20 Jan purchase — running balance changes  
- 21 Jan manufacture — the cost of consuming 100L changes

**Milk Powder (cascade):**
- 21 Jan manufacture — production cost jumps from ₹580/KG to ₹680/KG
- 25 Jan sale — COGS goes from ₹5,800 to ₹6,800

One price change on Milk rippled through to a completely different item.

---

## How the recalculation works

The system replays every transaction from 10 Jan onward, rebuilding the FIFO queue from scratch:

| What | Before edit | After edit |
|------|-------------|------------|
| 10 Jan PR value | ₹3,000 | ₹4,000 |
| 15 Jan COGS | ₹4,000 | ₹4,000 (same — it used the ₹50 bucket) |
| 15 Jan balance | ₹4,000 | ₹5,000 |
| 21 Jan manufacturing cost | ₹5,800 | ₹6,800 |
| Milk Powder per-KG rate | ₹580 | ₹680 |
| 25 Jan Milk Powder COGS | ₹5,800 | ₹6,800 |

---

## My reposting approach

Here's the algorithm step by step:

1. **Find** the edited transaction
2. **Snapshot** the FIFO queue state just before that date (the last known good state)
3. **Clear** all Stock Ledger Entries from that date onward for affected items
4. **Reset** each item's FIFO queue to the snapshot
5. **Replay** every transaction in chronological order, generating fresh SLEs
6. **Cascade** — if any replayed transaction manufactures a different item, that item gets reposted too
7. **Report** what values changed (old vs new)

The key design decision: I don't try to "patch" individual entries. I wipe everything from the edit point and replay from scratch. It's simpler, less error-prone, and matches how ERPNext actually handles this internally.

---

## Problems with running balances

ERPNext stores a running snapshot in every Stock Ledger Entry — quantity balance, total value, valuation rate. This is fast for reads but painful for edits:

- **Stale data everywhere** — change one old entry and hundreds of later entries have wrong balances
- **Performance** — reposting 10,000 entries is slow, especially if you hit the database for each one
- **Race conditions** — someone might create a new transaction while reposting is in progress, using stale values
- **Locking** — you might need to freeze the item during repost, blocking other users
- **Cascading cost** — Milk's repost triggers Milk Powder's repost, which could trigger more items
- **Crash risk** — if reposting fails midway, you're left with a mix of old and new values

---

## Project structure

```
models.py              Data classes (Item, Transaction, SLE, BOM)
fifo_queue.py          FIFO queue — add/remove with cost tracking
stock_ledger.py        Main engine — process, repost, cascade
test_stock_reposting.py   29 tests covering every scenario
demo.py                Visual walkthrough you can run directly
```

A few things I paid attention to:

- **DRY** — Transaction dispatch logic lives in one place (`_execute_transaction`), used by both first-time processing and reposting replay. The test scenario setup is also shared via a helper function.
- **Separation of concerns** — FIFO queue doesn't know about transactions. The ledger doesn't know how FIFO works internally. Each piece does one job.
- **Snapshots for debugging** — every SLE stores a copy of the FIFO queue state at that point, so you can trace exactly what happened.

---

## How to run

You need Python 3.8+ and pytest.

```bash
# Install pytest
pip install pytest

# Run all 29 tests
pytest test_stock_reposting.py -v

# Run the visual demo (shows before/after tables)
python demo.py

# Run tests with printed output (so you can see the tables)
pytest test_stock_reposting.py -v -s
```

---

## What the tests cover

| Test group | Count | What it checks |
|-----------|-------|----------------|
| FIFO Queue | 8 | Add, remove, FIFO ordering, edge cases |
| Before Edit | 7 | Every transaction's values in the original scenario |
| After Edit (Reposting) | 7 | Every value after the rate change, including cascade |
| Edge Cases | 5 | Multiple edits, missing transactions, error handling |
| Integration | 1 | Full end-to-end with printed comparison |
| **Total** | **29** | |
