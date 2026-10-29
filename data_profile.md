# Supplied dataset profile used for calibration

Source: `collections_30k_dataset (1).zip`, file `calls.csv`. The analyzer is reproducible with `profile_dataset.py` and reads the ZIP without extracting it.

| Measure | Result |
| --- | ---: |
| Raw call rows | 91,350 |
| Distinct `call_id` | 90,000 |
| Duplicate rows removed | 1,350 |
| Conflicting duplicate statuses | 0 |
| Deduplicated ANSWERED calls | 17,880 |
| Historical answer rate | 19.87% |
| Answered calls with positive duration | 17,866 |
| Mean answered duration | 453.3 sec |
| Median answered duration | 455 sec |
| Source time zone labels | UTC, Asia/Kolkata, Asia/Dubai |
| Event date range | 2025-12-29 through 2026-08-12 |

Deduplication keys on stable `call_id`, retaining the row with greatest `event_at`; equal/duplicate statuses were consistent in this snapshot. This is a summary calibration only. It is not split by campaign, contact cohort, or time of day, and the dataset README notes intentionally inconsistent timestamps, identifiers, campaign definitions, and late-arriving events. Do not use the aggregate rate as a live production prediction. In production, normalize timestamps, document tie-breaking rules, preserve raw events, calculate campaign-specific confidence bounds, and monitor drift.
