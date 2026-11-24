"""Domain helpers and validation for the fictional admissions matcher.

The whole application state is one JSON document. Nothing here depends on the
web layer, so the engine and exhaustive verifier can be tested in isolation.

Domain rules (kept explicit, see README.md):
* A candidate belongs to one pool: normal or special. Ordinary campuses expose
  a normal quota and a special quota; a candidate only uses their own pool.
  A joint programme's seats are special seats shared by host campuses.
* Campus priorities are tier lists (sibling / legacy / diversity). A candidate
  declares at which campus they hold which status. Within one tier the
  persisted lottery draw decides; the draw is derived from the saved seed the
  first time it is needed and is never redrawn.
* Unused special seats return to the normal pool when configured to convert.
  Conversion happens inside the round before normal-pool competition, so it
  changes who wins later seats.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

TIERS = ("sibling", "legacy", "diversity")
TIER_LABELS = {
    "sibling": "兄弟姐妹在读",
    "legacy": "教职工子女",
    "diversity": "就近/多元",
    "none": "普通",
}
POOLS = ("normal", "special")
POOL_LABELS = {"normal": "普通池", "special": "专项池"}
POLICIES = ("full_recompute", "protect_promises")
POLICY_LABELS = {
    "full_recompute": "整体重算",
    "protect_promises": "保留承诺（增量补录）",
}
SUBJECT_LABELS = {"maths": "数学", "science": "科学", "language": "语文"}


class StateError(ValueError):
    """The request or stored state is structurally invalid."""


def sha_hex(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def lottery_draw(seed_text: str, accepter_id: str, candidate_id: str) -> float:
    """Deterministic [0, 1) draw from the saved seed.

    48 bits are plenty: a collision would still be resolved by candidate id,
    and the value stays safely below 2**53 for JSON/JavaScript consumers.
    """
    h = sha_hex("lottery", seed_text, accepter_id, candidate_id)
    return int(h[:12], 16) / float(16 ** 12)


def ensure_tie_vectors(state: Dict[str, Any]) -> None:
    """Materialise and persist every draw used by current accepters.

    Existing values are never overwritten, which is the "saved draw, not a new
    shuffle on every calculation" guarantee.
    """
    vectors = state.setdefault("tie_vectors", {})
    accepters = [p["id"] for p in state.get("programs", [])]
    accepters += [j["id"] for j in state.get("joint_programs", [])]
    seed_text = state.get("seed_text", "seed")
    for aid in accepters:
        table = vectors.setdefault(aid, {})
        for c in state.get("candidates", []):
            table.setdefault(c["id"], lottery_draw(seed_text, aid, c["id"]))


def tie_draw(state: Dict[str, Any], accepter_id: str, candidate_id: str) -> float:
    table = state.setdefault("tie_vectors", {}).setdefault(accepter_id, {})
    value = table.get(candidate_id)
    if value is None:
        value = lottery_draw(state.get("seed_text", "seed"), accepter_id, candidate_id)
        table[candidate_id] = value
    return float(value)


def candidate_tier(candidate: Dict[str, Any], accepter: Dict[str, Any]) -> str:
    """Best priority tier the candidate holds at this accepter."""
    order = accepter.get("priority_order") or list(TIERS)
    held = candidate.get("priorities", {}) or {}
    for tier in order:
        targets = held.get(tier, []) or []
        if accepter["id"] in targets:
            return tier
    return "none"


def priority_rank(candidate: Dict[str, Any], accepter: Dict[str, Any]) -> int:
    order = accepter.get("priority_order") or list(TIERS)
    tier = candidate_tier(candidate, accepter)
    return order.index(tier) if tier in order else len(order)


def rank_key(state: Dict[str, Any], candidate: Dict[str, Any], accepter: Dict[str, Any]) -> Tuple[int, float, str]:
    """Strict, deterministic ordering key for an accepter."""
    return (
        priority_rank(candidate, accepter),
        tie_draw(state, accepter["id"], candidate["id"]),
        candidate["id"],
    )


def eligibility(candidate: Dict[str, Any], accepter: Dict[str, Any]) -> Tuple[bool, Dict[str, Any] | None]:
    """Hard gate check. Returns (ok, failure detail)."""
    for subject, minimum in (accepter.get("requires") or {}).items():
        score = (candidate.get("scores") or {}).get(subject)
        if score is None or score < minimum:
            return False, {
                "code": "gate",
                "subject": subject,
                "subject_label": SUBJECT_LABELS.get(subject, subject),
                "minimum": minimum,
                "score": score,
            }
    return True, None


def program_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {p["id"]: p for p in state.get("programs", [])}


def joint_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {j["id"]: j for j in state.get("joint_programs", [])}


def accepter_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out = program_map(state)
    out.update(joint_map(state))
    return out


def candidate_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["id"]: c for c in state.get("candidates", [])}


def quota(program: Dict[str, Any], pool: str) -> int:
    return int((program.get("quotas") or {}).get(pool, 0))


def withdrawn_ids(state: Dict[str, Any], round_no: int) -> set:
    out = set()
    for w in state.get("withdrawals", []):
        if int(w.get("round", 0)) <= round_no:
            out.add(w["candidate_id"])
    return out


def binding_seats(state: Dict[str, Any], round_no: int) -> List[Dict[str, Any]]:
    """Seats granted in earlier rounds and not released."""
    out = []
    for s in state.get("seats", []):
        if s.get("released_in_round") is not None:
            continue
        if int(s.get("round_granted", 0)) >= round_no:
            continue
        out.append(s)
    return out


def validate_state(state: Dict[str, Any]) -> None:
    for key in ("seed_text", "candidates", "programs"):
        if key not in state:
            raise StateError(f"state is missing {key!r}")
    pids = {p["id"] for p in state["programs"]}
    jids = {j["id"] for j in state.get("joint_programs", [])}
    aids = pids | jids
    cids = set()
    for c in state["candidates"]:
        if c["id"] in cids:
            raise StateError(f"duplicate candidate {c['id']}")
        cids.add(c["id"])
        if c.get("pool", "normal") not in POOLS:
            raise StateError(f"candidate {c['id']} has unknown pool")
        seen = set()
        for ch in c.get("choices", []):
            aid = ch["program_id"]
            if aid not in aids:
                raise StateError(f"candidate {c['id']} chooses unknown accepter {aid}")
            if aid in seen:
                raise StateError(f"candidate {c['id']} lists {aid} twice")
            seen.add(aid)
        for tier, targets in (c.get("priorities") or {}).items():
            if tier not in TIERS:
                raise StateError(f"candidate {c['id']} has unknown tier {tier}")
            for tid in targets or []:
                if tid not in aids:
                    raise StateError(f"candidate {c['id']} priority points to unknown {tid}")
    for p in state["programs"]:
        for pool, q in (p.get("quotas") or {}).items():
            if pool not in POOLS or int(q) < 0:
                raise StateError(f"program {p['id']} has invalid quota {pool}={q}")
    for j in state.get("joint_programs", []):
        if int(j.get("capacity", 0)) < 0:
            raise StateError(f"joint programme {j['id']} has negative capacity")
        for h in j.get("hosts", []):
            if h["program_id"] not in pids:
                raise StateError(f"joint {j['id']} host {h['program_id']} missing")
            if int(h.get("capacity", 0)) < 0:
                raise StateError("host capacity cannot be negative")
        target = (j.get("convert_to") or {}).get("program_id")
        if target and target not in pids:
            raise StateError(f"joint {j['id']} converts to missing program {target}")


def new_id(state: Dict[str, Any], prefix: str) -> str:
    n = int(state.setdefault("counters", {}).get(prefix, 0)) + 1
    state["counters"][prefix] = n
    return f"{prefix}{n:02d}"
