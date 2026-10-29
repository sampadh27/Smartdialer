"""Small local allocation-throughput check; not a production benchmark."""
from __future__ import annotations
import time
import csv
from smartdialer import Store


def run(count: int) -> dict[str, float | int]:
    store = Store()
    for i in range(count):
        store.add_agent(f"A{i}")
    started = time.perf_counter()
    for i in range(count):
        store.reserve(f"B{i}")
    elapsed = time.perf_counter() - started
    return {"agents": count, "reservations": count, "elapsed_sec": round(elapsed, 6),
            "reservations_per_sec": round(count / elapsed, 1) if elapsed else 0}


if __name__ == "__main__":
    rows = [run(size) for size in (100, 1000, 10000)]
    for row in rows:
        print(row)
    with open("load_test_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
