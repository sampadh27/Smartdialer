# SmartDialer: safety-first prototype

This runnable Python 3.10+ prototype demonstrates progressive dialing, predictive pacing behind an independent safety controller, concurrent allocation, idempotent provider events, two different mock providers, failure recovery, scenario simulation, and a basic allocation load test. It uses only the Python standard library.

## Run it

Open a terminal in this folder:

```powershell
python -m unittest -v
python simulate.py
python load_test.py
python profile_dataset.py "C:\Users\hp\Downloads\collections_30k_dataset (1).zip"
```

`simulate.py` writes `simulation_results.csv` beside the source. `load_test.py` writes `load_test_results.csv` for local in-memory reservation throughput at 100, 1,000, and 10,000 agents. These numbers are machine-specific microbenchmarks, not production capacity claims.

To use the library, create `Store`, add agents, construct a provider and `SmartDialer`, then call `progressive(borrower_id)`. `predictive_batch` accepts a list of borrower IDs and the observed `(answered, attempts)` history. Provider implementations are injected into `SmartDialer`; they never own allocator state.

## Design at a glance

```text
Campaign queue -> Progressive / PredictivePacer -> SafetyController -> SmartDialer allocator -> Provider interface
                                     (proposal only)       (hard cap, health gate)       (reservation first)
Provider event callback -> idempotent state projection -> Agent / Call state
```

The safety boundary is enforced by the `SmartDialer` public path: proposals are data, not provider calls, and every predictive batch is authorized before allocation. In this prototype each live outbound attempt consumes one distinct reserved agent slot until a definitive terminal provider event. That intentionally conservative capacity rule guarantees there cannot be more live answerable calls than assigned agent capacity. It also explains the throughput/utilization ceiling: risk-free blind over-dialing is impossible without an answer buffer, a carrier-side answer-control feature, or accepting a nonzero abandon risk.

The in-memory `Store` uses one reentrant lock to make agent and borrower reservation atomic. In production, use a transaction that conditionally updates an AVAILABLE agent (`UPDATE ... WHERE state='AVAILABLE' ... RETURNING`) and enforces a unique active call per agent and borrower; commit an outbox record in the same transaction. Provider initiation happens after commit with the stable internal `call_id` as idempotency key. A worker crash after reservation is recovered by reservation lease expiry. A crash after sending is ambiguous: keep the slot reserved and reconcile by provider call ID/idempotency key rather than blindly retrying.

Provider duplicate events are keyed by event ID. Call states are monotonic and terminal states cannot be resurrected by late events. An answer event atomically connects its already-reserved agent; if the reservation is gone, the call is failed and never bridged. On provider timeout, the prototype opens a circuit after repeated errors and retains the ambiguous call's capacity until reconciliation. This favors safety over utilization.

## Supplied dataset use

`profile_dataset.py` profiles `calls.csv` directly from the supplied ZIP. It deduplicates on stable `call_id`, selecting the latest `event_at` row. The supplied README warns of duplicate events, mixed time zones, inconsistent IDs, and late events; production use should normalize timestamps to UTC, preserve source timestamps, and validate call IDs before constructing a historical model. The assessed baseline is documented in `data_profile.md`.

## Scope and limits

This is a functional simulator and design reference, not a telecom dialer. The mock's immediate event delivery abstracts network timing; the scenario simulator separately models setup delay, no-answer/failure, talk time, changing conditions, and pacing. The process-local lock does not provide multi-process durability. Persist state, use database uniqueness/transactions and an outbox, add provider reconciliation webhooks, lease fencing tokens, per-campaign fair queues, rate limits, metrics, and integration tests before handling real borrowers or regulated traffic.

See [architecture.md](architecture.md) for tradeoffs and [diagrams.md](diagrams.md) for three GitHub-ready Mermaid blocks. `architecture.mmd`, `agent_state.mmd`, and `call_state.mmd` are individual raw Mermaid source files; render each separately.
