# Architecture decision and assessment response

## Decision

Build a safety-first, single-process reference with explicit interfaces and a transactional state store abstraction. Start in progressive mode by default; allow predictive pacing only to propose a bounded batch. A separately constructed `SafetyController` gates both progressive and predictive calls before provider dispatch. Each live outbound call holds one distinct agent reservation until a terminal outcome is confirmed.

This policy leaves some utilization benefit on the table compared with statistical multi-dialing. That is deliberate: if 10 calls can answer while only 8 agents can take them, a statistical answer-rate estimate cannot guarantee zero abandoned connects. To promise deterministic no-overflow, every answerable call needs a distinct reserved agent slot, or the provider must offer a guaranteed answer-control mechanism. The prototype demonstrates the safe point on that tradeoff curve; the pace estimate smooths batch starts and accounts for uncertainty, while the safety boundary preserves the hard capacity invariant.

## Why this stack

Python and the standard library keep the prototype locally runnable, auditable, and focused on the allocation/state transitions. One process lock makes the reference behavior easy to understand. PostgreSQL is the natural first durable store because conditional updates, transactions, unique indexes, and an outbox can make allocation atomic without introducing a broker or cache. The process-local implementation is not a multi-worker production store.

## Allocation and concurrency

The reservation transaction checks borrower uniqueness, claims one AVAILABLE agent, creates one RESERVED call, and records an initiation outbox item. A competing worker loses the conditional claim and cannot reserve the same agent. Production constraints should include unique active reservation per agent, unique active borrower call, and a campaign idempotency key. Workers claim outbox rows with short leases and use the internal call ID as the provider idempotency key. A cache may accelerate reads, but the database transaction and constraints decide who owns the slot.

The demo uses an `RLock`; under true multi-process load, the database transaction replaces it. A user-facing reservation lease carries a fencing/version token so a delayed worker cannot act after a newer owner. Never hold the database transaction open during a provider request.

## State machines

Agent: `OFFLINE -> AVAILABLE -> RESERVED -> DIALING -> CONNECTED -> WRAP_UP -> AVAILABLE`. `AVAILABLE -> PAUSED/OFFLINE` is allowed only when no call is attached. A drop from an active state triggers a safe provider cancel/reconciliation and does not silently free the capacity until it is known that no answer can still arrive. The prototype models OFFLINE, AVAILABLE, RESERVED, DIALING, CONNECTED, WRAP_UP, and PAUSED; wrap-up durations are in the simulator.

Call: `QUEUED -> RESERVED -> INITIATED -> RINGING -> ANSWERED -> CONNECTED -> COMPLETED`, with `FAILED` and `CANCELLED` terminal edges from nonterminal states. Provider-specific statuses are normalized at the adapter. Stable event IDs make duplicate delivery a no-op. Versioned monotonic transitions reject stale events; terminal states cannot return to CONNECTED. An event that skips ahead is accepted only if it doesn't regress state. Event, call, and agent projection updates belong in one transaction.

## Failure behavior

| Failure | Behavior |
| --- | --- |
| Worker dies before provider request | Reservation lease expires; transaction releases agent and borrower and marks the call cancelled. |
| Worker dies after provider request | Call remains reserved; reconcile with provider by idempotency key/webhook. Do not retry an ambiguous originate. |
| Provider timeout | Retain the reserved seat because the provider may have originated the call; count health failures, open circuit, reconcile. |
| Provider outage | Reject new predictive and progressive starts when unhealthy/circuit-open; existing calls continue to receive events/reconciliation. Retry only definitive transient failures with bounded backoff and same idempotency key. |
| Agent disappears while queued | Atomic reservation sees unavailable state and skips it. |
| Agent disappears while dialing | Cancel at provider and retain capacity until cancel/terminal confirmation; do not route a late answer to another borrower. |
| Duplicate / out-of-order event | Deduplicate by provider event ID and monotonic projection; terminal states are absorbing. |
| Database/cache conflict | Database row and unique constraints win. Cache is invalidated/rebuilt from committed version. |

