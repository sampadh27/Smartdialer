"""Reproduce the call baseline from the supplied ZIP without extracting it."""
from __future__ import annotations
import argparse, csv, io, statistics, zipfile


def profile(path: str) -> dict[str, object]:
    # calls.csv contains 1,350 duplicate rows. Stable call_id plus latest event_at is
    # the documented dedup rule; in this snapshot duplicate statuses agree.
    with zipfile.ZipFile(path) as zf:
        reader = csv.DictReader(io.TextIOWrapper(zf.open("calls.csv"), encoding="utf-8"))
        calls = {}
        conflicting_duplicates = 0
        row_count = 0
        for row in reader:
            row_count += 1
            key = row["call_id"]
            if key in calls and calls[key]["call_status"] != row["call_status"]:
                conflicting_duplicates += 1
            if key not in calls or row["event_at"] > calls[key]["event_at"]:
                calls[key] = row
    answered = [r for r in calls.values() if r["call_status"].upper() == "ANSWERED"]
    durations = [float(r["duration_sec"]) for r in answered
                 if r["duration_sec"] and float(r["duration_sec"]) > 0]
    return {"rows": row_count, "unique_call_ids": len(calls),
            "duplicate_rows_removed": row_count - len(calls),
            "conflicting_duplicate_statuses": conflicting_duplicates,
            "answered_calls": len(answered),
            "answer_rate": len(answered) / len(calls) if calls else 0,
            "answered_duration_n": len(durations),
            "answered_duration_mean_sec": statistics.mean(durations) if durations else 0,
            "answered_duration_median_sec": statistics.median(durations) if durations else 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", help="Path to collections_30k_dataset ZIP")
    print(profile(parser.parse_args().zip_path))
