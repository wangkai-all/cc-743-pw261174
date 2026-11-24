"""Human-readable explanations for decisions and rejection reasons."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .model import POOL_LABELS, TIER_LABELS, candidate_tier, eligibility, rank_key, tie_draw


def _cname(state: Dict[str, Any], cid: Optional[str]) -> str:
    if not cid:
        return "—"
    c = next((x for x in state["candidates"] if x["id"] == cid), None)
    return f"{c['name']}（{cid}）" if c else cid


def _aname(state: Dict[str, Any], aid: Optional[str]) -> str:
    if not aid:
        return "—"
    a = next((p for p in state.get("programs", []) if p["id"] == aid), None)
    if a is None:
        a = next((j for j in state.get("joint_programs", []) if j["id"] == aid), None)
    return a.get("name", aid) if a else aid


def rank_text(rank: Optional[Dict[str, Any]]) -> str:
    if not rank:
        return "无"
    tier = rank.get("tier_label") or TIER_LABELS.get(rank.get("tier"), rank.get("tier"))
    draw = rank.get("draw")
    draw_s = f"{draw:.4f}" if isinstance(draw, (int, float)) else "—"
    return f"{tier}；保存抽签号 {draw_s}"


def reason_text(state: Dict[str, Any], ev: Dict[str, Any]) -> str:
    code = ev.get("reason_code")
    d = ev.get("reason_detail") or {}
    rank = ev.get("rank") or {}
    if code == "pool_restricted":
        pool = d.get("candidate_pool", "normal")
        return f"池别不符：你属于{POOL_LABELS.get(pool, pool)}，该志愿席位不向此池别开放。"
    if code == "gate_failed":
        score = d.get("score")
        score_s = "缺成绩" if score is None else f"{score} 分"
        return f"未通过硬门槛：{d.get('subject_label', d.get('subject'))} {score_s}，要求至少 {d.get('minimum')} 分。"
    if code == "promise_blocks":
        ids = d.get("promised") or []
        return f"没有可竞争席位：{len(ids)} 个席位是此前已接受并保留的承诺（{', '.join(ids)}），承诺政策下不能挤出。"
    if code == "no_quota":
        return "该项目在对应池别没有可竞争名额（容量为 0 或已全部占用）。"
    if code == "lower_priority_than_cutoff":
        cutoff = ev.get("cutoff") or {}
        return (
            "校区优先序低于末位录取者：你的顺序为「"
            + rank_text(rank)
            + "」；末位为 "
            + _cname(state, cutoff.get("candidate_id"))
            + "，顺序为「"
            + rank_text(cutoff)
            + "」。"
        )
    if code == "displaced_by_higher_priority":
        cutoff = ev.get("cutoff") or {}
        return "曾被暂时接收，随后因更高优先序的 " + _cname(state, d.get("by")) + " 进入而被挤出。挤出者顺序：" + rank_text(cutoff)
    if code == "displaced_lower_priority":
        return "你以更高优先序替换了末位候选人 " + _cname(state, d.get("displaced")) + "。"
    if code in ("tentatively_held",):
        return "在该轮申请中被暂时保留，等待后续申请结束。"
    if code == "host_by_priority":
        return f"按联合专项优先序和你的宿主校区志愿，分配到 {_aname(state, d.get('host_program_id'))}。"
    return code or "已处理"


def attach_event_texts(state: Dict[str, Any], result: Dict[str, Any]) -> None:
    for ev in result.get("events", []):
        ev["reason_text"] = reason_text(state, ev)


def explain_candidate(state: Dict[str, Any], result: Dict[str, Any], cid: str) -> Dict[str, Any]:
    c = next(x for x in state["candidates"] if x["id"] == cid)
    a = result["assignments"].get(cid)
    aids = [ch["program_id"] for ch in c.get("choices", [])]
    current_index = aids.index(a["accepter_id"]) if a and a["accepter_id"] in aids else None

    # Holder lookup from final assignments.
    holders: Dict[str, List[str]] = {}
    for ocid, oa in result["assignments"].items():
        holders.setdefault(f"{oa['accepter_id']}:{oa['pool']}", []).append(ocid)

    rows = []
    for idx, aid in enumerate(aids):
        accepter = next((p for p in state.get("programs", []) if p["id"] == aid), None)
        is_joint = accepter is None
        if is_joint:
            accepter = next(j for j in state["joint_programs"] if j["id"] == aid)
        row: Dict[str, Any] = {
            "choice_index": idx,
            "accepter_id": aid,
            "accepter_name": accepter.get("name", aid),
            "is_joint": is_joint,
        }

        if a and idx == current_index:
            row["status"] = "admitted"
            row["status_label"] = "最终录取"
            row["pool"] = a["pool"]
            row["source"] = a.get("source")
            row["host_program_id"] = a.get("host_program_id")
            if a.get("host_program_id"):
                row["host_name"] = _aname(state, a["host_program_id"])
            row["priority"] = a.get("priority")
            rows.append(row)
            continue

        if current_index is not None and idx > current_index:
            row["status"] = "not_considered"
            row["status_label"] = "未再考虑"
            row["explanation"] = "你已被更靠前的志愿暂时/最终录取，DA 不会再向后续志愿投递。"
            rows.append(row)
            continue

        # This is a higher choice that did not win.
        if c.get("pool", "normal") == "normal" and is_joint:
            row.update(
                status="rejected",
                status_label="池别不符",
                reason_code="pool_restricted",
                explanation="联合科创专项只向专项池学生开放，普通池学生不能占用其共享席位。",
            )
            rows.append(row)
            continue

        ok, gate = eligibility(c, accepter)
        if not ok:
            row.update(
                status="rejected",
                status_label="未过门槛",
                reason_code="gate_failed",
                gate=gate,
                explanation=reason_text(state, {"reason_code": "gate_failed", "reason_detail": gate}),
            )
            rows.append(row)
            continue

        if c.get("pool", "normal") == "special":
            pool = "special"
            cap = int(accepter.get("capacity", 0)) if is_joint else int((accepter.get("quotas") or {}).get("special", 0))
        else:
            pool = "normal"
            cap = next(
                (r["normal_effective_capacity"] for r in result["capacity_audit"]["rows"] if r["accepter_id"] == aid),
                int((accepter.get("quotas") or {}).get("normal", 0)),
            )
        hs = holders.get(f"{aid}:{pool}", [])
        cmap = {x["id"]: x for x in state["candidates"]}
        cutoff = max(hs, key=lambda h: rank_key(state, cmap[h], accepter)) if hs else None
        my_rank = {
            "tier": candidate_tier(c, accepter),
            "tier_label": TIER_LABELS.get(candidate_tier(c, accepter)),
            "draw": tie_draw(state, aid, cid),
        }
        promise_here = [h for h in hs if result["assignments"][h].get("source") == "promise"]

        # Prefer the recorded proposal event when available.
        ev = next(
            (
                e
                for e in result.get("events", [])
                if e["candidate_id"] == cid and e.get("accepter_id") == aid and e.get("choice_index") == idx and e.get("outcome") == "rejected"
            ),
            None,
        )
        if ev:
            explanation = reason_text(state, ev)
            code = ev.get("reason_code")
        elif len(hs) < cap:
            code = "promise_blocks" if promise_here else "vacancy_but_not_held"
            explanation = (
                f"该项目本应存在空位（容量 {cap}，录取 {len(hs)}），但最终结果未录取你；"
                "请查看稳定性核对中的可阻挡组合。"
            )
        elif cutoff and rank_key(state, c, accepter) < rank_key(state, cmap[cutoff], accepter):
            code = "promise_blocks" if promise_here else "blocking_pair"
            explanation = "按优先序你应能替换末位录取者；这是承诺政策或规则冲突造成的可阻挡组合，详见稳定性核对。"
        else:
            code = "lower_priority_than_cutoff"
            cutoff_rank = {
                "tier": candidate_tier(cmap[cutoff], accepter),
                "tier_label": TIER_LABELS.get(candidate_tier(cmap[cutoff], accepter)),
                "draw": tie_draw(state, aid, cutoff),
                "candidate_id": cutoff,
            } if cutoff else None
            explanation = (
                f"该{POOL_LABELS[pool]}容量 {cap} 已满。你的优先序为「{rank_text(my_rank)}」；"
                + (f"末位 {_cname(state, cutoff)} 为「{rank_text(cutoff_rank)}」，因此你未进入该志愿。" if cutoff else "没有末位记录。")
            )
            if promise_here:
                explanation += f" 其中 {len(promise_here)} 个席位由保留承诺占据。"

        row.update(
            status="rejected",
            status_label="未录取",
            reason_code=code,
            explanation=explanation,
            pool=pool,
            capacity=cap,
            used=len(hs),
            holders=hs,
            cutoff_id=cutoff,
            my_rank=my_rank,
            promise_holders=promise_here,
        )
        rows.append(row)

    summary = _summary(state, c, a, rows)
    return {
        "candidate_id": cid,
        "candidate_name": c["name"],
        "pool": c.get("pool", "normal"),
        "assigned": a,
        "rows": rows,
        "summary": summary,
        "proposal_events": [e for e in result.get("events", []) if e["candidate_id"] == cid],
    }


def _summary(state: Dict[str, Any], c: Dict[str, Any], a: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]) -> str:
    if not a:
        first_reject = next((r for r in rows if r["status"] == "rejected"), None)
        tail = first_reject["explanation"] if first_reject else "所有志愿均未录取。"
        return f"{c['name']}（{c['id']}）本轮未被录取。{tail}"
    idx = a.get("choice_index")
    if idx in (0, None):
        if idx == 0:
            return f"{c['name']}（{c['id']}）被第一志愿 {_aname(state, a['accepter_id'])} 录取。"
        return f"{c['name']} 已保留此前席位。"
    earlier = [r for r in rows if r["choice_index"] < idx and r["status"] == "rejected"]
    top = earlier[0] if earlier else None
    if top:
        return (
            f"{c['name']}（{c['id']}）最终被第 {idx + 1} 志愿录取；"
            f"没有进入更靠前的「{top['accepter_name']}」，原因是：{top['explanation']}"
        )
    return f"{c['name']}（{c['id']}）最终被第 {idx + 1} 志愿录取。"
