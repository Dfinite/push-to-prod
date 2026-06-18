"""Domain Pack Builder — shared schema contract (ai-service).

T0–10 공통 산출물. **typing 전용**으로, 런타임 의존성이 없다.
(LangGraph / FastAPI / anthropic 등 어떤 패키지도 import 하지 않는다 — 단순 import 가능.)

- A1(infra+후반 노드) / A2(content 노드)가 동일한 state 키와 페이로드 형태를 공유한다.
- W(web)는 이 파일을 1:1로 `src/types/index.ts` 로 옮긴다.
- T10 이후 계약 변경 금지 (불가피 시 3명 동시).

노드별 I/O 정본: docs/노드별설계.md
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict

# ---------------------------------------------------------------------------
# 0. 입력 (그래프 진입 / invoke)
# ---------------------------------------------------------------------------


class InputDoc(TypedDict):
    """업로드 문서 1건. parser가 텍스트로 정규화해서 인입."""

    kind: str  # "brd" | "sales_note" | "email" | "chat" | ...
    title: str
    content: str


class PackInput(TypedDict):
    """POST /runs 바디 (그래프 진입 페이로드)."""

    industry: str  # "distribution" | ...
    documents: List[InputDoc]
    problem: Optional[str]  # 사용자가 직접 명시한 문제. 없으면 None → intake가 합성.


# ---------------------------------------------------------------------------
# 1. intake → ProblemProfile
# ---------------------------------------------------------------------------


class ProfileItem(TypedDict):
    """근거 추적 가능한 프로파일 항목. sources = 지지 문서 title 목록.

    S2 BRD의 EvidenceBadge가 sources를 사용 → 빈 근거 항목은 drop_unsupported에서 제거.
    """

    text: str
    sources: List[str]  # InputDoc.title 들


class ProblemProfile(TypedDict):
    """intake 노드 출력. state['problem_profile']."""

    goals: List[ProfileItem]
    pain_points: List[ProfileItem]
    kpis: List[ProfileItem]
    constraints: List[ProfileItem]
    systems: List[str]  # 문자열 집합 (예: ["ERP", "WMS"])
    stakeholders: List[str]  # 문자열 집합 (예: ["물류팀", "영업"])


# ---------------------------------------------------------------------------
# 2. retrieve → Seed (reference · expected · gap · gold_questions)
# ---------------------------------------------------------------------------


class Column(TypedDict):
    name: str
    type: str


class Table(TypedDict):
    table: str
    columns: List[Column]
    pk: List[str]
    fk: List[dict]  # [{"col": "l_orderkey", "ref": "orders.o_orderkey"}]


class Reference(TypedDict):
    """레퍼런스 DB (MVP: TPC-H via DuckDB)."""

    name: str  # "tpch"
    schema: List[Table]
    samples: dict  # {table: [row, ...]}  5행/테이블
    profile: dict  # {table: {"rows": int, col: {"min":..,"max":..}}}


class Gap(TypedDict):
    """expected vs reference 정합 결과."""

    matched: List[dict]  # [{"expected","reference","via":[col...]}]
    missing: List[dict]  # [{"expected","note"}]
    extra: List[str]  # reference에만 있는 테이블/개념


class Seed(TypedDict):
    reference: Reference
    expected: dict  # {"entities": [{"name","needed_fields","from"}]}
    gap: Gap
    gold_questions: List[str]


# ---------------------------------------------------------------------------
# 3. gen_questions → BusinessQuestion[]
# ---------------------------------------------------------------------------


class BusinessQuestion(TypedDict):
    """질문 1건.

    id 는 결정론적으로 부여(q1, q2, ...) — 온톨로지 answers 링크가 의존하므로 안정적이어야 함.
    data_status 는 S3 뱃지 색을 결정: "available" 또는 "missing:<무엇>".
    """

    id: str  # "q1", "q2", ...
    question: str
    category: str  # COVERAGE_AREAS 중 하나 (enum 권장)
    rationale: str
    linked_sources: List[str]  # 실제 컬럼 경로 (예: "lineitem.l_commitdate")
    data_status: str  # "available" | "missing:<설명>"


# ---------------------------------------------------------------------------
# 5. gen_ontology → Ontology
# ---------------------------------------------------------------------------


class OntologyNode(TypedDict):
    id: str  # "n_order", ...
    name: str  # "고객주문"
    type: str  # "entity" | "event" | "kpi" | "property" | ...
    maps_from: List[str]  # reference 테이블명 (예: ["orders"])
    answers: List[str]  # BusinessQuestion.id 들 (예: ["q1"])


class OntologyRelation(TypedDict):
    source: str  # OntologyNode.id
    target: str  # OntologyNode.id
    label: str  # "주문-포함→출하라인"


class Ontology(TypedDict):
    nodes: List[OntologyNode]
    relations: List[OntologyRelation]


# ---------------------------------------------------------------------------
# 7~8. gen_workflows / gen_demo
# ---------------------------------------------------------------------------


class Workflow(TypedDict):
    id: str  # "wf1", ...
    name: str
    steps: List[str]
    answers_question: str  # BusinessQuestion.id
    uses_nodes: List[str]  # OntologyNode.id 들


class DemoScenario(TypedDict):
    narrative: str
    steps: List[str]
    based_on: str  # Workflow.id


# ---------------------------------------------------------------------------
# 4 / 6 / 9. review 노드 공통 상태
# ---------------------------------------------------------------------------


class ReviewState(TypedDict):
    status: Literal["pending", "approved", "rejected"]
    feedback: List[str]
    attempts: int


# ---------------------------------------------------------------------------
# 10. assemble_export → RequiredSources
# ---------------------------------------------------------------------------


class RequiredSources(TypedDict):
    available: List[str]  # 확보된 컬럼/소스
    needed: List[str]  # 확보 필요 (TPC-H 미보유 등)


# ---------------------------------------------------------------------------
# 그래프 전역 상태 / 최종 출력
# ---------------------------------------------------------------------------


class DomainPackState(TypedDict, total=False):
    """LangGraph 전역 state. 노드가 점진적으로 채우므로 total=False."""

    # 입력
    industry: str
    documents: List[InputDoc]
    problem: Optional[str]
    # intake
    problem_profile: ProblemProfile
    # retrieve
    seed: Seed
    # gen_questions
    business_questions: List[BusinessQuestion]
    # gen_ontology
    ontology: Ontology
    # gen_workflows / gen_demo
    workflows: List[Workflow]
    demo_scenario: DemoScenario
    # assemble_export
    required_sources: RequiredSources
    export: dict  # {"markdown": str, ...}
    # review 게이트
    review_questions: ReviewState
    review_ontology: ReviewState
    review_final: ReviewState
    max_attempts: int


class DomainPackOutput(TypedDict):
    """최종 pack (resume 종료 응답 / canned_pack.json). 프론트 S4·S7이 사용."""

    industry: str
    business_questions: List[BusinessQuestion]
    ontology: Ontology
    workflows: List[Workflow]
    demo_scenario: DemoScenario
    required_sources: RequiredSources
    export: dict  # {"markdown": str}


# ---------------------------------------------------------------------------
# interrupt / resume 페이로드 (review_questions ↔ UI)
# ---------------------------------------------------------------------------


class Coverage(TypedDict):
    covered: List[str]
    missing: List[str]


class QuestionInterrupt(TypedDict):
    """그래프 → UI (review_questions interrupt). stage 로 프론트 라우팅."""

    stage: Literal["questions"]
    brd: ProblemProfile  # S2 로컬 편집 표시용
    items: List[BusinessQuestion]
    coverage: Coverage


class ResumeDecision(TypedDict, total=False):
    """UI → 그래프 (3버튼 중 하나)."""

    action: Literal["approve", "edit", "regenerate"]
    edited_items: List[BusinessQuestion]  # action == "edit"
    feedback: str  # action == "regenerate"


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

# 질문 커버리지 영역. gen_questions의 build_context는 profile.kpis 기반으로
# 이 영역들을 채우려 시도하고, review 페이로드의 coverage.covered/missing이 이를 기준으로 계산된다.
COVERAGE_AREAS: List[str] = [
    "납기·리드타임",
    "공급·공급사 리스크",
    "수익·마진",
    "재고 건전성",
    "고객·주문",
]


# ---------------------------------------------------------------------------
# Claude tool-calling 정의 (input_schema 골격 — TODO 구현 시 채움)
# ---------------------------------------------------------------------------

# NOTE: 아래는 anthropic tool 정의의 골격이다. input_schema 의 properties 는
# 위 TypedDict 와 1:1로 맞춰 채운다. 런타임 의존성을 막기 위해 평범한 dict 로 둔다.

# intake.extract — 문서/청크별 ProblemProfile 필드 추출
PROFILE_TOOL: Dict[str, Any] = {
    "name": "emit_profile",
    "description": "문서에서 goals/pain_points/kpis/systems/constraints/stakeholders 추출",
    "input_schema": {
        "type": "object",
        # TODO: ProblemProfile 와 1:1. goals/pain_points/kpis/constraints 는
        #       {text, sources:[title]} 배열, systems/stakeholders 는 string 배열.
        "properties": {},
        "required": [],
    },
}

# gen_questions — BusinessQuestion[] 생성
QUESTIONS_TOOL: Dict[str, Any] = {
    "name": "emit_questions",
    "description": "비즈니스 질문 5~8개 생성 (최소 3개 category, linked_sources는 실제 컬럼)",
    "input_schema": {
        "type": "object",
        # TODO: {"questions": [BusinessQuestion ...]} — category 는 COVERAGE_AREAS enum,
        #       data_status 는 "available" | "missing:..", id 는 노드 코드에서 q1.. 부여.
        "properties": {},
        "required": [],
    },
}

# gen_ontology — Ontology(nodes, relations) 생성
ONTOLOGY_TOOL: Dict[str, Any] = {
    "name": "emit_ontology",
    "description": "온톨로지 노드/관계 생성 (FK skeleton 기반, 질문 answers 링크 포함)",
    "input_schema": {
        "type": "object",
        # TODO: {"nodes": [OntologyNode ...], "relations": [OntologyRelation ...]}.
        #       relations 양끝 id 는 nodes 에 존재해야 하고, answers 는 q-id 와 일치.
        "properties": {},
        "required": [],
    },
}
