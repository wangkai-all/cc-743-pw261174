"""JSON-file persistence and application service operations."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .engine import compute_matching
from .explain import attach_event_texts, explain_candidate
from .fixtures import coalition_state, demo_state
from .model import StateError, ensure_tie_vectors, new_id, validate_state

DATA_DIR = Path(os.environ.get("ADMISSIONS_DATA_DIR", "/workspace/var"))
STATE_PATH = Path(os.environ.get("ADMISSIONS_STATE_PATH", DATA_DIR / "state.json"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def init_state(scenario: str = "demo") -> Dict[str, Any]:
    state = coalition_state() if scenario == "coalition" else demo_state()
    ensure_tie_vectors(state)
    validate_state(state)
    _create_initial_round(state)
    return state


def _create_initial_round(state: Dict[str, Any]) -> None:
    round_no = 1
    preset = bool(state.pop("seed_commitments", False))
    policy = "protect_promises" if preset else "full_recompute"
    trigger = {"type": "seed_commitments" if preset else "initial_demo"}
    result = compute_matching(
        state,
        round_no=round_no,
        policy=policy,
        trigger=trigger,
        previous_assignments={},
    )
    result["computed_at"] = now_iso()
    attach_event_texts(state, result)

    if not preset:
        for cid, a in result["assignments"].items():
            state.setdefault("seats", []).append(
                {
                    "candidate_id": cid,
                    "program_id": a["accepter_id"],
                    "accepter_type": a.get("accepter_type", "program"),
                    "host_program_id": a.get("host_program_id"),
                    "pool": a["pool"],
                    "choice_index": a.get("choice_index"),
                    "round_granted": round_no,
                    "released_in_round": None,
                }
            )

    record = {
        "round_no": round_no,
        "created_at": now_iso(),
        "policy": policy,
        "action": {"type": "initialize", "scenario": state.get("scenario", "demo")},
        "trigger": trigger,
        "result": result,
        "snapshot": _snapshot(state),
    }
    state["rounds"] = [record]
    state["current_round"] = round_no


def _snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "seed_text": state.get("seed_text"),
        "tie_vectors": state.get("tie_vectors", {}),
        "candidates": deepcopy(state.get("candidates", [])),
        "programs": deepcopy(state.get("programs", [])),
        "joint_programs": deepcopy(state.get("joint_programs", [])),
    }


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        state = init_state("demo")
        save_state(state)
        return state
    with STATE_PATH.open("r", encoding="utf-8") as f:
        state = json.load(f)
    ensure_tie_vectors(state)
    return state


def save_state(state: Dict[str, Any]) -> None:
    validate_state(state)
    atomic_write_json(STATE_PATH, state)


def reset_state(scenario: str = "demo") -> Dict[str, Any]:
    state = init_state(scenario)
    save_state(state)
    return state


def latest_round(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rounds = state.get("rounds") or []
    return rounds[-1] if rounds else None


def previous_assignment_map(state: Dict[str, Any]) -> Dict[str, str]:
    rec = latest_round(state)
    if not rec:
        return {}
    return {cid: a["accepter_id"] for cid, a in rec["result"]["assignments"].items()}


def apply_action(state: Dict[str, Any], action: Dict[str, Any], round_no: int) -> Dict[str, Any]:
    """Mutate state according to a user action. Returns trigger metadata."""
    kind = action.get("type")
    if kind == "add_candidate":
        candidate = _build_candidate(state, action.get("candidate") or action)
        cid = new_id(state, "C")
        candidate["id"] = cid
        state.setdefault("candidates", []).append(candidate)
        ensure_tie_vectors(state)
        return {"type": "add_candidate", "candidate_id": cid}

    if kind == "edit_capacity":
        aid = action.get("accepter_id")
        pool = action.get("pool", "normal")
        new_cap = int(action.get("new_capacity"))
        if new_cap < 0:
            raise StateError("容量不能为负数")
        pmap = {p["id"]: p for p in state.get("programs", [])}
        jmap = {j["id"]: j for j in state.get("joint_programs", [])}
        if aid in pmap:
            p = pmap[aid]
            old = int((p.get("quotas") or {}).get(pool, 0))
            p.setdefault("quotas", {})[pool] = new_cap
        elif aid in jmap:
            if pool != "joint":
                raise StateError("跨校共享专项请修改 joint 总容量")
            j = jmap[aid]
            old = int(j.get("capacity", 0))
            j["capacity"] = new_cap
        else:
            raise StateError(f"未知项目 {aid}")
        return {
            "type": "edit_capacity",
            "capacity_edits": [
                {"program_id": aid, "accepter_id": aid, "pool": pool, "old_capacity": old, "new_capacity": new_cap}
            ],
        }

    if kind == "withdraw":
        cid = action.get("candidate_id")
        reason = (action.get("reason") or "").strip() or "家长申请撤回"
        c = next((x for x in state["candidates"] if x["id"] == cid), None)
        if c is None:
            raise StateError(f"未知学生 {cid}")
        already = any(w["candidate_id"] == cid for w in state.get("withdrawals", []))
        if already:
            raise StateError(f"{cid} 已经撤回")
        state.setdefault("withdrawals", []).append(
            {"candidate_id": cid, "round": round_no, "reason": reason, "at": now_iso()}
        )
        for seat in state.get("seats", []):
            if seat["candidate_id"] == cid and seat.get("released_in_round") is None:
                seat["released_in_round"] = round_no
                seat["release_reason"] = "withdrawal"
        return {"type": "withdraw", "withdrawals": [cid], "reason": reason}

    raise StateError(f"未知操作类型 {kind!r}")


def _build_candidate(state: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name:
        raise StateError("请填写学生姓名")
    pool = data.get("pool", "normal")
    if pool not in ("normal", "special"):
        raise StateError("池别必须是 normal 或 special")

    choice_ids = data.get("choice_ids") or data.get("choices") or []
    if isinstance(choice_ids, str):
        choice_ids = [x.strip() for x in choice_ids.split(",") if x.strip()]
    choices = [{"program_id": aid} for aid in choice_ids if aid]
    if not choices:
        raise StateError("至少提交一个志愿")

    scores = {}
    for subject in ("maths", "science", "language"):
        raw = data.get(subject)
        if raw not in (None, ""):
            try:
                scores[subject] = float(raw)
            except (TypeError, ValueError):
                raise StateError(f"{subject} 成绩必须是数字")

    priorities: Dict[str, list] = {}
    tier = data.get("priority_tier")
    target = data.get("priority_accepter_id")
    if tier and target:
        priorities[tier] = [target]

    host_prefs = data.get("host_preference_ids") or []
    if isinstance(host_prefs, str):
        host_prefs = [x.strip() for x in host_prefs.split(",") if x.strip()]

    return {
        "name": name,
        "pool": pool,
        "scores": scores,
        "priorities": priorities,
        "choices": choices,
        "host_preferences": host_prefs,
        "note": (data.get("note") or "").strip(),
    }


def preview(action: Dict[str, Any], policy: Optional[str] = None) -> Dict[str, Any]:
    base = load_state()
    provisional = deepcopy(base)
    kind = action.get("type")
    current_round = int(provisional.get("current_round", 0))
    next_round = current_round + 1
    if kind == "compare_only":
        # A no-write what-if.  Computing at the next round number makes all
        # currently active seats visible as binding promises under protection.
        trigger = {"type": "compare_only"}
        display_round = current_round
    else:
        display_round = next_round
        trigger = apply_action(provisional, action, next_round)
    previous = previous_assignment_map(base)

    policies = [policy] if policy else ["full_recompute", "protect_promises"]
    out: Dict[str, Any] = {
        "round": display_round,
        "computed_round": next_round,
        "compare_only": kind == "compare_only",
        "trigger": trigger,
        "policies": {},
    }
    for pol in policies:
        result = compute_matching(
            provisional,
            round_no=next_round,
            policy=pol,
            trigger=trigger,
            previous_assignments=previous,
        )
        result["computed_at"] = now_iso()
        attach_event_texts(provisional, result)
        out["policies"][pol] = result
    out["snapshot"] = _snapshot(provisional)
    return out


def commit(action: Dict[str, Any], policy: str) -> Dict[str, Any]:
    if policy not in ("full_recompute", "protect_promises"):
        raise StateError("policy 必须是 full_recompute 或 protect_promises")
    state = load_state()
    next_round = int(state.get("current_round", 0)) + 1
    trigger = apply_action(state, action, next_round)
    previous = previous_assignment_map(state)

    result = compute_matching(
        state,
        round_no=next_round,
        policy=policy,
        trigger=trigger,
        previous_assignments=previous,
    )
    result["computed_at"] = now_iso()
    attach_event_texts(state, result)

    # Supersede every prior seat record. The final assignments become the
    # commitments available to the next protect_promises round.
    for seat in state.get("seats", []):
        if seat.get("released_in_round") is None:
            seat["released_in_round"] = next_round
            seat["release_reason"] = "superseded_by_new_round"
    for cid, a in result["assignments"].items():
        state.setdefault("seats", []).append(
            {
                "candidate_id": cid,
                "program_id": a["accepter_id"],
                "accepter_type": a.get("accepter_type", "program"),
                "host_program_id": a.get("host_program_id"),
                "pool": a["pool"],
                "choice_index": a.get("choice_index"),
                "round_granted": next_round,
                "released_in_round": None,
                "committed_policy": policy,
            }
        )

    record = {
        "round_no": next_round,
        "created_at": now_iso(),
        "policy": policy,
        "action": _action_summary(action),
        "trigger": trigger,
        "result": result,
        "snapshot": _snapshot(state),
    }
    state.setdefault("rounds", []).append(record)
    state["current_round"] = next_round
    save_state(state)
    return record


def _action_summary(action: Dict[str, Any]) -> Dict[str, Any]:
    kind = action.get("type")
    if kind == "add_candidate":
        c = action.get("candidate") or action
        return {"type": kind, "name": c.get("name"), "pool": c.get("pool", "normal")}
    if kind == "edit_capacity":
        return {"type": kind, "accepter_id": action.get("accepter_id"), "new_capacity": action.get("new_capacity")}
    if kind == "withdraw":
        return {"type": kind, "candidate_id": action.get("candidate_id")}
    return {"type": kind}


def explain(round_no: Optional[int], candidate_id: str) -> Dict[str, Any]:
    state = load_state()
    if round_no is None:
        rec = latest_round(state)
    else:
        rec = next((r for r in state.get("rounds", []) if r["round_no"] == round_no), None)
    if rec is None:
        raise StateError("还没有可解释的轮次")
    snapshot = rec.get("snapshot") or _snapshot(state)
    return explain_candidate(snapshot, rec["result"], candidate_id)


def state_for_api(state: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(state)
    rounds = []
    for r in out.get("rounds", []):
        rounds.append(
            {
                "round_no": r["round_no"],
                "created_at": r.get("created_at"),
                "policy": r.get("policy"),
                "action": r.get("action"),
                "trigger": r.get("trigger"),
                "status": r["result"].get("status"),
                "stats": r["result"].get("stats"),
            }
        )
    out["rounds"] = rounds
    latest = latest_round(state)
    out["current_result"] = latest["result"] if latest else None
    out["current_policy"] = latest.get("policy") if latest else None
    out["current_created_at"] = latest.get("created_at") if latest else None
    out["state_path"] = str(STATE_PATH)
    return out
