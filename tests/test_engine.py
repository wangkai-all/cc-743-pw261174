from __future__ import annotations

import unittest
from copy import deepcopy

from admissions.audit import _find_cycles
from admissions.engine import compute_matching
from admissions.explain import explain_candidate
from admissions.fixtures import coalition_state, demo_state
from admissions.model import candidate_tier, ensure_tie_vectors
from admissions.verify import run_verification


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.state = demo_state()

    def test_demo_is_capacity_valid_and_stable(self):
        result = compute_matching(self.state, round_no=1, policy="full_recompute")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["capacity_audit"]["all_within_capacity"])
        self.assertTrue(result["stability"]["stable"])
        self.assertEqual(result["stability"]["blocking_pairs"], [])
        self.assertEqual(result["conflicts"], [])

    def test_saved_tie_draws_never_change_between_runs(self):
        before = deepcopy(self.state["tie_vectors"])
        compute_matching(self.state, round_no=1, policy="full_recompute")
        compute_matching(self.state, round_no=1, policy="full_recompute")
        self.assertEqual(before, self.state["tie_vectors"])

        p1_c01 = self.state["tie_vectors"]["P1"]["C01"]
        p1_c02 = self.state["tie_vectors"]["P1"]["C02"]
        self.assertNotEqual(p1_c01, p1_c02)

        r1 = compute_matching(self.state, round_no=1, policy="full_recompute")
        r2 = compute_matching(self.state, round_no=1, policy="full_recompute")
        self.assertEqual(
            {c: a["accepter_id"] for c, a in r1["assignments"].items()},
            {c: a["accepter_id"] for c, a in r2["assignments"].items()},
        )
        # Lower tuple wins; with the fixed fixture C02 takes the single P1 seat.
        self.assertEqual(r1["assignments"]["C02"]["accepter_id"], "P1")
        self.assertNotIn("P1", [a["accepter_id"] for cid, a in r1["assignments"].items() if cid == "C01" for a in [a]])

    def test_multiple_same_tier_groups_exist_in_demo(self):
        pmap = {p["id"]: p for p in self.state["programs"]}
        cmap = {c["id"]: c for c in self.state["candidates"]}

        p1_tiers = {cid: candidate_tier(cmap[cid], pmap["P1"]) for cid in ("C01", "C02")}
        p2_tiers = {cid: candidate_tier(cmap[cid], pmap["P2"]) for cid in ("C03", "C04")}
        self.assertEqual(set(p1_tiers.values()), {"sibling"})
        self.assertEqual(set(p2_tiers.values()), {"legacy"})
        self.assertNotEqual(self.state["tie_vectors"]["P1"]["C01"], self.state["tie_vectors"]["P1"]["C02"])
        self.assertNotEqual(self.state["tie_vectors"]["P2"]["C03"], self.state["tie_vectors"]["P2"]["C04"])

    def test_gate_failure_is_recorded(self):
        result = compute_matching(self.state, round_no=1, policy="full_recompute")
        gate_events = [e for e in result["events"] if e["reason_code"] == "gate_failed" and e["candidate_id"] == "C06"]
        self.assertTrue(gate_events)
        self.assertEqual(gate_events[0]["reason_detail"]["subject"], "maths")
        self.assertEqual(gate_events[0]["reason_detail"]["minimum"], 60)

    def test_first_choice_summary_uses_program_name(self):
        result = compute_matching(self.state, round_no=1, policy="full_recompute")
        explanation = explain_candidate(self.state, result, "C02")
        self.assertIn("青禾实验中学·望江校区", explanation["summary"])
        self.assertNotIn("志愿 P1", explanation["summary"])

    def test_over_capacity_special_promise_fails_capacity_audit(self):
        full = compute_matching(self.state, round_no=1, policy="full_recompute")
        holder = next(
            cid
            for cid, a in full["assignments"].items()
            if a["accepter_id"] == "P1" and a["pool"] == "special"
        )
        self.state["seats"] = [
            {
                "candidate_id": holder,
                "program_id": "P1",
                "pool": "special",
                "round_granted": 0,
                "released_in_round": None,
            }
        ]
        for p in self.state["programs"]:
            if p["id"] == "P1":
                p["quotas"]["special"] = 0

        result = compute_matching(self.state, round_no=1, policy="protect_promises")
        row = next(r for r in result["capacity_audit"]["rows"] if r["accepter_id"] == "P1")
        self.assertTrue(row["over_capacity"])
        self.assertFalse(result["capacity_audit"]["all_within_capacity"])
        codes = {c["code"] for c in result["conflicts"]}
        self.assertIn("capacity_oversubscribed", codes)
        self.assertIn("capacity_violation", codes)

    def test_joint_capacity_and_host_capacity_are_respected(self):
        result = compute_matching(self.state, round_no=1, policy="full_recompute")
        joint_holders = [cid for cid, a in result["assignments"].items() if a["accepter_id"] == "J1"]
        self.assertEqual(sorted(joint_holders), ["C13", "C14"])
        hosts = [result["assignments"][cid]["host_program_id"] for cid in joint_holders]
        self.assertEqual(sorted(hosts), ["P2", "P4"])
        for row in result["capacity_audit"]["rows"]:
            if row["accepter_id"] == "J1":
                self.assertLessEqual(row["special_used"], row["special_effective_capacity"])
                self.assertEqual(row["special_used"], 2)

    def test_special_to_normal_conversion_can_change_outcome(self):
        def state(convert):
            return {
                "seed_text": "unit-conversion",
                "programs": [
                    {
                        "id": "P1",
                        "name": "P1",
                        "priority_order": ["sibling"],
                        "quotas": {"normal": 1, "special": 1},
                        "requires": {},
                        "convert_special_to_normal": convert,
                    }
                ],
                "joint_programs": [],
                "candidates": [
                    {"id": "C1", "name": "C1", "pool": "normal", "scores": {}, "priorities": {}, "choices": [{"program_id": "P1"}]},
                    {"id": "C2", "name": "C2", "pool": "normal", "scores": {}, "priorities": {}, "choices": [{"program_id": "P1"}]},
                    {"id": "C3", "name": "C3", "pool": "normal", "scores": {}, "priorities": {}, "choices": [{"program_id": "P1"}]},
                ],
            }

        converted = compute_matching(state(True), round_no=1, policy="full_recompute")
        reserved = compute_matching(state(False), round_no=1, policy="full_recompute")
        self.assertEqual(len(converted["assignments"]), 2)
        self.assertEqual(len(reserved["assignments"]), 1)
        self.assertEqual(converted["conversion"][0]["converted_to_normal"], 1)
        self.assertEqual(reserved["conversion"][0]["converted_to_normal"], 0)

    def test_promise_coalition_is_found_and_full_recompute_resolves_it(self):
        state = coalition_state()
        protected = compute_matching(state, round_no=1, policy="protect_promises")
        full = compute_matching(state, round_no=1, policy="full_recompute")

        self.assertFalse(protected["stability"]["stable"])
        self.assertTrue(protected["stability"]["constrained_stable"])
        self.assertEqual(len(protected["stability"]["promise_blocking_coalitions"]), 1)
        coalition = protected["stability"]["blocking_coalitions"][0]
        self.assertEqual(coalition["members"], ["K1", "K2", "K3"])
        self.assertTrue(coalition["promise_caused"])

        self.assertTrue(full["stability"]["stable"])
        self.assertEqual(full["stability"]["blocking_pairs"], [])
        self.assertEqual(full["assignments"]["K1"]["accepter_id"], "A2")
        self.assertEqual(full["assignments"]["K2"]["accepter_id"], "A3")
        self.assertEqual(full["assignments"]["K3"]["accepter_id"], "A1")

    def test_coalition_search_branches_over_multiple_blocking_edges(self):
        state = {
            "candidates": [{"id": cid} for cid in ("A", "B", "C", "Z")],
            "programs": [{"id": "X"}, {"id": "Y"}, {"id": "ZED"}],
            "joint_programs": [],
        }
        assignments = {
            "B": {"accepter_id": "X"},
            "C": {"accepter_id": "Y"},
            "A": {"accepter_id": "ZED"},
            "Z": {"accepter_id": "X"},
        }
        # A has two valid replacement edges.  A -> Z is visited/listed last in
        # the old single-edge representation and would hide A -> B -> C -> A.
        edge_options = {"A": ["B", "Z"], "B": ["C"], "C": ["A"]}
        cycles = _find_cycles(edge_options, set(), state, assignments)
        self.assertEqual([c["members"] for c in cycles], [["A", "B", "C"]])
        step_targets = [s["target_accepter_id"] for s in cycles[0]["steps"]]
        self.assertEqual(step_targets, ["X", "Y", "ZED"])

    def test_exhaustive_small_instances(self):
        report = run_verification(exhaustive_cases=12)
        failures = [c for c in report["cases"] if not c["pass"]]
        self.assertTrue(report["all_passed"], failures)
        self.assertGreaterEqual(report["total"], 16)


if __name__ == "__main__":
    unittest.main()
