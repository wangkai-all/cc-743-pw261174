"""Deterministic matching engine.

The engine is deliberately dependency-free and does not know about HTTP.

Allocation is a student-proposing deferred acceptance run in two pool stages:

1. special-pool candidates compete for ordinary special quotas and joint
   programme quotas;
2. unused special / joint seats are converted to normal seats when configured;
3. normal-pool candidates compete for normal quotas plus converted seats.

Under ``protect_promises`` previously granted seats are seeded as untouchable
holders and their capacity is removed before the DA starts.  Any blocking pair
that can only be resolved by breaking one of those seats is reported by the
audit layer as promise-caused rather than silently "fixed".
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from .model import (
    POOL_LABELS,
    TIER_LABELS,
    binding_seats,
    eligibility,
    ensure_tie_vectors,
    joint_map,
    priority_rank,
    program_map,
    quota,
    rank_key,
    tie_draw,
    withdrawn_ids,
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _accepter(state: Dict[str, Any], aid: str) -> Optional[Dict[str, Any]]:
    return program_map(state).get(aid) or joint_map(state).get(aid)


def _is_joint(state: Dict[str, Any], aid: str) -> bool:
    return aid in joint_map(state)


def _rank_tuple(state: Dict[str, Any], cid: str, accepter: Dict[str, Any]) -> Tuple:
    c = {c["id"]: c for c in state["candidates"]}[cid]
    return rank_key(state, c, accepter)


def _rank_public(state: Dict[str, Any], cid: str, accepter: Dict[str, Any]) -> Dict[str, Any]:
    candidates = {c["id"]: c for c in state["candidates"]}
    c = candidates[cid]
    tier = None
    from .model import candidate_tier

    tier = candidate_tier(c, accepter)
    return {
        "tier": tier,
        "tier_label": TIER_LABELS.get(tier, tier),
        "priority_index": priority_rank(c, accepter),
        "draw": tie_draw(state, accepter["id"], cid),
        "candidate_id": cid,
        "key": list(rank_key(state, c, accepter)),
    }


def _worst_holder(state, aid, accepter, holders):
    if not holders:
        return None
    return max(holders, key=lambda cid: _rank_tuple(state, cid, accepter))


# ---------------------------------------------------------------------------
# Generic capacity DA for one stage / accepter family
# ---------------------------------------------------------------------------


def deferred_acceptance(
    state: Dict[str, Any],
    *,
    stage: str,
    participants: List[Dict[str, Any]],
    capacities: Dict[str, int],
    allowed_aids: set,
    promised: Dict[str, List[str]],
    events: List[Dict[str, Any]],
) -> Dict[str, List[str]]:
    """Run candidate-proposing DA.

    ``capacities[aid]`` already includes every seat available in this stage.
    ``promised[aid]`` are untouchable seat holders; only
    ``capacity - len(promised)`` seats are contestable here.  When promises
    exceed capacity the contestable number is zero and the conflict is recorded
    by the caller.
    """
    holders: Dict[str, List[str]] = {aid: [] for aid in capacities}
    new_cap: Dict[str, int] = {}
    for aid, cap in capacities.items():
        new_cap[aid] = max(0, int(cap) - len(promised.get(aid, [])))

    pointers = {c["id"]: 0 for c in participants}
    queue = deque(c["id"] for c in participants)
    cmap = {c["id"]: c for c in participants}

    while queue:
        cid = queue.popleft()
        c = cmap[cid]
        choices = c.get("choices", [])
        pos = pointers[cid]
        if pos >= len(choices):
            continue
        choice = choices[pos]
        pointers[cid] = pos + 1
        aid = choice["program_id"]
        choice_index = pos

        if aid not in allowed_aids or aid not in capacities:
            # Choice belongs to the other pool stage (e.g. normal candidate
            # reaching a special-only joint programme).
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "rejected",
                    "reason_code": "pool_restricted",
                    "reason_detail": {"candidate_pool": c.get("pool", "normal")},
                }
            )
            if pointers[cid] < len(choices):
                queue.append(cid)
            continue

        accepter = _accepter(state, aid)
        if accepter is None:
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "rejected",
                    "reason_code": "unknown_program",
                    "reason_detail": {},
                }
            )
            if pointers[cid] < len(choices):
                queue.append(cid)
            continue

        ok, gate = eligibility(c, accepter)
        if not ok:
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "rejected",
                    "reason_code": "gate_failed",
                    "reason_detail": gate,
                    "rank": _rank_public(state, cid, accepter),
                }
            )
            if pointers[cid] < len(choices):
                queue.append(cid)
            continue

        contestable = new_cap[aid]
        current = holders[aid]
        applicant_key = _rank_tuple(state, cid, accepter)

        if contestable <= 0:
            promise_ids = promised.get(aid, [])
            code = "promise_blocks" if promise_ids else "no_quota"
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "rejected",
                    "reason_code": code,
                    "reason_detail": {
                        "capacity": capacities[aid],
                        "promised": promise_ids,
                    },
                    "rank": _rank_public(state, cid, accepter),
                }
            )
            if pointers[cid] < len(choices):
                queue.append(cid)
            continue

        if len(current) < contestable:
            current.append(cid)
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "held",
                    "reason_code": "tentatively_held",
                    "reason_detail": {},
                    "rank": _rank_public(state, cid, accepter),
                }
            )
            continue

        worst = _worst_holder(state, aid, accepter, current)
        worst_key = _rank_tuple(state, worst, accepter)
        if applicant_key < worst_key:
            current.remove(worst)
            current.append(cid)
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "held",
                    "reason_code": "displaced_lower_priority",
                    "reason_detail": {"displaced": worst},
                    "rank": _rank_public(state, cid, accepter),
                    "cutoff": _rank_public(state, worst, accepter),
                }
            )
            events.append(
                {
                    "candidate_id": worst,
                    "accepter_id": aid,
                    "choice_index": pointers[worst] - 1 if worst in pointers else None,
                    "stage": stage,
                    "outcome": "displaced",
                    "reason_code": "displaced_by_higher_priority",
                    "reason_detail": {"by": cid},
                    "rank": _rank_public(state, worst, accepter),
                    "cutoff": _rank_public(state, cid, accepter),
                }
            )
            if pointers[worst] < len(cmap[worst].get("choices", [])):
                queue.append(worst)
        else:
            events.append(
                {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "choice_index": choice_index,
                    "stage": stage,
                    "outcome": "rejected",
                    "reason_code": "lower_priority_than_cutoff",
                    "reason_detail": {
                        "capacity": capacities[aid],
                        "holders": list(current),
                    },
                    "rank": _rank_public(state, cid, accepter),
                    "cutoff": _rank_public(state, worst, accepter),
                }
            )
            if pointers[cid] < len(choices):
                queue.append(cid)

    return holders


# ---------------------------------------------------------------------------
# Host placement for joint programmes
# ---------------------------------------------------------------------------


def place_joint_hosts(
    state: Dict[str, Any],
    joint: Dict[str, Any],
    admitted: List[str],
    promised_hosts: Dict[str, str],
    events: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Serial dictatorship by joint priority over declared host prefs."""
    conflicts: List[Dict[str, Any]] = []
    host_caps = {h["program_id"]: int(h.get("capacity", 0)) for h in joint.get("hosts", [])}
    remaining = dict(host_caps)
    placement: Dict[str, str] = dict(promised_hosts)
    for pid in promised_hosts.values():
        remaining[pid] = max(0, remaining.get(pid, 0) - 1)

    cmap = candidate_map = {c["id"]: c for c in state["candidates"]}
    ordered = sorted(admitted, key=lambda cid: _rank_tuple(state, cid, joint))
    for cid in ordered:
        c = cmap[cid]
        prefs = list(c.get("host_preferences", []))
        prefs += [h["program_id"] for h in joint.get("hosts", []) if h["program_id"] not in prefs]
        chosen = None
        for pid in prefs:
            if pid not in host_caps:
                continue
            if remaining.get(pid, 0) <= 0:
                continue
            prog = program_map(state).get(pid)
            ok, gate = eligibility(c, prog) if prog else (False, {"code": "missing_host"})
            if not ok:
                continue
            chosen = pid
            break
        if chosen is None:
            conflicts.append(
                {
                    "code": "joint_host_unplaceable",
                    "severity": "blocking",
                    "joint_id": joint["id"],
                    "candidate_id": cid,
                    "message": f"{cid} 被{joint['id']}录取，但所有宿主校区均无容量或未通过门槛",
                }
            )
            continue
        placement[cid] = chosen
        remaining[chosen] -= 1
        events.append(
            {
                "candidate_id": cid,
                "accepter_id": joint["id"],
                "stage": "host_placement",
                "outcome": "host_assigned",
                "reason_code": "host_by_priority",
                "reason_detail": {"host_program_id": chosen},
            }
        )
    return placement, conflicts


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------


