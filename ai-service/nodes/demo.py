"""gen_demo 노드 — pick_top 결정론 + LLM tool-calling으로 narrative+steps 생성.

pick_top 정책:
  - q1 을 answers_question 으로 갖는 워크플로우 우선
  - 없으면 workflows[0]
  - workflows 자체가 비어있으면 canned_pack fallback

LLM 실패 정책 (D-6): try/except → canned_pack.demo_scenario fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from schemas import DemoScenario, DomainPackState, Workflow

# ---------------------------------------------------------------------------
# Tool 정의 (inline — D-11)
# ---------------------------------------------------------------------------

DEMO_TOOL: Dict[str, Any] = {
    "name": "emit_demo",
    "description": (
        "선택된 워크플로우를 기반으로 PoC 데모 시나리오를 작성한다. "
        "narrative는 데이터에서 인사이트까지 이어지는 흐름을 한 문장으로 요약한다. "
        "steps는 청중에게 시연할 화면 순서(4~6단계)를 구체적으로 기술한다. "
        "based_on은 선택된 워크플로우의 id 그대로 넣는다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "데모 흐름을 한 문장으로 요약한 내러티브",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 4,
                "description": "시연 단계 목록 (4~6단계)",
            },
            "based_on": {
                "type": "string",
                "description": "기반 워크플로우 id (예: wf1)",
            },
        },
        "required": ["narrative", "steps", "based_on"],
    },
}

# ---------------------------------------------------------------------------
# fallback 로드
# ---------------------------------------------------------------------------

_CANNED_PATH = Path(__file__).parent.parent / "canned_pack.json"


def _load_canned_demo() -> DemoScenario:
    try:
        data = json.loads(_CANNED_PATH.read_text(encoding="utf-8"))
        return data.get("demo_scenario", {})
    except Exception:
        return {"narrative": "", "steps": [], "based_on": ""}


# ---------------------------------------------------------------------------
# pick_top — 결정론
# ---------------------------------------------------------------------------


def _pick_top(workflows: List[Workflow]) -> Optional[Workflow]:
    """q1 을 answers 하는 워크플로우 우선, 없으면 첫 번째."""
    if not workflows:
        return None
    for wf in workflows:
        if (wf or {}).get("answers_question") == "q1":
            return wf
    return workflows[0]


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------


def gen_demo(state: DomainPackState) -> Dict[str, Any]:
    """pick_top(결정론) → LLM tool-calling으로 narrative+steps 생성.

    LLM 실패 시 canned_pack fallback (D-6).
    """
    workflows: List[Workflow] = state.get("workflows", []) or []
    ontology = state.get("ontology", {}) or {}

    chosen: Optional[Workflow] = _pick_top(workflows)

    if chosen is None:
        # workflows 자체가 비어있는 경우 canned fallback
        return {"demo_scenario": _load_canned_demo()}

    # LLM 호출 시도
    try:
        from llm import call_tool

        node_summary = "\n".join(
            f"  - id={n.get('id')}: {n.get('name', '')} ({n.get('type', '')})"
            for n in (ontology or {}).get("nodes", [])
        )
        wf_steps = "\n".join(
            f"  {i + 1}. {step}"
            for i, step in enumerate((chosen or {}).get("steps", []))
        )
        system = (
            "당신은 도메인 팩 빌더의 데모 시나리오 작성 전문가입니다. "
            "선택된 워크플로우를 바탕으로 의사결정자에게 설득력 있는 PoC 데모 흐름을 작성합니다. "
            "narrative는 데이터에서 인사이트까지 이어지는 핵심을 한 문장으로 포착하고, "
            "steps는 실제 화면 시연 순서를 구체적으로 기술합니다."
        )
        user = (
            f"다음 워크플로우를 기반으로 PoC 데모 시나리오를 작성하세요.\n\n"
            f"## 선택된 워크플로우\n"
            f"- id: {chosen.get('id')}\n"
            f"- name: {chosen.get('name')}\n"
            f"- answers_question: {chosen.get('answers_question')}\n"
            f"- steps:\n{wf_steps}\n\n"
            f"## 온톨로지 노드\n{node_summary}\n\n"
            f"based_on 은 반드시 '{chosen.get('id')}' 를 그대로 사용하세요."
        )
        result = call_tool(
            system=system,
            user=user,
            tool=DEMO_TOOL,
            temperature=0.3,
        )
        demo_scenario: DemoScenario = {
            "narrative": result.get("narrative", ""),
            "steps": result.get("steps", []),
            "based_on": result.get("based_on", chosen.get("id", "")),
        }
        # based_on 이 chosen.id 와 다른 경우 강제 교정
        if demo_scenario["based_on"] != chosen.get("id"):
            demo_scenario["based_on"] = chosen.get("id", "")
        return {"demo_scenario": demo_scenario}

    except Exception:
        # D-6: LLM 실패 → canned fallback
        canned = _load_canned_demo()
        # based_on 은 실제 선택된 wf id 로 교정
        canned_fixed: DemoScenario = {
            "narrative": canned.get("narrative", ""),
            "steps": canned.get("steps", []),
            "based_on": chosen.get("id", canned.get("based_on", "")),
        }
        return {"demo_scenario": canned_fixed}
