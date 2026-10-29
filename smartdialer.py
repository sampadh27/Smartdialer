"""Small, deterministic SmartDialer reference implementation (stdlib only).

The in-memory store is a teaching adapter. Production persistence must provide the
same compare-and-set and uniqueness guarantees in one database transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import floor
from random import Random
from threading import RLock
from time import monotonic
from typing import Callable, Iterable
import uuid


class AgentState(str, Enum):
    OFFLINE = "OFFLINE"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    DIALING = "DIALING"
    CONNECTED = "CONNECTED"
    WRAP_UP = "WRAP_UP"
    PAUSED = "PAUSED"


class CallState(str, Enum):
    QUEUED = "QUEUED"
    RESERVED = "RESERVED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    CONNECTED = "CONNECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {CallState.COMPLETED, CallState.FAILED, CallState.CANCELLED}
CALL_ORDER = {s: i for i, s in enumerate((CallState.QUEUED, CallState.RESERVED,
    CallState.INITIATED, CallState.RINGING, CallState.ANSWERED,
    CallState.CONNECTED, CallState.COMPLETED))}


@dataclass
class Agent:
    id: str
    state: AgentState = AgentState.AVAILABLE
    current_call: str | None = None
    version: int = 0
    updated_at: float = field(default_factory=monotonic)


@dataclass
class Call:
    id: str
    borrower_id: str
    agent_id: str
    state: CallState = CallState.QUEUED
    provider_id: str | None = None
    event_ids: set[str] = field(default_factory=set)
    version: int = 0
    created_at: float = field(default_factory=monotonic)
    error: str | None = None


@dataclass(frozen=True)
class Reservation:
    call_id: str
    agent_id: str
    borrower_id: str


class AllocationError(RuntimeError):
    pass


class Store:
    """Atomic in-process reference for unique agent/borrower active reservations."""
    def __init__(self) -> None:
        self.lock = RLock()
        self.agents: dict[str, Agent] = {}
        self.calls: dict[str, Call] = {}
        self.active_borrowers: dict[str, str] = {}
        self.outbox: list[tuple[str, str]] = []

    def add_agent(self, agent_id: str, state: AgentState = AgentState.AVAILABLE) -> None:
        with self.lock:
            self.agents[agent_id] = Agent(agent_id, state)

    def reserve(self, borrower_id: str, agent_id: str | None = None) -> Reservation:
        """One critical section models a DB transaction with row locks/CAS/indexes."""
        with self.lock:
            if borrower_id in self.active_borrowers:
                raise AllocationError("borrower already has an active call")
            candidates = ([self.agents[agent_id]] if agent_id else
                          [a for a in self.agents.values() if a.state == AgentState.AVAILABLE])
            agent = next((a for a in candidates if a.state == AgentState.AVAILABLE), None)
            if agent is None:
                raise AllocationError("no available agent")
            call_id = str(uuid.uuid4())
            call = Call(call_id, borrower_id, agent.id, CallState.RESERVED)
            # All rows change atomically; outbox event is committed with reservation.
            agent.state, agent.current_call = AgentState.RESERVED, call_id
            agent.version += 1
            call.version += 1
            self.calls[call_id] = call
            self.active_borrowers[borrower_id] = call_id
            self.outbox.append(("INITIATE_CALL", call_id))
            return Reservation(call_id, agent.id, borrower_id)

    def claim_initiation(self, call_id: str) -> bool:
        with self.lock:
            call = self.calls[call_id]
            if call.state != CallState.RESERVED:
                return False
            call.state = CallState.INITIATED
            call.version += 1
            agent = self.agents[call.agent_id]
            agent.state = AgentState.DIALING
            agent.version += 1
            return True

    def apply_event(self, call_id: str, event_id: str, status: str,
                    provider_call_id: str | None = None) -> bool:
        """Idempotent, monotonic event projection; late events cannot resurrect calls."""
        try:
            target = CallState(status.upper())
        except ValueError:
            return False
        with self.lock:
            call = self.calls.get(call_id)
            if not call or event_id in call.event_ids:
                return False
            call.event_ids.add(event_id)
            if provider_call_id and call.provider_id not in (None, provider_call_id):
                return False
            if provider_call_id:
                call.provider_id = provider_call_id
            if call.state in TERMINAL:
                return False
            # Events may skip intermediate states but may not regress them.
            if target in CALL_ORDER and call.state in CALL_ORDER and CALL_ORDER[target] < CALL_ORDER[call.state]:
                return False
            if target not in CALL_ORDER and target not in TERMINAL:
                return False
            call.state = target
            call.version += 1
            agent = self.agents[call.agent_id]
            if target in {CallState.ANSWERED, CallState.CONNECTED}:
                # Same transaction that records answer reserves the exact agent slot.
                if agent.current_call != call_id:
                    call.state = CallState.FAILED
                    call.error = "agent reservation lost before answer"
                    self._release(call, agent)
                    return False
                agent.state = AgentState.CONNECTED
                call.state = CallState.CONNECTED
                call.version += 1
                agent.version += 1
            elif target in TERMINAL:
                if target == CallState.COMPLETED:
                    # Keep the agent unavailable for after-call work, but free the borrower.
                    agent.state = AgentState.WRAP_UP
                    agent.current_call = call_id
                    agent.version += 1
                    if self.active_borrowers.get(call.borrower_id) == call_id:
                        self.active_borrowers.pop(call.borrower_id, None)
                else:
                    self._release(call, agent)
            return True

    def finish_wrap_up(self, agent_id: str, call_id: str | None = None) -> bool:
        with self.lock:
            agent = self.agents.get(agent_id)
            if not agent or agent.state != AgentState.WRAP_UP:
                return False
            if call_id is not None and agent.current_call != call_id:
                return False
            agent.state = AgentState.AVAILABLE
            agent.current_call = None
            agent.version += 1
            return True

    def _release(self, call: Call, agent: Agent) -> None:
        if agent.current_call == call.id:
            agent.current_call = None
            agent.state = AgentState.AVAILABLE
            agent.version += 1
        if self.active_borrowers.get(call.borrower_id) == call.id:
            self.active_borrowers.pop(call.borrower_id, None)

    def fail_or_cancel(self, call_id: str, state: CallState, reason: str) -> None:
        with self.lock:
            call = self.calls[call_id]
            if call.state in TERMINAL:
                return
            call.state, call.error = state, reason
            call.version += 1
            self._release(call, self.agents[call.agent_id])

    def expire_reservations(self, older_than_seconds: float) -> int:
        """Lease recovery after worker crash. Provider idempotency key is call.id."""
        cutoff = monotonic() - older_than_seconds
        expired = 0
        with self.lock:
            for call in self.calls.values():
                if call.state == CallState.RESERVED and call.created_at <= cutoff:
                    call.state, call.error = CallState.CANCELLED, "reservation lease expired"
                    call.version += 1
                    self._release(call, self.agents[call.agent_id])
                    expired += 1
        return expired


@dataclass(frozen=True)
class PaceProposal:
    requested: int
    reason: str
    probability: float


class PredictivePacer:
    """Conservative Wilson lower-bound pacing proposal; never places calls."""
    def propose(self, available_agents: int, in_flight_slots: int,
                answered: int, attempts: int, safety_buffer: int = 1,
                max_batch: int = 25) -> PaceProposal:
        p = self._wilson_lower(answered, attempts) if attempts else 0.20
        safe_slots = max(0, available_agents - in_flight_slots - safety_buffer)
        # Need a distinct hard agent slot for every call: absolute no-overflow invariant.
        request = min(max_batch, safe_slots, floor(safe_slots * min(1.0, p + 0.10)))
        return PaceProposal(request, f"safe_slots={safe_slots}; Wilson-LCB={p:.3f}", p)

    @staticmethod
    def _wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
        if total <= 0:
            return 0.0
        p = successes / total
        den = 1 + z*z/total
        center = p + z*z/(2*total)
        margin = z * ((p*(1-p)/total + z*z/(4*total*total)) ** 0.5)
        return max(0.0, (center-margin)/den)


class SafetyController:
    """Only gateway to provider. Clamps pace and protects capacity/health."""
    def __init__(self, provider_failure_threshold: int = 3) -> None:
        self.failure_threshold = provider_failure_threshold
        self.consecutive_failures = 0
        self.open_circuit = False
        self.audit: list[dict[str, object]] = []

    def authorize(self, proposal: PaceProposal, available_agents: int,
                  in_flight_slots: int, provider_healthy: bool = True) -> int:
        capacity = max(0, available_agents - in_flight_slots)
        requested = max(0, proposal.requested)
        if not provider_healthy or self.open_circuit:
            allowed, decision = 0, "REJECT_PROVIDER_UNHEALTHY"
        else:
            allowed = min(requested, capacity)
            decision = "APPROVE" if allowed == requested else "REDUCE_TO_CAPACITY"
            if allowed == 0 and requested:
                decision = "FALLBACK_PROGRESSIVE_OR_STOP"
        self.audit.append({"requested": requested, "allowed": allowed,
                           "capacity": capacity, "decision": decision,
                           "reason": proposal.reason})
        return allowed

    def provider_result(self, ok: bool) -> None:
        if ok:
            self.consecutive_failures = 0
            self.open_circuit = False
        else:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.failure_threshold:
                self.open_circuit = True


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    call_id: str
    status: str
    provider_call_id: str


class MockProvider:
    """Provider boundary: all event IDs and the app call ID are idempotency keys."""
    def __init__(self, name: str, answer_rate: float, failure_rate: float,
                 latency_seconds: float, duplicate_events: bool = False,
                 reorder_events: bool = False, seed: int = 42) -> None:
        self.name, self.answer_rate, self.failure_rate = name, answer_rate, failure_rate
        self.latency_seconds = latency_seconds
        self.duplicate_events, self.reorder_events = duplicate_events, reorder_events
        self.random = Random(seed)
        self.initiated: set[str] = set()
        self.healthy = True

    def originate(self, call_id: str, agent_id: str) -> list[ProviderEvent]:
        if not self.healthy or self.random.random() < self.failure_rate:
            raise TimeoutError(f"{self.name} originate failed")
        self.initiated.add(call_id)
        status = "ANSWERED" if self.random.random() < self.answer_rate else "NO_ANSWER"
        statuses = ["RINGING", status, "COMPLETED"] if status == "ANSWERED" else ["RINGING", "FAILED"]
        events = [ProviderEvent(f"{call_id}:{i}:{s}", call_id, s, f"{self.name}:{call_id}")
                  for i, s in enumerate(statuses)]
        if self.reorder_events:
            events.reverse()
        if self.duplicate_events and events:
            events.insert(1, events[0])
        return events


class FastReliableProvider(MockProvider):
    def __init__(self, answer_rate: float = .20, seed: int = 1) -> None:
        super().__init__("fast-reliable", answer_rate, .005, .05, seed=seed)


class SlowNoisyProvider(MockProvider):
    def __init__(self, answer_rate: float = .20, seed: int = 2) -> None:
        super().__init__("slow-noisy", answer_rate, .12, .8, True, True, seed)


class SmartDialer:
    """Coordinates allocation and provider; no code path bypasses safety."""
    def __init__(self, store: Store, provider: MockProvider,
                 safety: SafetyController | None = None) -> None:
        self.store, self.provider = store, provider
        self.safety = safety or SafetyController()

    def progressive(self, borrower_id: str, agent_id: str | None = None) -> str:
        with self.store.lock:
            total = len(self.store.agents)
            in_flight = sum(c.state not in TERMINAL for c in self.store.calls.values())
        permitted = self.safety.authorize(PaceProposal(1, "progressive one-agent request", 1.0),
                                          total, in_flight, self.provider.healthy)
        if permitted < 1:
            raise AllocationError("safety controller denied progressive call")
        reservation = self.store.reserve(borrower_id, agent_id)
        return self._dispatch(reservation.call_id)

    def predictive_batch(self, borrowers: Iterable[str], pacer: PredictivePacer,
                         answer_history: tuple[int, int], safety_buffer: int = 1) -> list[str]:
        candidates = list(borrowers)
        with self.store.lock:
            # Pacer/safety receive total staffed capacity; in-flight slots are subtracted once.
            avail = len(self.store.agents)
            in_flight = sum(c.state not in TERMINAL for c in self.store.calls.values())
        prop = pacer.propose(avail, in_flight, *answer_history, safety_buffer=safety_buffer)
        authorized = self.safety.authorize(prop, avail, in_flight, self.provider.healthy)
        ids = []
        # Distinct agent and borrower hard reservations before any provider side effect.
        for borrower in candidates[:authorized]:
            try:
                reservation = self.store.reserve(borrower)
            except AllocationError:
                break
            ids.append(self._dispatch(reservation.call_id))
        return ids

    def _dispatch(self, call_id: str) -> str:
        if not self.store.claim_initiation(call_id):
            return call_id
        call = self.store.calls[call_id]
        try:
            events = self.provider.originate(call_id, call.agent_id)
            self.safety.provider_result(True)
            for event in events:
                self.store.apply_event(event.call_id, event.event_id, event.status, event.provider_call_id)
        except TimeoutError as exc:
            self.safety.provider_result(False)
            # Ambiguous timeout: retain the reserved seat until provider reconciliation.
            # Releasing it could let a late answer arrive with no agent available.
            with self.store.lock:
                self.store.calls[call_id].error = f"reconciliation required: {exc}"
        return call_id


def wilson_lower_bound(successes: int, total: int) -> float:
    return PredictivePacer._wilson_lower(successes, total)
