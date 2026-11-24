"""Independent small-instance exhaustive verification.

The brute-force checker intentionally re-derives validity and blocking pairs
instead of importing the audit implementation.  A bug shared by the engine and
audit should therefore be caught here on tiny instances.
"""

from __future__ import annotations

import itertools
import random
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from .engine import compute_matching
from .model import ensure_tie_vectors, rank_key


# ---------------------------------------------------------------------------
# Generic one-pool exhaustive check
# ---------------------------------------------------------------------------


def _pref_index(c: Dict[str, Any], aid: Optional[str]) -> int:
    choices = [x["program_id"] for x in c.get("choices", [])]
    if aid is None:
        return len(choices)
    return choices.index(aid)


def _bruteforce_one_pool(state: Dict[str, Any]) -> Dict[str, Any]:
    candidates = state["candidates"]
    programs = state["programs"]
    caps = {p["id"]: int(p["quotas"].get("normal", 0)) for p in programs}
    aids = list(caps)
    ensure_tie_vectors(state)

    options = [[None] + [x["program_id"] for x in c["choices"]] for c in candidates]
    total = 1
    for opts in options:
        total *= len(opts)

    stable_matchings: List[Dict[str, Optional[str]]] = []
    valid_count = 0

    for combo in itertools.product(*options):
        matching = {candidates[i]["id"]: combo[i] for i in range(len(candidates))}
        used: Dict[str, int] = {aid: 0 for aid in aids}
        valid = True
        for aid in matching.values():
            if aid is not None:
                used[aid] += 1
                if used[aid] > caps[aid]:
                    valid = False
                    break
        if not valid:
            continue
        valid_count += 1

        blocking = _bruteforce_blocking_pairs(state, matching, caps, pool="normal")
        if not blocking:
            stable_matchings.append(matching)

    result = compute_matching(state, round_no=1, policy="full_recompute")
    da_map = {cid: a["accepter_id"] for cid, a in result["assignments"].items()}

    da_valid = all(da_map.get(c["id"]) in [x["program_id"] for x in c["choices"]] + [None] for c in candidates)
    da_capacity = all(sum(1 for a in da_map.values() if a == aid) <= caps[aid] for aid in aids)
    da_blocking = _bruteforce_blocking_pairs(state, da_map, caps, pool="normal")

    student_optimal = True
    worst_violation = None
    for stable in stable_matchings:
        for c in candidates:
            cid = c["id"]
            da_i = _pref_index(c, da_map.get(cid))
            stable_i = _pref_index(c, stable.get(cid))
            if da_i > stable_i:
                student_optimal = False
                worst_violation = {"candidate_id": cid, "da_index": da_i, "stable_index": stable_i}
                break
        if not student_optimal:
            break

    return {
        "kind": "one_pool_da",
        "candidates": len(candidates),
        "programs": len(programs),
        "enumerated_assignments": total,
        "valid_matchings": valid_count,
        "stable_matching_count": len(stable_matchings),
        "da_capacity_valid": da_capacity,
        "da_choice_valid": da_valid,
        "da_blocking_pair_count": len(da_blocking),
        "da_stable": not da_blocking,
        "student_optimal_vs_all_stable": student_optimal,
        "student_optimal_violation": worst_violation,
        "pass": da_valid and da_capacity and not da_blocking and student_optimal and bool(stable_matchings),
    }


def _bruteforce_blocking_pairs(
    state: Dict[str, Any],
    matching: Dict[str, Optional[str]],
    caps: Dict[str, int],
    *,
    pool: str,
) -> List[Dict[str, Any]]:
    cmap = {c["id"]: c for c in state["candidates"]}
    amap = {a["id"]: a for a in state["programs"] + state.get("joint_programs", [])}
    holders: Dict[str, List[str]] = {aid: [] for aid in caps}
    for cid, aid in matching.items():
        if aid is not None and aid in holders:
            holders[aid].append(cid)

    pairs = []
    for c in state["candidates"]:
        cid = c["id"]
        current = matching.get(cid)
        current_i = _pref_index(c, current)
        for i, ch in enumerate(c.get("choices", [])):
            if i >= current_i:
                break
            aid = ch["program_id"]
            if aid not in caps:
                continue
            accepter = amap[aid]
            hs = holders[aid]
            if len(hs) < caps[aid]:
                pairs.append({"candidate_id": cid, "accepter_id": aid, "kind": "vacancy"})
                continue
            worst = max(hs, key=lambda h: rank_key(state, cmap[h], accepter))
            if rank_key(state, c, accepter) < rank_key(state, cmap[worst], accepter):
                pairs.append(
                    {
                        "candidate_id": cid,
                        "accepter_id": aid,
                        "kind": "priority",
                        "worst_holder_id": worst,
                    }
                )
    return pairs


