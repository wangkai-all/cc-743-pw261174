from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from admissions import store
from admissions.server import Handler


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        self._old_path = store.STATE_PATH
        store.STATE_PATH = self.path
        store.reset_state("demo")

    def tearDown(self):
        store.STATE_PATH = self._old_path
        self.tmp.cleanup()

    def test_initial_round_and_explanation(self):
        state = store.load_state()
        self.assertEqual(state["current_round"], 1)
        explanation = store.explain(1, "C01")
        self.assertIn("沈望舒", explanation["summary"])
        self.assertTrue(any(row["status"] == "rejected" for row in explanation["rows"]))

    def test_add_high_priority_candidate_creates_promise_block_only_under_protection(self):
        action = {
            "type": "add_candidate",
            "name": "陆星回",
            "pool": "normal",
            "maths": 90,
            "science": 88,
            "priority_tier": "sibling",
            "priority_accepter_id": "P2",
            "choice_ids": ["P2", "P1", "P3"],
        }
        preview = store.preview(action)
        full = preview["policies"]["full_recompute"]
        protected = preview["policies"]["protect_promises"]
        self.assertIn("C19", full["assignments"])
        self.assertEqual(full["assignments"]["C19"]["accepter_id"], "P2")
        self.assertNotIn("C19", protected["assignments"])
        self.assertEqual(protected["stats"]["promise_blocking_pairs"], 1)
        pair = protected["stability"]["promise_blocking_pairs"][0]
        self.assertEqual((pair["candidate_id"], pair["accepter_id"]), ("C19", "P2"))

        record = store.commit(action, "protect_promises")
        self.assertEqual(record["round_no"], 2)
        self.assertEqual(store.load_state()["current_round"], 2)
        # Under promise protection C19 is the blocking outsider and is not seated.
        self.assertFalse(any(s["candidate_id"] == "C19" for s in store.load_state()["seats"]))

    def test_withdrawal_releases_seat_and_builds_chain(self):
        action = {"type": "withdraw", "candidate_id": "C03", "reason": "搬家"}
        preview = store.preview(action, "protect_promises")
        result = preview["policies"]["protect_promises"]
        self.assertNotIn("C03", result["assignments"])
        self.assertTrue(result["chains"]["chains"])
        self.assertIn("C08", result["assignments"])

        record = store.commit(action, "protect_promises")
        state = store.load_state()
        self.assertTrue(any(w["candidate_id"] == "C03" and w["round"] == 2 for w in state["withdrawals"]))
        seat = next(s for s in state["seats"] if s["candidate_id"] == "C03" and s.get("released_in_round") == 2)
        self.assertEqual(seat["released_in_round"], 2)
        self.assertEqual(record["result"]["trigger"]["withdrawals"], ["C03"])

    def test_chain_with_policy_divergence_for_c02(self):
        action = {"type": "withdraw", "candidate_id": "C02", "reason": "搬迁"}
        preview = store.preview(action)
        full = preview["policies"]["full_recompute"]
        protected = preview["policies"]["protect_promises"]

        self.assertEqual(full["assignments"]["C01"]["accepter_id"], "P1")
        self.assertEqual(full["assignments"]["C18"]["accepter_id"], "P3")
        self.assertIsNone(full["assignments"].get("C08"))
        self.assertEqual(len(full["chains"]["chains"][0]["steps"]), 2)

        self.assertEqual(protected["assignments"]["C01"]["accepter_id"], "P3")
        self.assertEqual(protected["assignments"]["C08"]["accepter_id"], "P1")
        pair = protected["stability"]["promise_blocking_pairs"][0]
        self.assertEqual((pair["candidate_id"], pair["accepter_id"], pair["worst_holder_id"]), ("C01", "P1", "C08"))

    def test_compare_only_preview_does_not_create_round(self):
        preview = store.preview({"type": "compare_only"})
        self.assertTrue(preview["compare_only"])
        self.assertEqual(preview["round"], 1)
        self.assertEqual(preview["computed_round"], 2)
        self.assertIn("full_recompute", preview["policies"])
        self.assertIn("protect_promises", preview["policies"])
        self.assertEqual(store.load_state()["current_round"], 1)
        self.assertEqual(len(store.load_state()["rounds"]), 1)

    def test_capacity_below_promises_is_an_explicit_conflict(self):
        action = {"type": "edit_capacity", "accepter_id": "P1", "pool": "normal", "new_capacity": 0}
        record = store.commit(action, "protect_promises")
        codes = {c["code"] for c in record["result"]["conflicts"]}
        self.assertIn("capacity_oversubscribed", codes)
        self.assertIn("capacity_violation", codes)
        self.assertEqual(record["result"]["status"], "conflict")

    def test_coalition_reset(self):
        state = store.reset_state("coalition")
        self.assertEqual(state["scenario"], "promise_coalition")
        record = store.latest_round(state)
        self.assertEqual(record["policy"], "protect_promises")
        self.assertEqual(len(record["result"]["stability"]["promise_blocking_coalitions"]), 1)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._old_path = store.STATE_PATH
        store.STATE_PATH = Path(cls.tmp.name) / "state.json"
        store.reset_state("demo")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        store.STATE_PATH = cls._old_path
        cls.tmp.cleanup()

    def get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def post(self, path, payload):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read().decode("utf-8"))

    def test_health_state_and_static(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        status, data = self.get("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("candidates", data)
        with urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as res:
            html = res.read().decode("utf-8")
            self.assertEqual(res.status, 200)
            self.assertIn("招生匹配分析台", html)

    def test_missing_static_asset_returns_404_not_html_shell(self):
        with self.assertRaises(HTTPError) as ctx:
            urlopen(f"http://127.0.0.1:{self.port}/static/does-not-exist.js", timeout=10)
        self.assertEqual(ctx.exception.code, 404)
        self.assertIn("application/json", ctx.exception.headers.get("Content-Type", ""))

    def test_preview_and_verify_http(self):
        status, report = self.get("/api/verify")
        self.assertEqual(status, 200)
        self.assertTrue(report["all_passed"])
        status, preview = self.post("/api/preview", {"action": {"type": "withdraw", "candidate_id": "C07"}})
        self.assertEqual(status, 200)
        self.assertIn("protect_promises", preview["policies"])


if __name__ == "__main__":
    unittest.main()
