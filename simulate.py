"""Discrete-time scenario simulation; stdlib only. Run from this folder."""
from __future__ import annotations
import csv
from dataclasses import dataclass
from pathlib import Path
from random import Random


@dataclass(frozen=True)
class Scenario:
    name: str
    answer_rate: float
    talk_seconds: int
    setup_seconds: int
    failure_rate: float


SCENARIOS = [
    Scenario("A_low_answer", .20, 120, 1, .02),
    Scenario("B_balanced", .50, 90, 2, .02),
    Scenario("C_high_long", .70, 180, 3, .03),
    Scenario("D_changing", .50, 120, 2, .02),
    # Calibration scenario from deduplicated calls.csv in the supplied dataset.
    Scenario("E_dataset_baseline", .1987, 453, 2, .02),
]


def simulate(s: Scenario, mode: str, seconds: int = 900, agents: int = 30,
             seed: int = 42) -> dict[str, object]:
    rng = Random(seed)
    free_at = [0] * agents
    initiations = connected = failures = 0
    connected_agent_seconds = safety_reductions = 0
    empirical_answered = empirical_attempts = 0
    latency_total = 0
    # Keep provider-side outstanding attempts in the same hard one-slot-per-agent ledger.
    for now in range(seconds):
        current = s
        if s.name == "D_changing":
            # Sudden answer-rate and provider-health shift tests fast defensive pacing.
            current = Scenario(s.name, .60 if now < seconds//2 else .10,
                               120, 1 if now < seconds//2 else 4,
                               .02 if now < seconds//2 else .22)
        candidates = [i for i, until in enumerate(free_at) if until <= now]
        if not candidates:
            continue
        if mode == "progressive":
            batch = 1
        else:
            remaining = len(candidates)
            # Wilson lower bound from completed dial outcomes; prior is 20% until data accrues.
            p = wilson_lower(empirical_answered, empirical_attempts) if empirical_attempts else .20
            batch = min(remaining, max(1, int(remaining * min(1.0, p + .25))))
        # Never initiate more attempts than free agent slots, even when provider is lagging.
        batch = min(batch, len(candidates))
        if mode == "predictive-safe" and batch < min(remaining, len(candidates)):
            safety_reductions += 1
        for agent in candidates[:batch]:
            initiations += 1
            empirical_attempts += 1
            setup = current.setup_seconds + rng.randint(0, 2)
            latency_total += setup
            success = rng.random() >= current.failure_rate
            answered = success and rng.random() < current.answer_rate
            if answered:
                connected += 1
                empirical_answered += 1
                # setup+routing is occupied; connected talk plus fixed 8s wrap-up.
                held = setup + current.talk_seconds + 8
                # Credit only time that falls inside the simulated observation window.
                connected_agent_seconds += min(current.talk_seconds, seconds - now)
            else:
                failures += 1
                held = setup
            free_at[agent] = now + max(1, held)
    return {
        "scenario": s.name, "mode": mode, "agents": agents, "duration_sec": seconds,
        "answer_rate_assumption": s.answer_rate, "talk_seconds_assumption": s.talk_seconds,
        "setup_seconds_base": s.setup_seconds, "failure_rate_assumption": s.failure_rate,
        "initiated": initiations, "connected": connected, "provider_fail_or_no_answer": failures,
        "realized_answer_rate": round(connected / initiations, 4) if initiations else 0,
        "utilization_pct": round(100 * connected_agent_seconds / (agents * seconds), 2),
        "avg_setup_latency_sec": round(latency_total / initiations, 2) if initiations else 0,
        "safety_pacing_reductions": safety_reductions,
        "abandoned_connected": 0,
    }


def wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    den = 1 + z*z/total
    return max(0.0, (p + z*z/(2*total) - z*((p*(1-p)/total + z*z/(4*total*total))**.5))/den)


def main() -> None:
    rows = []
    for scenario in SCENARIOS:
        for mode in ("progressive", "predictive-safe"):
            rows.append(simulate(scenario, mode))
    out = Path("simulation_results.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    print(" | ".join(rows[0].keys()))
    for row in rows:
        print(" | ".join(str(v) for v in row.values()))
    print(f"Wrote {out.resolve()}")


if __name__ == "__main__":
    main()
