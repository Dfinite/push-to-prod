"""공유 Claude 클라이언트 + tool-calling 헬퍼 (A2 노드 공통).

3개 노드(intake / retrieve / gen_questions)가 제각각 anthropic 클라이언트를 짜지 않도록
호출 패턴을 한 곳에 고정한다. tool input_schema 도 여기서 확정 (schemas.py 의 골격을 구체화).

사용:
    from llm import call_tool, PROFILE_TOOL
    data = call_tool(system="...", user="...", tool=PROFILE_TOOL, temperature=0.2)
    # data 는 tool input dict (검증된 구조)

환경변수(.env): ANTHROPIC_API_KEY (필수), ANTHROPIC_MODEL (선택, 기본 claude-sonnet-4-6)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# schemas 의 COVERAGE_AREAS 를 category enum 으로 재사용 (단일 출처)
from schemas import COVERAGE_AREAS

load_dotenv()

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


@lru_cache(maxsize=1)
def get_client():
    """anthropic 클라이언트 (지연 생성 + 캐시). 키 없으면 명확히 실패."""
    import anthropic  # 지연 import — 테스트에서 키 없이도 모듈 import 가능

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY 가 없습니다. `cp .env.example .env` 후 키를 채우세요."
        )
    return anthropic.Anthropic(api_key=key)


def call_tool(
    *,
    system: str,
    user: str,
    tool: Dict[str, Any],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """tool_choice 로 단일 tool 사용을 강제하고, 모델이 채운 input dict 를 반환한다.

    LLM 이 자유 텍스트 대신 구조화된 tool input 만 내도록 강제 → 파싱 불필요.
    """
    resp = get_client().messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": user}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
            return block.input  # type: ignore[return-value]
    raise RuntimeError(f"tool '{tool['name']}' 사용 결과가 응답에 없습니다: {resp.stop_reason}")


# ---------------------------------------------------------------------------
# Tool 정의 (input_schema 확정). properties 는 schemas.py TypedDict 와 1:1.
# ---------------------------------------------------------------------------

_STR_ARRAY = {"type": "array", "items": {"type": "string"}}

# intake.extract — 문서/청크 1건에서 프로파일 필드 추출.
# NOTE: 이 단계 출력은 텍스트(문자열)만. {text, sources} 결합은 merge(코드)가 doc.title 로 수행.
PROFILE_TOOL: Dict[str, Any] = {
    "name": "emit_profile",
    "description": "문서에서 목표/통점/KPI/시스템/제약/이해관계자를 추출한다. "
    "근거 없는 내용은 만들지 말 것(할루시 금지). 각 항목은 문서에 실제로 드러난 표현만.",
    "input_schema": {
        "type": "object",
        "properties": {
            "goals": _STR_ARRAY,
            "pain_points": _STR_ARRAY,
            "kpis": _STR_ARRAY,
            "constraints": _STR_ARRAY,
            "systems": _STR_ARRAY,
            "stakeholders": _STR_ARRAY,
        },
        "required": ["goals", "pain_points", "kpis", "constraints", "systems", "stakeholders"],
    },
}

# gen_questions — 비즈니스 질문 5~8개. id 는 코드가 q1.. 결정론 부여(여기 없음).
QUESTIONS_TOOL: Dict[str, Any] = {
    "name": "emit_questions",
    "description": "비즈니스 질문 5~8개 생성. 최소 3개 category 를 커버. "
    "linked_sources 는 제공된 스키마의 실제 'table.column' 만. profile 을 우선하고 앵커는 형태만 참고.",
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 5,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "category": {"type": "string", "enum": list(COVERAGE_AREAS)},
                        "rationale": {"type": "string"},
                        "linked_sources": _STR_ARRAY,
                        "data_status": {
                            "type": "string",
                            "description": "'available' 또는 'missing:<무엇>'",
                        },
                    },
                    "required": [
                        "question",
                        "category",
                        "rationale",
                        "linked_sources",
                        "data_status",
                    ],
                },
            }
        },
        "required": ["questions"],
    },
}

# gen_ontology (A1) — 노드/관계. relations 양끝 id 는 nodes 에 존재해야 함.
ONTOLOGY_TOOL: Dict[str, Any] = {
    "name": "emit_ontology",
    "description": "온톨로지 노드/관계 생성. node.id 는 안정적(n_*), relations 의 source/target 은 "
    "node.id 와 일치, answers 는 비즈니스 질문 id(q1,q2..) 와 일치.",
    "input_schema": {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "maps_from": _STR_ARRAY,
                        "answers": _STR_ARRAY,
                    },
                    "required": ["id", "name", "type", "maps_from", "answers"],
                },
            },
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "label": {"type": "string"},
                    },
                    "required": ["source", "target", "label"],
                },
            },
        },
        "required": ["nodes", "relations"],
    },
}