The safety controller reduces starts to current capacity and gates on provider health. The allocator rechecks availability atomically, so a stale pace snapshot cannot overbook. With 100 agents and 40 going away, the next transaction immediately observes only 60 reservable agents; already reserved agents are governed by the provider cancellation/reconciliation path.

## Predictive pacing

The proposal uses a Wilson lower confidence bound for observed answer rate to avoid overreacting to small samples. It estimates a batch from known free slots and keeps a configurable safety buffer. The safety controller then independently clamps it to `total_agent_slots - in_flight_slots`, or rejects it on unhealthy provider state. Provider latency and failure rate are inputs to the simulation; in a production pacer they should also reduce the allowed batch and raise a circuit breaker. The model does not own provider credentials or a provider call method, and the only public dialer method applies safety before allocation.

Why did the dialer request N? The audit row records requested batch, remaining slots, confidence estimate, safety cap, and decision. The included scenario output makes these decisions inspectable. Any unbounded over-dial request is reduced or rejected at the safety boundary.

## Simulation and supplied data

`simulate.py` runs the requested 20%, 50%, and 70% answer-rate examples with 120s, 90s, and 180s talk time plus setup latency/failures, a changing regime that drops answer rate and provider health midway, and an extra dataset-calibrated case (19.87%, 453s mean answered duration). It compares one-at-a-time progressive starts with bounded predictive batches; metrics are calls initiated, connected, realized answer rate, connected-agent utilization, average setup delay, pacing reductions, and abandoned connects. The simulation is an illustrative discrete-time model, not a forecast; its assumptions are exposed in the result CSV.

The provided synthetic collections dataset offers a rough calibration check: `calls.csv` has 91,350 rows and 90,000 distinct call IDs; after deduplicating by call ID to latest event time, 17,880 are ANSWERED (19.87%), with a 453s mean answered duration (17,866 nonmissing positive values). This is close to Scenario A's 20% rate. The generated data has 1,350 duplicate rows, mixed UTC/Asia/Kolkata/Asia/Dubai labels, and records through 2026-08-12. Therefore the profile is a sanity check, not reliable campaign-specific production calibration. See `profile_dataset.py` for the reproducible calculation.

## Scale: what breaks first

- **100 agents:** a single relational database, one worker, and simple leases are adequate. Instrument provider latency, failure, reservation age, and idle time.
- **1,000 agents:** hot campaign queues and agent rows become contentious. Partition work by campaign/shard, use `SKIP LOCKED` or atomic claim queries, batch candidate reads, and keep per-agent updates narrow. Outbox dispatch and event ingestion should be horizontally scalable.
- **10,000+ agents:** provider rate limits, event bursts, campaign fairness, database write contention, and stale presence dominate before CPU. Partition the outbox/event log by campaign or call ID; use regional queues and provider-specific rate limiters; shard agent pools by campaign/team; keep a durable canonical event log and rebuildable projections. Add backpressure and per-campaign fairness so a hot campaign cannot starve others.

Do not introduce a broker or cache until measurement shows the database outbox/queue is the bottleneck. If a broker is added, events remain at-least-once and consumers remain idempotent. For provider events, retain raw payload and timestamps, normalize to UTC, validate signatures, and reconcile periodic snapshots.

## What I would improve next

Persist the store in PostgreSQL, make outbox claiming durable, add provider cancel/status lookup, reconcile ambiguous calls, enforce per-campaign idempotency, and test provider callbacks with fault injection. Calibrate by campaign, time-of-day, contact cohort, and provider using rolling confidence intervals. Add a strict provider-side maximum-connect policy if available. Before real dialing, require compliance review, consent/suppression enforcement, recording policies, and operational alerting.

## Final question

I would keep a deterministic safety controller as the only route from pacing to the telecom layer. The pacing model can use forecast answer rates and agent release times to recommend when and how many calls to prepare; the controller checks live reserved and connected capacity, provider health, campaign limits, and an explicit risk budget before any originate. To preserve a zero-abandon guarantee, reserve one agent slot per answerable attempt or use provider-side answer control with a proven bound. If the required predictive headroom exceeds the available safe slots, the controller falls back to progressive starts or stops. That delivers the utilization gain available from better scheduling and timing while keeping the safety invariant independent of model accuracy.
