"""gen_workflows 노드 — LLM tool-calling으로 2~3개 워크플로우 생성.

LLM 실패 정책 (D-6): try/except로 감싸 RuntimeError swallow → canned_pack fallback.
검증 정책:
  - answers_question 이 state['business_questions'] 의 id 집합에 속해야 함 (위반 drop)
  - uses_nodes 가 state['ontology']['nodes'] 의 id 집합에 ⊆ 이어야 함 (위반 item은 drop)
  - wf.id 중복 제거 (dedup)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from schemas import DomainPackState, Workflow

# ---------------------------------------------------------------------------
# Tool 정의 (inline — D-11: llm.py 변경 없음, 각 노드 모듈에 inline)
# ---------------------------------------------------------------------------

WORKFLOWS_TOOL: Dict[str, Any] = {
    "name": "emit_workflows",
    "description": (
        "비즈니스 질문을 해결하는 워크플로우 2~3개를 생성한다. "
        "각 워크플로우는 answers_question(질문 id)과 uses_nodes(온톨로지 노드 id 목록)를 반드시 포함해야 한다. "
        "steps 는 구체적인 분석 단계 3개 이상. "
        "answers_question 은 제공된 질문 id 중 하나여야 하고, "
        "uses_nodes 의 각 항목은 제공된 온톨로지 노드 id 중 하나여야 한다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "workflows": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "워크플로우 고유 id (예: wf1, wf2)",
                        },
                        "name": {
                            "type": "string",
                            "description": "워크플로우 이름",
                        },
                        "steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                            "description": "분석 단계 목록 (3개 이상)",
                        },
                        "answers_question": {
                            "type": "string",
                            "description": "이 워크플로우가 답하는 비즈니스 질문의 id",
                        },
                        "uses_nodes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "사용하는 온톨로지 노드 id 목록",
                        },
                    },
                    "required": ["id", "name", "steps", "answers_question", "uses_nodes"],
                },
            }
        },
        "required": ["workflows"],
    },
}

# ---------------------------------------------------------------------------
# fallback 로드 (key 없이 실행할 때)
# ---------------------------------------------------------------------------

_CANNED_PATH = Path(__file__).parent.parent / "canned_pack.json"


def _load_canned_workflows() -> List[Workflow]:
    try:
        data = json.loads(_CANNED_PATH.read_text(encoding="utf-8"))
        return data.get("workflows", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 검증 헬퍼
# ---------------------------------------------------------------------------


def _validate_workflows(
    raw: List[Dict[str, Any]],
    q_ids: set,
    node_ids: set,
) -> List[Workflow]:
    """answers_question ∈ q_ids, uses_nodes ⊆ node_ids 검증.
    위반 항목은 drop. wf.id 중복 제거.
    """
    seen_ids: set = set()
    result: List[Workflow] = []

    for wf in raw or []:
        wf_id = (wf or {}).get("id", "")
        if not wf_id or wf_id in seen_ids:
            continue

        aq = (wf or {}).get("answers_question", "")
        if aq not in q_ids:
            # answers_question 이 질문 id 집합에 없음 → drop
            continue

        raw_nodes: List[str] = (wf or {}).get("uses_nodes", [])
        valid_nodes = [n for n in raw_nodes if n in node_ids]
        # uses_nodes 가 완전히 비는 경우 그래도 유지 (노드가 없을 수 있음)
        # 단, 잘못된 id는 이미 필터됨

        result.append(
            {
                "id": wf_id,
                "name": (wf or {}).get("name", ""),
                "steps": (wf or {}).get("steps", []),
                "answers_question": aq,
                "uses_nodes": valid_nodes,
            }
        )
        seen_ids.add(wf_id)

    return result


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------


def gen_workflows(state: DomainPackState) -> Dict[str, Any]:
    """LLM tool-calling으로 2~3개 워크플로우 생성.

    LLM 실패 시 canned_pack fallback (D-6).
    검증: answers_question ∈ q_ids, uses_nodes ⊆ node_ids (위반 drop).
    """
    questions = state.get("business_questions", [])
    ontology = state.get("ontology", {})

    q_ids = {q.get("id") for q in (questions or []) if q.get("id")}
    node_ids = {n.get("id") for n in (ontology or {}).get("nodes", []) if n.get("id")}

    # LLM 호출 시도
    raw_workflows: List[Dict[str, Any]] = []
    try:
        from llm import call_tool  # 지연 import — key 없어도 모듈 import 가능

        # 시스템 프롬프트 구성
        q_summary = "\n".join(
            f"  - id={q.get('id')}: {q.get('question', '')}" for q in (questions or [])
        )
        node_summary = "\n".join(
            f"  - id={n.get('id')}: {n.get('name', '')} ({n.get('type', '')})"
            for n in (ontology or {}).get("nodes", [])
        )
        system = (
            "당신은 도메인 팩 빌더의 워크플로우 생성 전문가입니다. "
            "비즈니스 질문과 온톨로지 노드를 바탕으로 분석 워크플로우를 설계합니다. "
            "각 워크플로우는 반드시 하나의 비즈니스 질문에 답하고, "
            "온톨로지 노드를 활용하는 구체적인 단계로 구성됩니다."
        )
        user = (
            f"다음 비즈니스 질문들과 온톨로지 노드를 바탕으로 워크플로우 2~3개를 생성하세요.\n\n"
            f"## 비즈니스 질문 (answers_question은 아래 id 중 하나여야 합니다)\n{q_summary}\n\n"
            f"## 온톨로지 노드 (uses_nodes는 아래 id 목록에서만 선택)\n{node_summary}"
        )
        result = call_tool(
            system=system,
            user=user,
            tool=WORKFLOWS_TOOL,
            temperature=0.2,
        )
        raw_workflows = result.get("workflows", [])
    except Exception:
        # D-6: LLM 실패 → fallback
        raw_workflows = []

    # 검증
    validated = _validate_workflows(raw_workflows, q_ids, node_ids)

    # 검증 후 결과가 비어있으면 canned_pack fallback 재시도
    if not validated:
        canned = _load_canned_workflows()
        validated = _validate_workflows(canned, q_ids, node_ids)

    # q_ids / node_ids 가 비어있어 모두 drop 된 경우 canned 그대로 반환
    if not validated:
        canned = _load_canned_workflows()
        validated = canned  # 최후 fallback — id 사슬 검증은 외부에서 판단

    return {"workflows": validated}
