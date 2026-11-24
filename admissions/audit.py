"""Capacity and stability audits.

The stability check is an independent re-implementation of the blocking-pair
definition; it does not trust the DA's own bookkeeping.  That independence is
what lets the exhaustive verifier and the UI audit agree.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .model import (
    TIER_LABELS,
    candidate_tier,
    eligibility,
    joint_map,
    priority_rank,
    program_map,
    quota,
    rank_key,
    tie_draw,
    withdrawn_ids,
)


def rank_public(state: Dict[str, Any], cid: str, accepter: Dict[str, Any]) -> Dict[str, Any]:
    c = next(x for x in state["candidates"] if x["id"] == cid)
    tier = candidate_tier(c, accepter)
    return {
        "tier": tier,
        "tier_label": TIER_LABELS.get(tier, tier),
        "priority_index": priority_rank(c, accepter),
        "draw": tie_draw(state, accepter["id"], cid),
        "candidate_id": cid,
        "key": list(rank_key(state, c, accepter)),
    }


def _accepter(state, aid):
    return program_map(state).get(aid) or joint_map(state).get(aid)


def build_audit(
    *,
    state: Dict[str, Any],
    assignments: Dict[str, Dict[str, Any]],
    unmatched: List[str],
    policy: str,
    capacities: Dict[str, Dict[str, int]],
    promised: Dict[str, Dict[str, List[str]]],
    conversion: List[Dict[str, Any]],
    joint_conversion: List[Dict[str, Any]],
    host_placements: Dict[str, Dict[str, str]],
    conflicts: List[Dict[str, Any]],
    round_no: int,
) -> Dict[str, Any]:
    pmap = program_map(state)
    jmap = joint_map(state)
    normal_caps = capacities["normal"]
    special_caps = capacities["special"]

    normal_holders: Dict[str, List[str]] = {}
    special_holders: Dict[str, List[str]] = {}
    for cid, a in assignments.items():
        aid = a["accepter_id"]
        if a["pool"] == "normal":
            normal_holders.setdefault(aid, []).append(cid)
        else:
            special_holders.setdefault(aid, []).append(cid)

    promise_ids = {cid for cid, a in assignments.items() if a.get("source") == "promise"}

    # Effective special capacities after conversion closes the unused seats.
    special_effective: Dict[str, int] = {}
    conv_by_pid = {r["accepter_id"]: r for r in conversion}
    for pid, p in pmap.items():
        base = quota(p, "special")
        used = len(special_holders.get(pid, []))
        if bool(p.get("convert_special_to_normal", True)):
            # Filled special seats stay special; unfilled base seats are no
            # longer reserved once converted.  Never expand the effective
            # capacity to match an over-capacity promise, otherwise the
            # capacity audit would explain away a real violation.
            special_effective[pid] = min(base, used)
        else:
            special_effective[pid] = base
    jconv_by_id = {r["joint_id"]: r for r in joint_conversion}
    for jid, j in jmap.items():
        base = int(j.get("capacity", 0))
        used = len(special_holders.get(jid, []))
        row = jconv_by_id.get(jid, {})
        if row.get("converted_to_normal", 0):
            special_effective[jid] = used
        else:
            special_effective[jid] = base

    # ---------------- capacity rows ----------------
    cap_rows: List[Dict[str, Any]] = []
    for pid, p in pmap.items():
        qn = quota(p, "normal")
        qs = quota(p, "special")
        n_used = len(normal_holders.get(pid, []))
        s_used = len(special_holders.get(pid, []))
        n_promised = len(promised["normal"].get(pid, []))
        s_promised = len(promised["special"].get(pid, []))
        converted_in = max(0, normal_caps.get(pid, qn) - qn)
        row = conv_by_pid.get(pid, {})
        reserved_unused = 0
        if not bool(p.get("convert_special_to_normal", True)):
            reserved_unused = max(0, qs - s_used)
        over_normal = n_used > normal_caps.get(pid, qn)
        over_special = s_used > special_effective.get(pid, qs)
        if over_normal:
            conflicts.append(
                {
                    "code": "capacity_violation",
                    "severity": "blocking",
                    "accepter_id": pid,
                    "pool": "normal",
                    "used": n_used,
                    "capacity": normal_caps.get(pid, qn),
                    "message": f"{pid} 普通池录取 {n_used} 人，超过有效容量 {normal_caps.get(pid, qn)}",
                }
            )
        if over_special:
            conflicts.append(
                {
                    "code": "capacity_violation",
                    "severity": "blocking",
                    "accepter_id": pid,
                    "pool": "special",
                    "used": s_used,
                    "capacity": special_effective.get(pid, qs),
                    "message": f"{pid} 专项池录取 {s_used} 人，超过容量 {special_effective.get(pid, qs)}",
                }
            )
        cap_rows.append(
            {
                "accepter_id": pid,
                "name": p.get("name", pid),
                "kind": "program",
                "normal_base": qn,
                "special_base": qs,
                "normal_effective_capacity": normal_caps.get(pid, qn),
                "special_effective_capacity": special_effective.get(pid, qs),
                "normal_used": n_used,
                "special_used": s_used,
                "normal_promised": n_promised,
                "special_promised": s_promised,
                "converted_in": converted_in,
                "special_unused": row.get("unused", max(0, qs - s_used)),
                "reserved_unused": reserved_unused,
                "normal_free": normal_caps.get(pid, qn) - n_used,
                "special_free": special_effective.get(pid, qs) - s_used,
                "over_capacity": over_normal or over_special,
            }
        )

    for jid, j in jmap.items():
        base = int(j.get("capacity", 0))
        used = len(special_holders.get(jid, []))
        eff = special_effective.get(jid, base)
        if used > eff:
            conflicts.append(
                {
                    "code": "capacity_violation",
                    "severity": "blocking",
                    "accepter_id": jid,
                    "pool": "special",
                    "used": used,
                    "capacity": eff,
                    "message": f"联合专项 {jid} 录取 {used} 人，超过容量 {eff}",
                }
            )
        cap_rows.append(
            {
                "accepter_id": jid,
                "name": j.get("name", jid),
                "kind": "joint",
                "normal_base": 0,
                "special_base": base,
                "normal_effective_capacity": 0,
                "special_effective_capacity": eff,
                "normal_used": 0,
                "special_used": used,
                "normal_promised": 0,
                "special_promised": len(promised["special"].get(jid, [])),
                "converted_in": 0,
                "special_unused": max(0, base - used),
                "reserved_unused": 0 if jconv_by_id.get(jid, {}).get("converted_to_normal") else max(0, base - used),
                "normal_free": 0,
                "special_free": eff - used,
                "over_capacity": used > eff,
                "hosts": j.get("hosts", []),
                "host_placement": host_placements.get(jid, {}),
            }
        )

    # Individual rationality / choice membership checks.
    cmap = {c["id"]: c for c in state["candidates"]}
    withdrawn = withdrawn_ids(state, round_no)
    for cid, a in assignments.items():
        c = cmap.get(cid)
        if c is None:
            conflicts.append({"code": "orphan_assignment", "severity": "blocking", "candidate_id": cid, "message": f"录取了不存在的学生 {cid}"})
            continue
        if cid in withdrawn:
            conflicts.append({"code": "withdrawn_assigned", "severity": "blocking", "candidate_id": cid, "message": f"已撤回学生 {cid} 仍有席位"})
        aids = [ch["program_id"] for ch in c.get("choices", [])]
        if a["accepter_id"] not in aids:
            conflicts.append(
                {
                    "code": "assigned_without_choice",
                    "severity": "blocking",
                    "candidate_id": cid,
                    "accepter_id": a["accepter_id"],
                    "message": f"{cid} 被 {a['accepter_id']} 录取，但该志愿已不在其志愿表中",
                }
            )

    capacity_audit = {
        "rows": cap_rows,
        "total_base_capacity": sum(quota(p, "normal") + quota(p, "special") for p in state["programs"])
        + sum(int(j.get("capacity", 0)) for j in state.get("joint_programs", [])),
        "total_assigned": len(assignments),
        "over_capacity": any(r["over_capacity"] for r in cap_rows),
        "all_within_capacity": not any(r["over_capacity"] for r in cap_rows),
    }

    # ---------------- stability ----------------
    blocking_pairs: List[Dict[str, Any]] = []
    checked = 0
    # A candidate can strictly outrank worst holders at several earlier
    # choices.  Retain every such edge so cycle enumeration is not dependent
    # on which blocking pair happened to be visited last.
    edge_options: Dict[str, List[str]] = {}

    for c in state["candidates"]:
        cid = c["id"]
        if cid in withdrawn:
            continue
        a = assignments.get(cid)
        current_aid = a["accepter_id"] if a else None
        aids = [ch["program_id"] for ch in c.get("choices", [])]
        current_index = aids.index(current_aid) if current_aid in aids else None
        if a and current_index is None and policy == "protect_promises":
            conflicts.append(
                {
                    "code": "promise_not_in_choices",
                    "severity": "blocking",
                    "candidate_id": cid,
                    "accepter_id": current_aid,
                    "message": f"承诺学生 {cid} 的志愿表已不含其保留席位 {current_aid}",
                }
            )
        for idx, aid in enumerate(aids):
            checked += 1
            if current_index is not None and idx >= current_index:
                break
            accepter = _accepter(state, aid)
            if accepter is None:
                continue
            ok, gate = eligibility(c, accepter)
            if not ok:
                continue

            is_joint = aid in jmap
            if c.get("pool", "normal") == "normal":
                if is_joint:
                    continue  # joint programmes are special-only
                cap = normal_caps.get(aid, 0)
                holders = normal_holders.get(aid, [])
                pool = "normal"
            else:
                cap = special_effective.get(aid, special_caps.get(aid, 0))
                holders = special_holders.get(aid, [])
                pool = "special"

            if len(holders) < cap:
                pair = {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "accepter_name": accepter.get("name", aid),
                    "choice_index": idx,
                    "pool": pool,
                    "kind": "vacancy",
                    "current_accepter_id": current_aid,
                    "candidate_rank": rank_public(state, cid, accepter),
                    "cutoff_rank": None,
                    "worst_holder_id": None,
                }
                _classify_promise(pair, promise_ids, assignments, policy)
                blocking_pairs.append(pair)
                continue

            worst = max(holders, key=lambda h: rank_key(state, cmap[h], accepter))
            if rank_key(state, c, accepter) < rank_key(state, cmap[worst], accepter):
                pair = {
                    "candidate_id": cid,
                    "accepter_id": aid,
                    "accepter_name": accepter.get("name", aid),
                    "choice_index": idx,
                    "pool": pool,
                    "kind": "priority",
                    "current_accepter_id": current_aid,
                    "candidate_rank": rank_public(state, cid, accepter),
                    "cutoff_rank": rank_public(state, worst, accepter),
                    "worst_holder_id": worst,
                }
                _classify_promise(pair, promise_ids, assignments, policy)
                blocking_pairs.append(pair)
                options = edge_options.setdefault(cid, [])
                if worst not in options:
                    options.append(worst)

    promise_pairs = [p for p in blocking_pairs if p["promise_caused"]]
    structural_pairs = [p for p in blocking_pairs if not p["promise_caused"]]

    coalitions = _find_cycles(edge_options, promise_ids, state, assignments)
    promise_coalitions = [k for k in coalitions if k["promise_caused"]]
    structural_coalitions = [k for k in coalitions if not k["promise_caused"]]

    capacity_blocking_conflicts = [c for c in conflicts if c.get("severity") == "blocking"]
    stable = not blocking_pairs and not coalitions and not capacity_blocking_conflicts
    constrained_stable = not structural_pairs and not structural_coalitions and not capacity_blocking_conflicts

    stability = {
        "stable": stable,
        "constrained_stable": constrained_stable,
        "checked_choice_pairs": checked,
        "blocking_pairs": blocking_pairs,
        "promise_blocking_pairs": promise_pairs,
        "structural_blocking_pairs": structural_pairs,
        "blocking_coalitions": coalitions,
        "promise_blocking_coalitions": promise_coalitions,
        "structural_blocking_coalitions": structural_coalitions,
        "definition": (
            "若学生更偏好某项目，且该项目有空位、或该学生的校区优先序严格高于"
            "当前末位录取者，则构成可阻挡组合。承诺政策下，只有必须打破既有"
            "承诺才能消除的组合被标记为 promise_caused。"
        ),
    }
    return {"capacity": capacity_audit, "stability": stability}


def _classify_promise(pair: Dict[str, Any], promise_ids: set, assignments: Dict[str, Any], policy: str) -> None:
    cid = pair["candidate_id"]
    worst = pair.get("worst_holder_id")
    involved = []
    caused = False
    if policy == "protect_promises":
        if cid in promise_ids:
            caused = True
            involved.append(cid)
        if worst and worst in promise_ids:
            caused = True
            involved.append(worst)
    pair["promise_caused"] = caused
    pair["involved_promise_candidates"] = involved


def _find_cycles(
    edge_options: Dict[str, List[str]],
    promise_ids: set,
    state: Dict[str, Any],
    assignments: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Find short directed cycles c -> holder(c). Each is a blocking coalition."""
    cycles: List[Tuple[str, ...]] = []
    seen_canonical = set()

    def canonical(cyc: Tuple[str, ...]) -> Tuple[str, ...]:
        rots = []
        for i in range(len(cyc)):
            rots.append(cyc[i:] + cyc[:i])
        return min(rots)

    for start in edge_options:
        # DFS up to length 4, branching over every blocking edge option.
        stack: List[Tuple[str, Tuple[str, ...]]] = [(start, (start,))]
        while stack:
            node, path = stack.pop()
            for nxt in edge_options.get(node, []):
                if nxt == start and len(path) >= 2:
                    cyc = canonical(path)
                    if cyc not in seen_canonical:
                        seen_canonical.add(cyc)
                        cycles.append(cyc)
                    continue
                if nxt in path or len(path) >= 4:
                    continue
                stack.append((nxt, path + (nxt,)))

    cmap = {c["id"]: c for c in state["candidates"]}
    amap = {a["id"]: a for a in state.get("programs", []) + state.get("joint_programs", [])}
    out = []
    for cyc in cycles[:30]:
        steps = []
        for i, cid in enumerate(cyc):
            target_holder = cyc[(i + 1) % len(cyc)]
            target_aid = assignments.get(target_holder, {}).get("accepter_id")
            current_aid = assignments.get(cid, {}).get("accepter_id")
            target = amap.get(target_aid or "", {})
            steps.append(
                {
                    "candidate_id": cid,
                    "current_accepter_id": current_aid,
                    "target_accepter_id": target_aid,
                    "target_accepter_name": target.get("name", target_aid),
                    "would_replace": target_holder,
                }
            )
        caused = any(cid in promise_ids for cid in cyc)
        readable = " → ".join(
            f"{cid} 升入 {steps[i]['target_accepter_name'] or steps[i]['target_accepter_id']}（替换 {steps[i]['would_replace']}）"
            for i, cid in enumerate(cyc)
        )
        out.append(
            {
                "members": list(cyc),
                "length": len(cyc),
                "promise_caused": caused,
                "steps": steps,
                "message": readable + f"；形成 {len(cyc)} 人交换联盟，所有人均升到更前志愿",
            }
        )
    return out
