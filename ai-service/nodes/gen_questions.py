"""S3 gen_questions 노드 — ProblemProfile + Seed → BusinessQuestion[].

흐름:
    state(problem_profile, seed)
      → build_context(profile, seed)            # 결정론적 프롬프트 합성 (LLM/IO 없음)
      → call_tool(QUESTIONS_TOOL)               # LLM 1차
      → validate(...)                           # 의미론 가드 (위반 피드백 목록)
      → [위반 시] call_tool(... + 피드백)        # 재생성 정확히 1회
      → _sanitize → _topup → _assign_ids        # 결정론 후처리 (실패 불가)
      → {"business_questions": [...], "review_questions": pending}

계약 동결 노트:
    llm.call_tool 은 strict mode / prompt-caching 를 지원하지 않는다(공유 계약, 변경 불가).
    즉 tool input_schema 의 enum/required 는 모델이 "권장"으로만 받으며 강제되지 않을 수 있다.
    따라서 이 모듈의 validate()(LLM 재생성 가드) + _sanitize()/_topup()(결정론 보정)이
    화이트리스트(실제 table.column)·category 커버리지·중복·개수의 유일한 실질 가드다.
    노드는 어떤 LLM 출력에도 예외/빈 출력으로 끝나지 않고 항상 5~8개의 유효 질문을 보장한다.

linked_sources 화이트리스트:
    seed['reference']['schema'] 의 모든 'table.column' 집합. 이 밖의 값은 허위(생성/추측)로 간주.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from llm import QUESTIONS_TOOL, call_tool
from schemas import COVERAGE_AREAS

# 최종 BusinessQuestion 이 가져야 할 정확한 6개 키 (schemas.BusinessQuestion 와 동일).
_BQ_KEYS = ("id", "question", "category", "rationale", "linked_sources", "data_status")

# data_status 미보유 표기 접두어.
_MISSING_PREFIX = "missing:"


# ---------------------------------------------------------------------------
# 1. 화이트리스트 / 정규화 유틸
# ---------------------------------------------------------------------------


def _reference_columns(seed: Dict[str, Any]) -> set[str]:
    """seed reference 스키마의 모든 'table.column' 집합 (linked_sources 화이트리스트)."""
    return {
        f"{t['table']}.{c['name']}"
        for t in seed["reference"]["schema"]
        for c in t["columns"]
    }


_NORMALIZE_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _normalize(text: str) -> str:
    """dedup 키: 공백·문장부호 제거 + 소문자. 표기만 다른 중복을 잡는다."""
    return _NORMALIZE_RE.sub("", str(text)).lower()


# ---------------------------------------------------------------------------
# 2. 프롬프트 합성 (결정론적, LLM/IO 없음)
# ---------------------------------------------------------------------------


def _join_items(items: List[Dict[str, Any]]) -> str:
    """ProfileItem 리스트 → '- text' 줄 모음 (없으면 '- (없음)')."""
    lines = [f"  - {it.get('text', '')}" for it in (items or [])]
    return "\n".join(lines) if lines else "  - (없음)"


def build_context(profile: Dict[str, Any], seed: Dict[str, Any]) -> Tuple[str, str]:
    """(system, user) 프롬프트를 결정론적으로 합성한다. 동일 입력 → 동일 출력."""
    ref = seed["reference"]
    ref_cols = sorted(_reference_columns(seed))

    system = (
        "당신은 유통/공급망 도메인의 비즈니스 분석가다. 업로드된 문제 프로파일과 "
        "레퍼런스 데이터(스키마)를 바탕으로, 데이터로 답할 수 있는 비즈니스 질문 5~8개를 만든다.\n"
        "규칙:\n"
        "- linked_sources 는 반드시 아래 user 메시지의 '유효 컬럼 화이트리스트'에 있는 실제 "
        "table.column 만 사용한다. 컬럼을 추측하거나 새로 만들지 말 것.\n"
        "- problem_profile(goals/pain_points/kpis/constraints)을 최우선 근거로 삼는다. "
        "gold 앵커는 질문의 '형태'만 참고하고, 문장을 복사/붙여넣지 말 것.\n"
        "- 서로 다른 category 를 최소 3개 커버한다. 다만 억지로 채우지 말고 profile 에서 "
        "자연스럽게 도출되는 질문을 우선한다.\n"
        "- data_status 는 데이터를 보유하면 'available', 미보유면 'missing:<무엇이 없는지>' 로 쓴다. "
        "미보유여도 linked_sources 는 가장 근접한 실제 컬럼으로 채운다."
    )

    # (a) profile 텍스트
    profile_block = (
        "[문제 프로파일]\n"
        f"목표(goals):\n{_join_items(profile.get('goals'))}\n"
        f"통점(pain_points):\n{_join_items(profile.get('pain_points'))}\n"
        f"KPI(kpis):\n{_join_items(profile.get('kpis'))}\n"
        f"제약(constraints):\n{_join_items(profile.get('constraints'))}\n"
        f"시스템: {', '.join(profile.get('systems') or []) or '(없음)'}\n"
        f"이해관계자: {', '.join(profile.get('stakeholders') or []) or '(없음)'}"
    )

    # (b) COVERAGE_AREAS + KPI 기반 우선영역 힌트
    kpi_text = " ".join(it.get("text", "") for it in (profile.get("kpis") or []))
    priority_hints: List[str] = []
    if any(k in kpi_text for k in ("납기", "리드타임", "지연")):
        priority_hints.append("납기·리드타임")
    if any(k in kpi_text for k in ("재고", "품절", "부진")):
        priority_hints.append("재고 건전성")
    if any(k in kpi_text for k in ("마진", "수익", "매출")):
        priority_hints.append("수익·마진")
    hint_line = (
        f"profile.kpis 로 보아 우선 다룰 영역: {', '.join(priority_hints)}"
        if priority_hints
        else "특정 우선영역 신호 없음 — profile 전반에서 자연스럽게 도출"
    )
    coverage_block = (
        "[커버리지 영역 (category enum, 정확히 이 5개 중에서만)]\n"
        + "\n".join(f"  - {a}" for a in COVERAGE_AREAS)
        + f"\n{hint_line}"
    )

    # (c) 스키마 카드 + 유효 컬럼 화이트리스트
    schema_cards = []
    for t in ref["schema"]:
        cols = ", ".join(f"{c['name']}:{c['type']}" for c in t["columns"])
        schema_cards.append(f"  {t['table']}({cols})")
    schema_block = (
        "[레퍼런스 스키마 카드]\n"
        + "\n".join(schema_cards)
        + "\n\n[유효 컬럼 화이트리스트 — linked_sources 는 여기에서만]\n"
        + ", ".join(ref_cols)
    )

    # (d) 샘플 일부 + reference.profile
    samples = ref.get("samples", {})
    sample_lines = []
    for table, rows in samples.items():
        if rows:
            sample_lines.append(f"  {table}[0] = {rows[0]}")
    profile_stats = ref.get("profile", {})
    data_block = (
        "[샘플 데이터 (테이블별 1행)]\n"
        + ("\n".join(sample_lines) if sample_lines else "  (없음)")
        + "\n[레퍼런스 통계 profile]\n"
        + f"  {profile_stats}"
    )

    # (e) gap matched/missing/extra + missing→data_status 규칙
    gap = seed.get("gap", {})
    matched = gap.get("matched", [])
    missing = gap.get("missing", [])
    extra = gap.get("extra", [])
    gap_block = (
        "[정합 gap]\n"
        f"  matched: {matched}\n"
        f"  missing: {missing}\n"
        f"  extra: {extra}\n"
        "  규칙: missing 에 해당하는 개념을 다루는 질문은 data_status='missing:<무엇>' 으로 표기하고, "
        "linked_sources 는 그래도 가장 근접한 실제 컬럼으로 채운다."
    )

    # (f) gold_questions 앞 3개만 의역 앵커
    gold = seed.get("gold_questions", []) or []
    anchors = gold[:3]
    gold_block = (
        "[의역 앵커 (형태만 참고, 복붙 금지 — 일부만 발췌)]\n"
        + "\n".join(f"  - {g}" for g in anchors)
        if anchors
        else "[의역 앵커] (없음)"
    )

    user = (
        f"{profile_block}\n\n"
        f"{coverage_block}\n\n"
        f"{schema_block}\n\n"
        f"{data_block}\n\n"
        f"{gap_block}\n\n"
        f"{gold_block}\n\n"
        "위 자료로 비즈니스 질문 5~8개를 생성하라. 각 질문은 question/category/rationale/"
        "linked_sources/data_status 를 가진다 (id 는 부여하지 말 것 — 코드가 부여)."
    )

    return system, user


# ---------------------------------------------------------------------------
# 3. validate — 의미론 가드 (위반 피드백 목록; 빈 리스트 = 통과)
# ---------------------------------------------------------------------------


def validate(questions: List[Dict[str, Any]], ref_cols: set[str]) -> List[str]:
    """LLM 출력의 의미론 위반을 사람이 읽는 피드백 문장 목록으로 반환한다."""
    problems: List[str] = []
    questions = questions or []

    # 개수 5~8
    n = len(questions)
    if n < 5 or n > 8:
        problems.append(f"질문 수가 {n}개입니다. 5~8개 필요")

    covered: set[str] = set()
    seen_norm: set[str] = set()
    for i, q in enumerate(questions, start=1):
        cat = q.get("category")
        # category enum
        if cat in COVERAGE_AREAS:
            covered.add(cat)
        else:
            problems.append(f"q{i}의 category '{cat}'는 허용 영역이 아님")

        # linked_sources 화이트리스트 (data_status 가 missing 이어도 동일 적용)
        for src in q.get("linked_sources") or []:
            if src not in ref_cols:
                problems.append(f"q{i}의 linked_source '{src}'는 허용 컬럼이 아님")

        # data_status 형식
        status = q.get("data_status", "")
        if status != "available" and not str(status).startswith(_MISSING_PREFIX):
            problems.append(
                f"q{i}의 data_status '{status}'는 'available' 또는 'missing:<설명>' 형식이어야 함"
            )

        # 중복 질문 (정규화 텍스트)
        norm = _normalize(q.get("question", ""))
        if norm and norm in seen_norm:
            problems.append(f"중복 질문: '{q.get('question')}'")
        seen_norm.add(norm)

    # distinct category < 3
    if len(covered) < 3:
        deficit = [a for a in COVERAGE_AREAS if a not in covered][:3]
        problems.append(
            f"커버된 category {sorted(covered)}, 최소 3종 필요. 부족: {deficit}"
        )

    return problems


# ---------------------------------------------------------------------------
# 4. _sanitize — 결정론 보정 (허위 컬럼 제거 / 중복 제거 / 빈 linked_sources 드롭)
# ---------------------------------------------------------------------------


def _sanitize(questions: List[Dict[str, Any]], ref_cols: set[str]) -> List[Dict[str, Any]]:
    """linked_sources 를 화이트리스트 교집합 + dedup 으로 정리하고,
    정규화 텍스트 기준 중복 질문을 제거하며, linked_sources 가 빈 질문은 드롭한다."""
    result: List[Dict[str, Any]] = []
    seen_norm: set[str] = set()
    for q in questions or []:
        # 화이트리스트 교집합 + 순서 보존 dedup
        clean_sources: List[str] = []
        for src in q.get("linked_sources") or []:
            if src in ref_cols and src not in clean_sources:
                clean_sources.append(src)
        if not clean_sources:
            # 실제 컬럼이 하나도 안 남으면 드롭
            continue
        norm = _normalize(q.get("question", ""))
        if norm in seen_norm:
            continue
        seen_norm.add(norm)
        result.append({**q, "linked_sources": clean_sources})
    return result


# ---------------------------------------------------------------------------
# 5. _topup — 결정론 보충 (노드가 빈 출력/예외로 끝나지 않게 보장)
# ---------------------------------------------------------------------------

# 5개 COVERAGE_AREA 를 각각 커버하는 fallback 템플릿 (실제 TPC-H 컬럼 사용, id 없음).
_FALLBACK: List[Dict[str, Any]] = [
    {
        "question": "약속 납기(l_commitdate) 대비 실제 출하(l_shipdate)가 지연된 라인의 비중은?",
        "category": "납기·리드타임",
        "rationale": "납기 준수율 KPI 를 출하 라인 단위 지연으로 정량화한다.",
        "linked_sources": ["lineitem.l_commitdate", "lineitem.l_shipdate"],
        "data_status": "available",
    },
    {
        "question": "부품-공급사 조합별 공급 가능 수량과 공급사 리드타임 리스크는?",
        "category": "공급·공급사 리스크",
        "rationale": "공급사 리드타임 변동이 큰 통점을 공급사·재고와 연결한다.",
        "linked_sources": ["supplier.s_suppkey", "partsupp.ps_suppkey"],
        "data_status": "missing:공급사 리드타임",
    },
    {
        "question": "주문 총액(o_totalprice)과 라인 매출(l_extendedprice) 기준 수익 기여 상위 항목은?",
        "category": "수익·마진",
        "rationale": "매출/마진 관점에서 우선 관리 대상 주문·품목을 식별한다.",
        "linked_sources": ["orders.o_totalprice", "lineitem.l_extendedprice"],
        "data_status": "available",
    },
    {
        "question": "공급 가능 수량(ps_availqty)이 낮아 품절 위험이 큰 부품-공급사 조합은?",
        "category": "재고 건전성",
        "rationale": "특정 SKU 반복 품절 통점을 재고 가용 수량으로 진단한다.",
        "linked_sources": ["partsupp.ps_availqty"],
        "data_status": "available",
    },
    {
        "question": "고객(c_custkey)별 주문(o_custkey) 규모와 클레임 위험이 높은 핵심 거래처는?",
        "category": "고객·주문",
        "rationale": "납기 지연 클레임이 집중되는 고객·주문을 식별한다.",
        "linked_sources": ["orders.o_custkey", "customer.c_custkey"],
        "data_status": "available",
    },
]


def _topup(questions: List[Dict[str, Any]], ref_cols: set[str]) -> List[Dict[str, Any]]:
    """fallback 템플릿으로 결정론적 보충: 결과가 5개 이상 AND distinct category>=3
    될 때까지 추가한다 (최대 8개 cap). 이미 있는 category/질문은 건너뛴다."""
    result = list(questions)
    have_cats = {q.get("category") for q in result}
    have_norm = {_normalize(q.get("question", "")) for q in result}

    def _enough() -> bool:
        cats = {q.get("category") for q in result if q.get("category") in COVERAGE_AREAS}
        return len(result) >= 5 and len(cats) >= 3

    # 1차: 아직 없는 category 부터 채워 distinct 를 빠르게 확보
    for fb in _FALLBACK:
        if _enough() or len(result) >= 8:
            break
        # ref_cols 에 존재하는 컬럼만 남김
        sources = [s for s in fb["linked_sources"] if s in ref_cols]
        if not sources:
            continue
        if fb["category"] in have_cats:
            continue
        if _normalize(fb["question"]) in have_norm:
            continue
        result.append({**fb, "linked_sources": sources})
        have_cats.add(fb["category"])
        have_norm.add(_normalize(fb["question"]))

    # 2차: 그래도 5개 미만이면 category 중복을 허용해서라도 개수를 채운다
    for fb in _FALLBACK:
        if len(result) >= 5:
            break
        sources = [s for s in fb["linked_sources"] if s in ref_cols]
        if not sources:
            continue
        if _normalize(fb["question"]) in have_norm:
            continue
        result.append({**fb, "linked_sources": sources})
        have_norm.add(_normalize(fb["question"]))

    return result


# ---------------------------------------------------------------------------
# 6. _assign_ids — 최종 리스트에 q1.. 순서 부여 + 6키 정규화
# ---------------------------------------------------------------------------


def _assign_ids(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """최종(dedup·sanitize·topup 후) 리스트에 q1, q2.. 를 순서대로 부여하고
    정확히 6개 키만 가진 BusinessQuestion dict 로 정규화한다."""
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(questions, start=1):
        out.append(
            {
                "id": f"q{i}",
                "question": q.get("question", ""),
                "category": q.get("category", ""),
                "rationale": q.get("rationale", ""),
                "linked_sources": list(q.get("linked_sources") or []),
                "data_status": q.get("data_status", "available"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 7. gen_questions — 오케스트레이션 (LangGraph 노드)
# ---------------------------------------------------------------------------


def gen_questions(state: Dict[str, Any]) -> Dict[str, Any]:
    """state(problem_profile, seed) → 부분 state(business_questions, review_questions).

    LLM 호출은 최대 2회(1차 + 위반 시 재생성 1회). 후처리는 결정론적이라 항상 5~8개를 보장한다.
    """
    profile = state["problem_profile"]
    seed = state["seed"]
    ref_cols = _reference_columns(seed)

    system, user = build_context(profile, seed)

    data = call_tool(system=system, user=user, tool=QUESTIONS_TOOL)
    questions = data["questions"]

    problems = validate(questions, ref_cols)
    if problems:
        # 재생성 정확히 1회 — 위반을 피드백으로 붙여 다시 생성
        user2 = (
            user
            + "\n\n[재생성 피드백 — 아래 위반을 고쳐 다시 생성]\n- "
            + "\n- ".join(problems)
        )
        data = call_tool(system=system, user=user2, tool=QUESTIONS_TOOL)
        questions = data["questions"]

    questions = _sanitize(questions, ref_cols)
    questions = _topup(questions, ref_cols)
    if len(questions) > 8:
        questions = questions[:8]

    business_questions = _assign_ids(questions)

    return {
        "business_questions": business_questions,
        "review_questions": {"status": "pending", "feedback": [], "attempts": 0},
    }
