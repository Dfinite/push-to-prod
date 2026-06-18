"""Domain Pack Builder — LangGraph orchestration (A1-S1 stub).

10노드 전부 스텁. review_questions만 실 interrupt 던지고,
review_ontology/review_final 은 자동승인 스텁이다.
S2에서 review_questions·gen_ontology 를, S3에서 workflows/demo/export 를 실 구현으로 교체.

데이터는 ai-service/canned_pack.json 을 그대로 흘려보낸다 (T30 게이트 충족 + 통합 폴백).

## 정책 (A1-S1 결정)

- **max_attempts 도달 시**: `review_questions.status` 는 **"rejected" 그대로 유지**하고
  `coerced=True` 플래그를 함께 기록한다. 라우터가 강제 통과시키되 감사 추적은 살린다.
- **edit 시 q.id 안정성**: `edited_items` 중 빈 id는 `q{maxN+1}` 결정론적으로 백엔드가 부여.
  ontology.answers 링크가 이 id에 의존하므로 사용자 추가 질문도 안정 id를 가져야 한다.
- **LLM 실패 정책 (S2/S3 노드 구현 시 적용)**:
  - gen_questions: 1회 자동 보정 재시도 (F-D8 패턴).
  - gen_ontology / gen_workflows / gen_demo: 실패 시 빈 결과 + 다음 단계 단순 통과.
    (review②③ 가 향후 라이브화되면 review 로 보내 인간이 결정.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from schemas import (
    COVERAGE_AREAS,
    BusinessQuestion,
    Coverage,
    DomainPackState,
    ProblemProfile,
    QuestionInterrupt,
    ReviewState,
)

# S2/S3 노드 모듈 — fk_skeleton + LLM 토글 + validate (모두 D-11 준수, llm.py re-export 또는 inline TOOL)
from nodes.ontology import gen_ontology
from nodes.workflows import gen_workflows
from nodes.demo import gen_demo
from nodes.export import assemble_export

_CANNED_PATH = Path(__file__).parent / "canned_pack.json"
_CANNED: Dict[str, Any] = json.loads(_CANNED_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 더미 데이터 (S2에서 A2 머지본이 들어오면 모두 교체됨)
# ---------------------------------------------------------------------------


def _dummy_problem_profile() -> ProblemProfile:
    return {
        "goals": [
            {"text": "납기 준수율 95% 달성", "sources": ["유통 운영 BRD v1.2"]},
        ],
        "pain_points": [
            {"text": "성수기 납기 지연 클레임", "sources": ["ABC상사 미팅 메모"]},
            {"text": "특정 SKU 반복 품절", "sources": ["물류팀 회신"]},
        ],
        "kpis": [
            {"text": "납기 준수율", "sources": ["유통 운영 BRD v1.2"]},
        ],
        "constraints": [
            {"text": "PoC 8주 · 거래처 20곳", "sources": ["유통 운영 BRD v1.2"]},
        ],
        "systems": ["ERP", "WMS"],
        "stakeholders": ["물류팀", "영업"],
    }


def _initial_review_state() -> ReviewState:
    return {"status": "pending", "feedback": [], "attempts": 0}


def _coverage_for(questions: List[BusinessQuestion]) -> Coverage:
    categories = {q.get("category") for q in questions}
    covered = [a for a in COVERAGE_AREAS if a in categories]
    missing = [a for a in COVERAGE_AREAS if a not in categories]
    return {"covered": covered, "missing": missing}


def _next_q_index(existing: List[BusinessQuestion]) -> int:
    """기존 q-id 들에서 최대 번호를 추출. 없으면 0."""
    max_n = 0
    for q in existing or []:
        qid = (q or {}).get("id") or ""
        if qid.startswith("q") and qid[1:].isdigit():
            max_n = max(max_n, int(qid[1:]))
    return max_n


def _assign_stable_ids(
    edited: List[BusinessQuestion], previous: List[BusinessQuestion]
) -> List[BusinessQuestion]:
    """edit 시 빈 id 에 q{maxN+1} 부여. 이미 있는 id는 보존.

    UI 가 사용자 추가 질문에 id 없이 보낼 수 있으므로 백엔드가 결정론적으로 채운다.
    """
    used = {(q or {}).get("id") for q in (previous or []) if (q or {}).get("id")}
    used |= {(q or {}).get("id") for q in (edited or []) if (q or {}).get("id")}
    next_n = _next_q_index((previous or []) + (edited or []))
    out: List[BusinessQuestion] = []
    for q in edited or []:
        qid = (q or {}).get("id")
        if not qid:
            next_n += 1
            new_id = f"q{next_n}"
            while new_id in used:  # 극히 드물지만 충돌 회피
                next_n += 1
                new_id = f"q{next_n}"
            used.add(new_id)
            q = {**q, "id": new_id}
        out.append(q)
    return out


# ---------------------------------------------------------------------------
# 노드 스텁 — 10개
# ---------------------------------------------------------------------------


def intake(state: DomainPackState) -> Dict[str, Any]:
    return {
        "problem_profile": _dummy_problem_profile(),
        "problem": "성수기 납기 지연 클레임 / 특정 SKU 반복 품절",
    }


def retrieve(state: DomainPackState) -> Dict[str, Any]:
    # S2 에서 A2 가 TPC-H introspect + gap 분석으로 교체.
    return {
        "seed": {
            "reference": {"name": "tpch", "schema": [], "samples": {}, "profile": {}},
            "expected": {"entities": []},
            "gap": {"matched": [], "missing": [], "extra": []},
            "gold_questions": [],
        }
    }


def gen_questions(state: DomainPackState) -> Dict[str, Any]:
    # S2 에서 A2 가 LLM 호출 + validate 로 교체. 지금은 canned_pack 의 q1/q2.
    return {
        "business_questions": _CANNED["business_questions"],
        "review_questions": _initial_review_state(),
    }


def review_questions(state: DomainPackState) -> Dict[str, Any]:
    """라이브 인터럽트 — 프론트는 stage='questions' 로 화면 라우팅.

    resume 결과 (ResumeDecision) 의 action 에 따라 분기:
    - approve   → status='approved', business_questions 유지
    - edit      → status='approved', business_questions = edited_items (빈 id 백엔드 부여)
    - regenerate → status='rejected', feedback 누적, attempts++,
                   attempts >= max_attempts 면 coerced=True (라우터가 강제 통과)
    """
    profile = state.get("problem_profile", {})
    current_questions = state.get("business_questions", [])
    payload: QuestionInterrupt = {
        "stage": "questions",
        "brd": profile,
        "items": current_questions,
        "coverage": _coverage_for(current_questions),
    }
    decision = interrupt(payload)
    if not isinstance(decision, dict):
        decision = {}

    action = decision.get("action", "approve")
    prev = state.get("review_questions") or _initial_review_state()
    prev_attempts = prev.get("attempts", 0)
    max_attempts = state.get("max_attempts", 3)

    if action == "approve":
        return {
            "review_questions": {
                "status": "approved",
                "feedback": [],
                "attempts": prev_attempts,
            }
        }
    if action == "edit":
        edited = _assign_stable_ids(decision.get("edited_items", []), current_questions)
        return {
            "business_questions": edited or current_questions,
            "review_questions": {
                "status": "approved",
                "feedback": [],
                "attempts": prev_attempts,
            },
        }

    # regenerate
    feedback = list(prev.get("feedback", []))
    if decision.get("feedback"):
        feedback.append(decision["feedback"])
    attempts_after = prev_attempts + 1
    review_out: Dict[str, Any] = {
        "status": "rejected",
        "feedback": feedback,
        "attempts": attempts_after,
    }
    if attempts_after >= max_attempts:
        # 라우터가 강제 통과시키지만 status 는 그대로 'rejected' 유지하고
        # 감사 추적용 플래그를 함께 기록한다.
        review_out["coerced"] = True
    return {"review_questions": review_out}


# gen_ontology 는 nodes/ontology.py 에서 import (S2 머지).


def review_ontology(state: DomainPackState) -> Dict[str, Any]:
    """MVP 자동승인 스텁 — 배선만 유지."""
    return {"review_ontology": {"status": "approved", "feedback": [], "attempts": 0}}


# gen_workflows / gen_demo 는 nodes/{workflows,demo}.py 에서 import (S3 머지).


def review_final(state: DomainPackState) -> Dict[str, Any]:
    """MVP 자동승인 스텁 — 배선만 유지."""
    return {"review_final": {"status": "approved", "feedback": [], "attempts": 0}}


# assemble_export 는 nodes/export.py 에서 import (S3 머지).


# ---------------------------------------------------------------------------
# 라우터 3개 — review 게이트의 분기 결정
# ---------------------------------------------------------------------------


def _max_attempts(state: DomainPackState) -> int:
    return state.get("max_attempts", 3)


def route_questions(state: DomainPackState) -> str:
    """rejected & attempts<max → 재생성. 그 외(approve/edit/강제 통과)는 ontology 진행.

    attempts >= max 이면 status 가 'rejected' 라도 coerced 플래그와 함께 통과한다.
    """
    review = state.get("review_questions") or _initial_review_state()
    if review.get("status") == "rejected" and review.get("attempts", 0) < _max_attempts(state):
        return "gen_questions"
    return "gen_ontology"


def route_ontology(state: DomainPackState) -> str:
    # MVP 자동승인. 향후 review_ontology 활성화 시 rejected 분기 추가.
    return "gen_workflows"


def route_final(state: DomainPackState) -> str:
    # MVP 자동승인. 향후 워크플로우/데모 개별 재생성 서브라우터로 확장.
    return "assemble_export"


# ---------------------------------------------------------------------------
# 그래프 조립
# ---------------------------------------------------------------------------


def build_graph():
    g = StateGraph(DomainPackState)

    g.add_node("intake", intake)
    g.add_node("retrieve", retrieve)
    g.add_node("gen_questions", gen_questions)
    g.add_node("review_questions", review_questions)
    g.add_node("gen_ontology", gen_ontology)
    g.add_node("review_ontology", review_ontology)
    g.add_node("gen_workflows", gen_workflows)
    g.add_node("gen_demo", gen_demo)
    g.add_node("review_final", review_final)
    g.add_node("assemble_export", assemble_export)

    g.add_edge(START, "intake")
    g.add_edge("intake", "retrieve")
    g.add_edge("retrieve", "gen_questions")
    g.add_edge("gen_questions", "review_questions")
    g.add_conditional_edges(
        "review_questions",
        route_questions,
        {"gen_questions": "gen_questions", "gen_ontology": "gen_ontology"},
    )
    g.add_edge("gen_ontology", "review_ontology")
    g.add_conditional_edges(
        "review_ontology",
        route_ontology,
        {"gen_workflows": "gen_workflows"},
    )
    g.add_edge("gen_workflows", "gen_demo")
    g.add_edge("gen_demo", "review_final")
    g.add_conditional_edges(
        "review_final",
        route_final,
        {"assemble_export": "assemble_export"},
    )
    g.add_edge("assemble_export", END)

    return g.compile(checkpointer=MemorySaver())


GRAPH = build_graph()