def _random_one_pool(seed: int, n: int = 5, m: int = 3) -> Dict[str, Any]:
    rng = random.Random(seed)
    pids = [f"P{i}" for i in range(1, m + 1)]
    programs = []
    for pid in pids:
        # Tight enough to create rejections.
        programs.append(
            {
                "id": pid,
                "name": pid,
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": rng.randint(1, 2), "special": 0},
                "requires": {},
                "convert_special_to_normal": False,
            }
        )
    candidates = []
    all_tiers = [None, "sibling", "legacy", "diversity"]
    for i in range(1, n + 1):
        cid = f"C{i:02d}"
        order = pids[:]
        rng.shuffle(order)
        k = rng.randint(1, m)
        choices = [{"program_id": pid} for pid in order[:k]]
        priorities: Dict[str, List[str]] = {}
        # Deliberately create many same-tier groups.
        for pid in pids:
            tier = rng.choice(all_tiers)
            if tier:
                priorities.setdefault(tier, []).append(pid)
        candidates.append(
            {
                "id": cid,
                "name": cid,
                "pool": "normal",
                "scores": {},
                "priorities": priorities,
                "choices": choices,
            }
        )
    state = {
        "seed_text": f"exhaustive-{seed}",
        "candidates": candidates,
        "programs": programs,
        "joint_programs": [],
    }
    ensure_tie_vectors(state)
    return state


def _tie_persistence_case() -> Dict[str, Any]:
    from .fixtures import demo_state

    state = demo_state()
    before = deepcopy(state["tie_vectors"])
    r1 = compute_matching(state, round_no=1, policy="full_recompute")
    r2 = compute_matching(state, round_no=1, policy="full_recompute")
    after = state["tie_vectors"]
    same_vectors = before == after
    a1 = {c: a["accepter_id"] for c, a in r1["assignments"].items()}
    a2 = {c: a["accepter_id"] for c, a in r2["assignments"].items()}
    return {
        "kind": "persisted_tie_draws",
        "pass": same_vectors and a1 == a2,
        "tie_vectors_unchanged": same_vectors,
        "repeated_computation_identical": a1 == a2,
        "p1_pair": {
            "C01": state["tie_vectors"]["P1"]["C01"],
            "C02": state["tie_vectors"]["P1"]["C02"],
        },
        "p2_pair": {
            "C03": state["tie_vectors"]["P2"]["C03"],
            "C04": state["tie_vectors"]["P2"]["C04"],
        },
    }


def _joint_shared_capacity_case() -> Dict[str, Any]:
    state = {
        "seed_text": "exhaustive-joint",
        "programs": [
            {
                "id": "P2",
                "name": "宿主二",
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": 0, "special": 2},
                "requires": {},
            },
            {
                "id": "P4",
                "name": "宿主四",
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": 0, "special": 2},
                "requires": {},
            },
        ],
        "joint_programs": [
            {
                "id": "J1",
                "name": "共享专项",
                "capacity": 2,
                "hosts": [{"program_id": "P2", "capacity": 1}, {"program_id": "P4", "capacity": 1}],
                "priority_order": ["sibling", "legacy", "diversity"],
                "requires": {},
                "convert_unused": False,
            }
        ],
        "candidates": [
            {
                "id": "C1",
                "name": "C1",
                "pool": "special",
                "scores": {},
                "priorities": {"sibling": ["J1"]},
                "choices": [{"program_id": "J1"}],
                "host_preferences": ["P2", "P4"],
            },
            {
                "id": "C2",
                "name": "C2",
                "pool": "special",
                "scores": {},
                "priorities": {"sibling": ["J1"]},
                "choices": [{"program_id": "J1"}],
                "host_preferences": ["P2", "P4"],
            },
            {
                "id": "C3",
                "name": "C3",
                "pool": "special",
                "scores": {},
                "priorities": {},
                "choices": [{"program_id": "J1"}],
                "host_preferences": ["P2", "P4"],
            },
        ],
    }
    ensure_tie_vectors(state)
    # Exhaust J1 seat-count assignments.
    count = 0
    valid = 0
    stable_count = 0
    for bits in itertools.product([0, 1], repeat=3):
        count += 1
        matching = {f"C{i+1}": ("J1" if bits[i] else None) for i in range(3)}
        used = sum(bits)
        if used > 2:
            continue
        valid += 1
        caps = {"J1": 2}
        bp = _bruteforce_blocking_pairs(state, matching, caps, pool="special")
        if not bp:
            stable_count += 1

    result = compute_matching(state, round_no=1, policy="full_recompute")
    j_holders = [cid for cid, a in result["assignments"].items() if a["accepter_id"] == "J1"]
    host_counts: Dict[str, int] = {}
    for cid in j_holders:
        h = result["assignments"][cid].get("host_program_id")
        host_counts[h] = host_counts.get(h, 0) + 1
    bp = result["stability"]["blocking_pairs"]
    return {
        "kind": "shared_joint_capacity",
        "enumerated_assignments": count,
        "valid_matchings": valid,
        "stable_matching_count": stable_count,
        "joint_holders": j_holders,
        "host_counts": host_counts,
        "host_capacity_respected": all(v <= 1 for v in host_counts.values()),
        "da_blocking_pair_count": len(bp),
        "pass": len(j_holders) == 2 and all(v <= 1 for v in host_counts.values()) and not bp,
    }


