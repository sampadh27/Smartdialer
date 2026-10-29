import threading
import unittest

from smartdialer import (AgentState, AllocationError, CallState, FastReliableProvider,
    SlowNoisyProvider, PaceProposal, PredictivePacer, SafetyController, SmartDialer, Store)


class DialerTests(unittest.TestCase):
    def test_concurrent_workers_cannot_reserve_same_agent(self):
        store = Store()
        store.add_agent("A")
        successes, failures = [], []
        barrier = threading.Barrier(12)
        def worker(i):
            barrier.wait()
            try:
                successes.append(store.reserve(f"B{i}"))
            except AllocationError:
                failures.append(i)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 11)

    def test_duplicate_and_out_of_order_provider_events_do_not_resurrect(self):
        store = Store(); store.add_agent("A")
        res = store.reserve("B"); store.claim_initiation(res.call_id)
        self.assertTrue(store.apply_event(res.call_id, "done", "COMPLETED", "p:1"))
        self.assertFalse(store.apply_event(res.call_id, "answer", "ANSWERED", "p:1"))
        self.assertFalse(store.apply_event(res.call_id, "done", "COMPLETED", "p:1"))
        self.assertEqual(store.calls[res.call_id].state, CallState.COMPLETED)
        self.assertEqual(store.agents["A"].state, AgentState.WRAP_UP)
        self.assertTrue(store.finish_wrap_up("A", res.call_id))
        self.assertEqual(store.agents["A"].state, AgentState.AVAILABLE)

    def test_repeated_answer_events_reserve_one_agent_once(self):
        store = Store(); store.add_agent("A")
        res = store.reserve("B"); store.claim_initiation(res.call_id)
        self.assertTrue(store.apply_event(res.call_id, "a1", "ANSWERED", "p:1"))
        self.assertFalse(store.apply_event(res.call_id, "a1", "ANSWERED", "p:1"))
        self.assertFalse(store.apply_event(res.call_id, "a2", "ANSWERED", "p:1"))
        self.assertEqual(store.agents["A"].state, AgentState.CONNECTED)
        self.assertEqual(store.agents["A"].current_call, res.call_id)

    def test_provider_timeout_holds_capacity_for_late_answer(self):
        store = Store(); store.add_agent("A")
        class AmbiguousTimeoutProvider(FastReliableProvider):
            def originate(self, call_id, agent_id):
                raise TimeoutError("accepted request response was lost")
        provider = AmbiguousTimeoutProvider()
        dialer = SmartDialer(store, provider, SafetyController(provider_failure_threshold=1))
        call_id = dialer.progressive("B")
        self.assertEqual(store.calls[call_id].state, CallState.INITIATED)
        self.assertEqual(store.agents["A"].state, AgentState.DIALING)
        self.assertTrue(dialer.safety.open_circuit)
        self.assertEqual(dialer.safety.authorize(PaceProposal(1, "test", .2), 1, 0), 0)

    def test_safety_clamps_predictive_request_and_opens_circuit(self):
        safety = SafetyController(provider_failure_threshold=2)
        n = safety.authorize(PaceProposal(20, "too aggressive", .7), 3, 1)
        self.assertEqual(n, 2)
        safety.provider_result(False); safety.provider_result(False)
        self.assertEqual(safety.authorize(PaceProposal(2, "retry", .4), 3, 0), 0)

    def test_pacer_never_exceeds_distinct_free_agent_slots(self):
        pacer = PredictivePacer()
        p = pacer.propose(10, 8, 70, 100, safety_buffer=0)
        self.assertLessEqual(p.requested, 2)
        self.assertEqual(pacer.propose(10, 10, 70, 100).requested, 0)

    def test_worker_crash_reservation_recovery(self):
        store = Store(); store.add_agent("A")
        res = store.reserve("B")
        store.calls[res.call_id].created_at -= 60
        self.assertEqual(store.expire_reservations(30), 1)
        self.assertEqual(store.agents["A"].state, AgentState.AVAILABLE)
        self.assertNotIn("B", store.active_borrowers)

    def test_two_mock_providers_have_different_event_behaviors(self):
        fast = FastReliableProvider(answer_rate=1.0, seed=10)
        noisy = SlowNoisyProvider(answer_rate=1.0, seed=10)
        fast_events = fast.originate("c1", "a1")
        noisy_events = noisy.originate("c2", "a2")
        self.assertEqual(fast.latency_seconds, .05)
        self.assertEqual(noisy.latency_seconds, .8)
        self.assertFalse(fast.duplicate_events)
        self.assertTrue(noisy.duplicate_events and noisy.reorder_events)
        self.assertEqual(fast_events[-1].status, "COMPLETED")
        self.assertEqual(noisy_events[0].status, "COMPLETED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
