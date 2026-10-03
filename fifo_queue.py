"""
fifo_queue.py

The FIFO queue. This is where the actual valuation math happens.

It keeps a list of "buckets" — each one is a batch of stock at some price.
When you consume stock, you always eat from the oldest bucket first.
That's literally all FIFO is.

In ERPNext, this lives inside stock_value_difference calculation
in stock_ledger.py / utils.py. I've pulled it out as its own class
so it's easier to test and reason about.
"""

import copy
from typing import List


class FIFOQueue:
    """
    Maintains a list of [qty, rate] pairs.
    Oldest bucket is at index 0. New stock goes to the end.
    """

    def __init__(self, initial_state: list = None):
        self.queue: List[List[float]] = []
        if initial_state:
            self.queue = copy.deepcopy(initial_state)

    def add(self, qty: float, rate: float) -> None:
        """New stock goes to the back of the line."""
        if qty <= 0:
            raise ValueError(f"Cannot add non-positive quantity: {qty}")
        if rate < 0:
            raise ValueError(f"Rate cannot be negative: {rate}")

        self.queue.append([qty, rate])

    def remove(self, qty: float) -> float:
        """
        Consume stock starting from the oldest bucket.
        Returns the total cost of what was consumed.
        
        This is the core FIFO logic — oldest first.
        """
        if qty <= 0:
            raise ValueError(f"Cannot remove non-positive quantity: {qty}")

        available = self.total_qty()
        if qty > available + 0.0001:  # tiny tolerance for floating point weirdness
            raise ValueError(
                f"Cannot remove {qty} units. Only {available} available."
            )

        total_cost = 0.0
        remaining = qty

        while remaining > 0.0001 and self.queue:
            bucket = self.queue[0]
            bucket_qty, bucket_rate = bucket[0], bucket[1]

            if bucket_qty <= remaining + 0.0001:
                # this bucket is fully consumed, pop it off
                total_cost += bucket_qty * bucket_rate
                remaining -= bucket_qty
                self.queue.pop(0)
            else:
                # partially consume this bucket
                total_cost += remaining * bucket_rate
                bucket[0] -= remaining
                remaining = 0

        return round(total_cost, 2)

    def total_qty(self) -> float:
        return sum(b[0] for b in self.queue)

    def total_value(self) -> float:
        return sum(b[0] * b[1] for b in self.queue)

    def valuation_rate(self) -> float:
        """Weighted average — total value / total qty."""
        qty = self.total_qty()
        if qty == 0:
            return 0.0
        return round(self.total_value() / qty, 2)

    def get_state(self) -> List[List[float]]:
        """Returns a deep copy so callers can't accidentally mess with our queue."""
        return copy.deepcopy(self.queue)

    def __repr__(self) -> str:
        buckets = ", ".join(f"{q}@₹{r}" for q, r in self.queue)
        return f"FIFOQueue([{buckets}])"