def _conversion_case() -> Dict[str, Any]:
    def build(convert: bool):
        return {
            "seed_text": "conversion-check",
            "programs": [
                {
                    "id": "P1",
                    "name": "P1",
                    "priority_order": ["sibling", "legacy", "diversity"],
                    "quotas": {"normal": 1, "special": 1},
                    "requires": {},
                    "convert_special_to_normal": convert,
                }
            ],
            "joint_programs": [],
            "candidates": [
                {
                    "id": "C1",
                    "name": "C1",
                    "pool": "normal",
                    "scores": {},
                    "priorities": {},
                    "choices": [{"program_id": "P1"}],
                },
                {
                    "id": "C2",
                    "name": "C2",
                    "pool": "normal",
                    "scores": {},
                    "priorities": {},
                    "choices": [{"program_id": "P1"}],
                },
                {
                    "id": "C3",
                    "name": "C3",
                    "pool": "normal",
                    "scores": {},
                    "priorities": {},
                    "choices": [{"program_id": "P1"}],
                },
            ],
        }

    converted = compute_matching(build(True), round_no=1, policy="full_recompute")
    reserved = compute_matching(build(False), round_no=1, policy="full_recompute")
    return {
        "kind": "special_to_normal_conversion",
        "with_conversion_assigned": sorted(converted["assignments"]),
        "without_conversion_assigned": sorted(reserved["assignments"]),
        "normal_capacity_with_conversion": converted["capacity_audit"]["rows"][0]["normal_effective_capacity"],
        "normal_capacity_without_conversion": reserved["capacity_audit"]["rows"][0]["normal_effective_capacity"],
        "pass": len(converted["assignments"]) == 2 and len(reserved["assignments"]) == 1,
    }


def _withdrawal_continuity_case() -> Dict[str, Any]:
    from .fixtures import demo_state

    state = demo_state()
    first = compute_matching(state, round_no=1, policy="full_recompute")
    for cid, a in first["assignments"].items():
        state["seats"].append(
            {
                "candidate_id": cid,
                "program_id": a["accepter_id"],
                "accepter_type": a["accepter_type"],
                "host_program_id": a.get("host_program_id"),
                "pool": a["pool"],
                "choice_index": a.get("choice_index"),
                "round_granted": 1,
                "released_in_round": None,
            }
        )
    victim = "C09" if "C09" in first["assignments"] else first["unmatched"][0]
    state["withdrawals"].append({"candidate_id": victim, "round": 2, "reason": "verify"})
    for seat in state["seats"]:
        if seat["candidate_id"] == victim and seat["released_in_round"] is None:
            seat["released_in_round"] = 2

    prev = {cid: a["accepter_id"] for cid, a in first["assignments"].items()}
    trigger = {"withdrawals": [victim]}
    full = compute_matching(
        state, round_no=2, policy="full_recompute", trigger=trigger, previous_assignments=prev
    )
    protected = compute_matching(
        state, round_no=2, policy="protect_promises", trigger=trigger, previous_assignments=prev
    )
    return {
        "kind": "withdrawal_state_continuity",
        "withdrawn_candidate": victim,
        "full_recompute_moves": len(full["chains"]["moves"]),
        "protect_promises_moves": len(protected["chains"]["moves"]),
        "protect_promise_blocking_pairs": len(protected["stability"]["promise_blocking_pairs"]),
        "withdrawn_absent_full": victim not in full["assignments"],
        "withdrawn_absent_protect": victim not in protected["assignments"],
        "pass": victim not in full["assignments"] and victim not in protected["assignments"],
    }


def run_verification(*, exhaustive_cases: int = 24) -> Dict[str, Any]:
    cases: List[Dict[str, Any]] = []
    for seed in range(exhaustive_cases):
        state = _random_one_pool(1000 + seed)
        case = _bruteforce_one_pool(state)
        case["name"] = f"随机小实例 #{seed + 1}"
        cases.append(case)

    named = [
        _tie_persistence_case(),
        _joint_shared_capacity_case(),
        _conversion_case(),
        _withdrawal_continuity_case(),
    ]
    for i, case in enumerate(named):
        case.setdefault("name", case["kind"])
        cases.append(case)

    passed = sum(1 for c in cases if c.get("pass"))
    return {
        "all_passed": passed == len(cases),
        "passed": passed,
        "total": len(cases),
        "cases": cases,
        "note": "穷举只用于容量较小的实例；主演示数据通过容量/稳定性独立审计核对。",
    }


if __name__ == "__main__":  # pragma: no cover
    import json

    print(json.dumps(run_verification(), ensure_ascii=False, indent=2))
