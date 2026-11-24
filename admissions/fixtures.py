"""Fictional demo dataset. All people, schools and scores are synthetic."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from .model import ensure_tie_vectors

SEED_TEXT = "清河学区-2026-确定性种子-v1"


def demo_state() -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "schema_version": 1,
        "seed_text": SEED_TEXT,
        "institution_notice": "本应用所有学生、校区、成绩与名额均为虚构，仅用于规则演示。",
        "counters": {"C": 18, "P": 4, "J": 1, "R": 2},
        "programs": [
            {
                "id": "P1",
                "name": "青禾实验中学·望江校区",
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": 1, "special": 1},
                "requires": {"maths": 60},
                "convert_special_to_normal": True,
            },
            {
                "id": "P2",
                "name": "青禾实验中学·云岭校区",
                "priority_order": ["legacy", "sibling", "diversity"],
                "quotas": {"normal": 3, "special": 1},
                "requires": {},
                "convert_special_to_normal": True,
            },
            {
                "id": "P3",
                "name": "河湾中学·南浦校区",
                "priority_order": ["sibling", "diversity", "legacy"],
                "quotas": {"normal": 2, "special": 1},
                "requires": {"maths": 50},
                "convert_special_to_normal": True,
            },
            {
                "id": "P4",
                "name": "河湾中学·北岸校区",
                "priority_order": ["diversity", "sibling", "legacy"],
                "quotas": {"normal": 2, "special": 2},
                "requires": {},
                "convert_special_to_normal": False,
            },
        ],
        "joint_programs": [
            {
                "id": "J1",
                "name": "区属科创·跨校共享专项",
                "capacity": 2,
                "hosts": [
                    {"program_id": "P2", "capacity": 1},
                    {"program_id": "P4", "capacity": 1},
                ],
                "priority_order": ["sibling", "legacy", "diversity"],
                "requires": {"science": 70},
                "convert_unused": True,
                "convert_to": {"program_id": "P3"},
            }
        ],
        "candidates": [
            # ---- P1 same-tier lottery pair --------------------------------
            {
                "id": "C01",
                "name": "沈望舒",
                "pool": "normal",
                "scores": {"maths": 92, "science": 88, "language": 90},
                "priorities": {"sibling": ["P1"]},
                "choices": [{"program_id": "P1"}, {"program_id": "P2"}, {"program_id": "P3"}],
            },
            {
                "id": "C02",
                "name": "陆知野",
                "pool": "normal",
                "scores": {"maths": 89, "science": 91, "language": 85},
                "priorities": {"sibling": ["P1"]},
                "choices": [{"program_id": "P1"}, {"program_id": "P2"}, {"program_id": "P4"}],
            },
            # ---- P2 legacy same-tier pair ---------------------------------
            {
                "id": "C03",
                "name": "顾清和",
                "pool": "normal",
                "scores": {"maths": 84, "science": 80, "language": 88},
                "priorities": {"legacy": ["P2"]},
                "choices": [{"program_id": "P2"}, {"program_id": "P1"}, {"program_id": "P3"}],
            },
            {
                "id": "C04",
                "name": "苏念安",
                "pool": "normal",
                "scores": {"maths": 82, "science": 86, "language": 91},
                "priorities": {"legacy": ["P2"]},
                "choices": [{"program_id": "P2"}, {"program_id": "P3"}, {"program_id": "P4"}],
            },
            # ---- Strong lower-tier applicants ------------------------------
            {
                "id": "C05",
                "name": "周既明",
                "pool": "normal",
                "scores": {"maths": 95, "science": 94, "language": 87},
                "priorities": {"diversity": ["P1", "P3"]},
                "choices": [{"program_id": "P1"}, {"program_id": "P3"}, {"program_id": "P2"}],
            },
            {
                "id": "C06",
                "name": "许听澜",
                "pool": "normal",
                "scores": {"maths": 58, "science": 76, "language": 83},
                "priorities": {},
                "choices": [{"program_id": "P1"}, {"program_id": "P3"}, {"program_id": "P4"}],
            },
            {
                "id": "C07",
                "name": "唐栖迟",
                "pool": "normal",
                "scores": {"maths": 73, "science": 79, "language": 82},
                "priorities": {"sibling": ["P3"]},
                "choices": [{"program_id": "P3"}, {"program_id": "P4"}, {"program_id": "P2"}],
            },
            {
                "id": "C08",
                "name": "江予白",
                "pool": "normal",
                "scores": {"maths": 78, "science": 75, "language": 80},
                "priorities": {"legacy": ["P1"]},
                "choices": [{"program_id": "P1"}, {"program_id": "P2"}, {"program_id": "P3"}],
            },
            {
                "id": "C09",
                "name": "林见鹿",
                "pool": "normal",
                "scores": {"maths": 69, "science": 72, "language": 77},
                "priorities": {"diversity": ["P4"]},
                "choices": [{"program_id": "P4"}, {"program_id": "P3"}, {"program_id": "P2"}],
            },
            {
                "id": "C10",
                "name": "贺云舟",
                "pool": "normal",
                "scores": {"maths": 88, "science": 90, "language": 84},
                "priorities": {"sibling": ["P2"]},
                "choices": [{"program_id": "P2"}, {"program_id": "P4"}, {"program_id": "P1"}],
            },
            {
                "id": "C11",
                "name": "温以凡",
                "pool": "normal",
                "scores": {"maths": 66, "science": 68, "language": 79},
                "priorities": {},
                "choices": [{"program_id": "P3"}, {"program_id": "P4"}],
            },
            {
                "id": "C12",
                "name": "程叙",
                "pool": "normal",
                "scores": {"maths": 71, "science": 74, "language": 70},
                "priorities": {"diversity": ["P2"]},
                "choices": [{"program_id": "P2"}, {"program_id": "P3"}, {"program_id": "P4"}],
            },
            # ---- Special pool / joint programme ----------------------------
            {
                "id": "C13",
                "name": "韩青禾",
                "pool": "special",
                "scores": {"maths": 91, "science": 96, "language": 86},
                "priorities": {"sibling": ["J1", "P2"]},
                "choices": [{"program_id": "J1"}, {"program_id": "P1"}, {"program_id": "P4"}],
                "host_preferences": ["P2", "P4"],
            },
            {
                "id": "C14",
                "name": "黎子衿",
                "pool": "special",
                "scores": {"maths": 87, "science": 92, "language": 93},
                "priorities": {"legacy": ["J1"]},
                "choices": [{"program_id": "J1"}, {"program_id": "P2"}, {"program_id": "P3"}],
                "host_preferences": ["P2", "P4"],
            },
            {
                "id": "C15",
                "name": "秦桑",
                "pool": "special",
                "scores": {"maths": 83, "science": 78, "language": 85},
                "priorities": {"diversity": ["J1", "P4"]},
                "choices": [{"program_id": "J1"}, {"program_id": "P4"}, {"program_id": "P2"}],
                "host_preferences": ["P4", "P2"],
            },
            {
                "id": "C16",
                "name": "魏澜",
                "pool": "special",
                "scores": {"maths": 76, "science": 65, "language": 81},
                "priorities": {},
                "choices": [{"program_id": "J1"}, {"program_id": "P4"}, {"program_id": "P3"}],
                "host_preferences": ["P2", "P4"],
            },
            {
                "id": "C17",
                "name": "谢时安",
                "pool": "special",
                "scores": {"maths": 80, "science": 82, "language": 78},
                "priorities": {"legacy": ["P4"]},
                "choices": [{"program_id": "P1"}, {"program_id": "P4"}, {"program_id": "P3"}, {"program_id": "J1"}],
                "host_preferences": ["P4", "P2"],
            },
            {
                "id": "C18",
                "name": "南知意",
                "pool": "normal",
                "scores": {"maths": 64, "science": 70, "language": 75},
                "priorities": {},
                "choices": [{"program_id": "P4"}, {"program_id": "P3"}],
            },
        ],
        "withdrawals": [],
        "seats": [],
        "rounds": [],
    }
    ensure_tie_vectors(state)
    return state


def fresh_state() -> Dict[str, Any]:
    return deepcopy(demo_state())


def coalition_state() -> Dict[str, Any]:
    """Tiny preset whose historical promises form a 3-way blocking cycle."""
    state: Dict[str, Any] = {
        "schema_version": 1,
        "scenario": "promise_coalition",
        "seed_text": "承诺联盟示例-固定种子-v1",
        "institution_notice": "三人三校的构造性示例：历史承诺形成可阻挡轮换联盟。",
        "counters": {"C": 3, "P": 3, "R": 1},
        "programs": [
            {
                "id": "A1",
                "name": "北辰校区·一席",
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": 1, "special": 0},
                "requires": {},
                "convert_special_to_normal": False,
            },
            {
                "id": "A2",
                "name": "南星校区·一席",
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": 1, "special": 0},
                "requires": {},
                "convert_special_to_normal": False,
            },
            {
                "id": "A3",
                "name": "东序校区·一席",
                "priority_order": ["sibling", "legacy", "diversity"],
                "quotas": {"normal": 1, "special": 0},
                "requires": {},
                "convert_special_to_normal": False,
            },
        ],
        "joint_programs": [],
        "candidates": [
            {
                "id": "K1",
                "name": "周砚",
                "pool": "normal",
                "scores": {},
                # High at A2, promised the less preferred A1.
                "priorities": {"sibling": ["A2"]},
                "choices": [{"program_id": "A2"}, {"program_id": "A1"}],
            },
            {
                "id": "K2",
                "name": "林峤",
                "pool": "normal",
                "scores": {},
                # High at A3, promised A2.
                "priorities": {"sibling": ["A3"]},
                "choices": [{"program_id": "A3"}, {"program_id": "A2"}],
            },
            {
                "id": "K3",
                "name": "许棠",
                "pool": "normal",
                "scores": {},
                # High at A1, promised A3.
                "priorities": {"sibling": ["A1"]},
                "choices": [{"program_id": "A1"}, {"program_id": "A3"}],
            },
        ],
        "withdrawals": [],
        "seats": [
            {
                "candidate_id": "K1",
                "program_id": "A1",
                "accepter_type": "program",
                "host_program_id": None,
                "pool": "normal",
                "choice_index": 1,
                "round_granted": 0,
                "released_in_round": None,
            },
            {
                "candidate_id": "K2",
                "program_id": "A2",
                "accepter_type": "program",
                "host_program_id": None,
                "pool": "normal",
                "choice_index": 1,
                "round_granted": 0,
                "released_in_round": None,
            },
            {
                "candidate_id": "K3",
                "program_id": "A3",
                "accepter_type": "program",
                "host_program_id": None,
                "pool": "normal",
                "choice_index": 1,
                "round_granted": 0,
                "released_in_round": None,
            },
        ],
        "rounds": [],
        "seed_commitments": True,
    }
    ensure_tie_vectors(state)
    return state