def compute_matching(
    base_state: Dict[str, Any],
    *,
    round_no: int,
    policy: str,
    trigger: Optional[Dict[str, Any]] = None,
    previous_assignments: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compute one round. Never mutates ``base_state``."""
    state = deepcopy(base_state)
    ensure_tie_vectors(state)
    conflicts: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []

    withdrawn = withdrawn_ids(state, round_no)
    all_c = state["candidates"]
    active = [c for c in all_c if c["id"] not in withdrawn]
    active_ids = {c["id"] for c in active}

    # Promises only bind under protect_promises.
    promise_seats: List[Dict[str, Any]] = []
    if policy == "protect_promises":
        for s in binding_seats(state, round_no):
            if s["candidate_id"] in withdrawn:
                continue
            promise_seats.append(s)

    pmap = program_map(state)
    jmap = joint_map(state)

    promised_special: Dict[str, List[str]] = {}
    promised_normal: Dict[str, List[str]] = {}
    promised_joint_host: Dict[str, Dict[str, str]] = {}
    for s in promise_seats:
        aid = s["program_id"]
        cid = s["candidate_id"]
        if aid in jmap or s.get("accepter_type") == "joint":
            promised_special.setdefault(aid, []).append(cid)
            if s.get("host_program_id"):
                promised_joint_host.setdefault(aid, {})[cid] = s["host_program_id"]
        elif s.get("pool") == "special":
            promised_special.setdefault(aid, []).append(cid)
        else:
            promised_normal.setdefault(aid, []).append(cid)
        accepter = _accepter(state, aid)
        if accepter is None:
            conflicts.append(
                {
                    "code": "promise_missing_program",
                    "severity": "blocking",
                    "candidate_id": cid,
                    "program_id": aid,
                    "message": f"{cid} 的承诺席位指向不存在的项目 {aid}",
                }
            )
            continue
        ok, gate = eligibility(next(c for c in all_c if c["id"] == cid), accepter)
        if not ok:
            conflicts.append(
                {
                    "code": "promise_gate_failed",
                    "severity": "blocking",
                    "candidate_id": cid,
                    "program_id": aid,
                    "gate": gate,
                    "message": f"承诺学生 {cid} 已不满足 {aid} 的硬门槛，无法同时保留承诺与规则",
                }
            )

    # Promised joint seats may also pin a host campus; check those host caps.
    for jid, host_map in promised_joint_host.items():
        joint = jmap.get(jid)
        if not joint:
            continue
        host_caps = {h["program_id"]: int(h.get("capacity", 0)) for h in joint.get("hosts", [])}
        host_used: Dict[str, List[str]] = {}
        for cid, pid in host_map.items():
            host_used.setdefault(pid, []).append(cid)
        for pid, cids in host_used.items():
            if pid not in host_caps:
                conflicts.append(
                    {
                        "code": "promise_unknown_host",
                        "severity": "blocking",
                        "joint_id": jid,
                        "program_id": pid,
                        "candidate_ids": cids,
                        "message": f"{jid} 的承诺席位指向未配置宿主 {pid}",
                    }
                )
            elif len(cids) > host_caps[pid]:
                conflicts.append(
                    {
                        "code": "host_capacity_oversubscribed",
                        "severity": "blocking",
                        "joint_id": jid,
                        "program_id": pid,
                        "capacity": host_caps[pid],
                        "promised": len(cids),
                        "candidate_ids": cids,
                        "message": f"{jid} 宿主 {pid} 容量 {host_caps[pid]}，但有 {len(cids)} 个保留承诺安置",
                    }
                )

    # ---- Stage 1: special pool (ordinary special quotas + joints) ---------
    special_aids = set(pmap.keys()) | set(jmap.keys())
    special_caps: Dict[str, int] = {pid: quota(p, "special") for pid, p in pmap.items()}
    special_caps.update({jid: int(j.get("capacity", 0)) for jid, j in jmap.items()})

    for aid, cap in special_caps.items():
        n_promised = len(promised_special.get(aid, []))
        if n_promised > cap:
            conflicts.append(
                {
                    "code": "capacity_oversubscribed",
                    "severity": "blocking",
                    "accepter_id": aid,
                    "pool": "special",
                    "capacity": cap,
                    "promised": n_promised,
                    "candidate_ids": promised_special.get(aid, []),
                    "message": f"{aid} 专项容量为 {cap}，但有 {n_promised} 个必须保留的承诺席位",
                }
            )

    special_participants = [c for c in active if c.get("pool", "normal") == "special" and c["id"] not in {x for v in promised_special.values() for x in v}]
    special_holders = deferred_acceptance(
        state,
        stage="special",
        participants=special_participants,
        capacities=special_caps,
        allowed_aids=special_aids,
        promised=promised_special,
        events=events,
    )

    # Joint host placement.
    host_placements: Dict[str, Dict[str, str]] = {}
    for jid, j in jmap.items():
        new_joint = special_holders.get(jid, [])
        placement, jconflicts = place_joint_hosts(
            state, j, new_joint, promised_joint_host.get(jid, {}), events
        )
        host_placements[jid] = placement
        conflicts.extend(jconflicts)

    # ---- Conversion of unused special seats -------------------------------
    conversion_rows: List[Dict[str, Any]] = []
    normal_extra: Dict[str, int] = {pid: 0 for pid in pmap}
    for pid, p in pmap.items():
        cap_s = quota(p, "special")
        used_s = len(promised_special.get(pid, [])) + len(special_holders.get(pid, []))
        leftover = max(0, cap_s - used_s)
        convert = bool(p.get("convert_special_to_normal", True))
        converted = leftover if convert else 0
        if converted:
            normal_extra[pid] += converted
        conversion_rows.append(
            {
                "accepter_id": pid,
                "special_capacity": cap_s,
                "special_used": used_s,
                "unused": leftover,
                "convert": convert,
                "converted_to_normal": converted,
            }
        )

    joint_conversion_rows = []
    for jid, j in jmap.items():
        cap_j = int(j.get("capacity", 0))
        used_j = len(promised_special.get(jid, [])) + len(special_holders.get(jid, []))
        leftover = max(0, cap_j - used_j)
        target = (j.get("convert_to") or {}).get("program_id")
        converted = leftover if target and j.get("convert_unused", True) else 0
        if converted and target:
            normal_extra[target] += converted
        joint_conversion_rows.append(
            {
                "joint_id": jid,
                "capacity": cap_j,
                "used": used_j,
                "unused": leftover,
                "target_program_id": target,
                "convert": bool(j.get("convert_unused", True)),
                "converted_to_normal": converted,
            }
        )
        if leftover and j.get("convert_unused", True) and not target:
            conflicts.append(
                {
                    "code": "conversion_without_target",
                    "severity": "blocking",
                    "joint_id": jid,
                    "message": f"联合专项 {jid} 配置为回流普通池，但缺少目标校区",
                }
            )

    # ---- Stage 2: normal pool --------------------------------------------
    normal_caps: Dict[str, int] = {}
    for pid, p in pmap.items():
        normal_caps[pid] = quota(p, "normal") + normal_extra.get(pid, 0)

    for pid, cap in normal_caps.items():
        n_promised = len(promised_normal.get(pid, []))
        if n_promised > cap:
            conflicts.append(
                {
                    "code": "capacity_oversubscribed",
                    "severity": "blocking",
                    "accepter_id": pid,
                    "pool": "normal",
                    "capacity": cap,
                    "promised": n_promised,
                    "candidate_ids": promised_normal.get(pid, []),
                    "message": f"{pid} 普通池有效容量为 {cap}，但有 {n_promised} 个必须保留的承诺席位",
                }
            )

    normal_participants = [c for c in active if c.get("pool", "normal") == "normal" and c["id"] not in {x for v in promised_normal.values() for x in v}]
    normal_holders = deferred_acceptance(
        state,
        stage="normal",
        participants=normal_participants,
        capacities=normal_caps,
        allowed_aids=set(pmap.keys()),
        promised=promised_normal,
        events=events,
    )

    # ---- Assemble assignments --------------------------------------------
    assignments: Dict[str, Dict[str, Any]] = {}
    for s in promise_seats:
        cid = s["candidate_id"]
        if cid in assignments:
            conflicts.append(
                {
                    "code": "duplicate_promise",
                    "severity": "blocking",
                    "candidate_id": cid,
                    "message": f"{cid} 同时存在多个有效承诺席位",
                }
            )
        aid = s["program_id"]
        assignments[cid] = {
            "candidate_id": cid,
            "accepter_id": aid,
            "accepter_type": "joint" if aid in jmap or s.get("accepter_type") == "joint" else "program",
            "host_program_id": s.get("host_program_id"),
            "pool": s.get("pool", "normal"),
            "choice_index": s.get("choice_index"),
            "source": "promise",
            "round_granted": s.get("round_granted"),
        }

    for aid, cids in special_holders.items():
        is_joint = aid in jmap
        for cid in cids:
            c = next(x for x in special_participants if x["id"] == cid)
            idx = next((i for i, ch in enumerate(c["choices"]) if ch["program_id"] == aid), None)
            assignments[cid] = {
                "candidate_id": cid,
                "accepter_id": aid,
                "accepter_type": "joint" if is_joint else "program",
                "host_program_id": host_placements.get(aid, {}).get(cid) if is_joint else None,
                "pool": "special",
                "choice_index": idx,
                "source": "matched",
            }

    for pid, cids in normal_holders.items():
        for cid in cids:
            c = next(x for x in normal_participants if x["id"] == cid)
            idx = next((i for i, ch in enumerate(c["choices"]) if ch["program_id"] == pid), None)
            assignments[cid] = {
                "candidate_id": cid,
                "accepter_id": pid,
                "accepter_type": "program",
                "host_program_id": None,
                "pool": "normal",
                "choice_index": idx,
                "source": "matched",
            }

    unmatched = [c["id"] for c in active if c["id"] not in assignments]

    # Persist rank snapshot on each assignment for easy rendering.
    for cid, a in assignments.items():
        accepter = _accepter(state, a["accepter_id"])
        c = next(x for x in all_c if x["id"] == cid)
        if accepter is not None:
            a["priority"] = _rank_public(state, cid, accepter)
            ok, gate = eligibility(c, accepter)
            a["eligible"] = ok
            a["gate"] = gate

    from .audit import build_audit

    audit = build_audit(
        state=state,
        assignments=assignments,
        unmatched=unmatched,
        policy=policy,
        capacities={
            "special": special_caps,
            "normal": normal_caps,
        },
        promised={"special": promised_special, "normal": promised_normal},
        conversion=conversion_rows,
        joint_conversion=joint_conversion_rows,
        host_placements=host_placements,
        conflicts=conflicts,
        round_no=round_no,
    )

    from .chains import build_chains

    chains = build_chains(
        previous_assignments or {},
        {cid: a["accepter_id"] for cid, a in assignments.items()},
        trigger or {},
        state,
    )

    stats = {
        "active": len(active),
        "assigned": len(assignments),
        "unmatched": len(unmatched),
        "promises": len(promise_seats),
        "blocking_pairs": len(audit["stability"]["blocking_pairs"]),
        "promise_blocking_pairs": len(audit["stability"]["promise_blocking_pairs"]),
        "blocking_coalitions": len(audit["stability"]["blocking_coalitions"]),
        "conflicts": len(conflicts),
    }

    return {
        "round": round_no,
        "policy": policy,
        "trigger": trigger,
        "status": "conflict" if conflicts else "ok",
        "assignments": assignments,
        "unmatched": unmatched,
        "events": events,
        "conversion": conversion_rows,
        "joint_conversion": joint_conversion_rows,
        "host_placements": host_placements,
        "capacity_audit": audit["capacity"],
        "stability": audit["stability"],
        "conflicts": conflicts,
        "chains": chains,
        "stats": stats,
        "computed_at": None,
    }
