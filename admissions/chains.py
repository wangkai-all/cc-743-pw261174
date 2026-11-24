"""Vacancy / supplementary-admission chain reconstruction.

Given the assignment map of the previous round and the new round, a withdrawal
seeds a vacant programme.  Following the student who now holds that seat back
to the seat they vacated produces a readable top-up chain.  Full recomputation
can produce long chains; promise protection usually truncates them because
existing seat holders are not allowed to move.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_chains(
    previous: Dict[str, str],
    current: Dict[str, str],
    trigger: Dict[str, Any],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    pmap = {p["id"]: p for p in state.get("programs", [])}
    jmap = {j["id"]: j for j in state.get("joint_programs", [])}

    def name(aid: Optional[str]) -> str:
        if not aid:
            return "空"
        a = pmap.get(aid) or jmap.get(aid)
        return a.get("name", aid) if a else aid

    # programme -> candidates currently there who were elsewhere / unmatched
    new_at: Dict[str, List[str]] = {}
    for cid, aid in current.items():
        if previous.get(cid) != aid:
            new_at.setdefault(aid, []).append(cid)

    roots: List[Dict[str, Any]] = []
    for w in trigger.get("withdrawals", []) or []:
        old = previous.get(w)
        if old:
            roots.append(
                {
                    "program_id": old,
                    "kind": "withdrawal",
                    "label": f"{w} 撤回，空出 {name(old)} 的席位",
                    "candidate_id": w,
                }
            )
    for edit in trigger.get("capacity_edits", []) or []:
        aid = edit.get("program_id")
        delta = int(edit.get("new_capacity", 0)) - int(edit.get("old_capacity", 0))
        if delta > 0 and aid:
            roots.append(
                {
                    "program_id": aid,
                    "kind": "capacity_increase",
                    "label": f"{name(aid)} 容量增加 {delta}，出现新席位",
                    "delta": delta,
                }
            )

    chains: List[Dict[str, Any]] = []
    used_edges = set()
    for root in roots:
        chain_id = len(chains) + 1
        steps: List[Dict[str, Any]] = []
        pid = root["program_id"]
        visited_programs = set()
        while pid and pid not in visited_programs:
            visited_programs.add(pid)
            entrants = [c for c in new_at.get(pid, []) if (c, pid) not in used_edges]
            if not entrants:
                steps.append(
                    {
                        "program_id": pid,
                        "program_name": name(pid),
                        "admitted_candidate_id": None,
                        "note": "没有候选人补上，席位空置",
                    }
                )
                break
            # Deterministic pick: first entrant. DA outputs are stable; when
            # several entrants exist (capacity jump) report each as branch.
            cid = entrants[0]
            used_edges.add((cid, pid))
            prev_pid = previous.get(cid)
            steps.append(
                {
                    "program_id": pid,
                    "program_name": name(pid),
                    "admitted_candidate_id": cid,
                    "previous_program_id": prev_pid,
                    "previous_program_name": name(prev_pid) if prev_pid else None,
                    "note": (
                        f"{cid} 从此前未录取状态补入"
                        if not prev_pid
                        else f"{cid} 从 {name(prev_pid)} 转入，原席位空出"
                    ),
                }
            )
            if not prev_pid or prev_pid == pid:
                break
            pid = prev_pid
        chains.append({"id": chain_id, "root": root, "steps": steps})

    # Always expose raw moves so the UI can render policy diffs even when no
    # trigger-rooted chain exists.
    moves = []
    for cid, aid in current.items():
        old = previous.get(cid)
        if old != aid:
            moves.append(
                {
                    "candidate_id": cid,
                    "from": old,
                    "from_name": name(old) if old else None,
                    "to": aid,
                    "to_name": name(aid),
                }
            )
    withdrawn = set(trigger.get("withdrawals", []) or [])
    for cid, old in previous.items():
        if cid in withdrawn:
            continue
        if cid not in current:
            moves.append(
                {
                    "candidate_id": cid,
                    "from": old,
                    "from_name": name(old),
                    "to": None,
                    "to_name": None,
                }
            )
    return {"chains": chains, "moves": moves}
