"""assemble_export 노드 — 전부 결정론 (LLM 사용 안 함).

build_required_sources:
  available = (모든 q.linked_sources ∪ ontology.nodes 의 maps_from 항목) - gap.missing 의 expected
  needed    = seed.gap.missing 의 각 항목을 "<expected> (<note>)" 형식으로

render_markdown_checklist:
  PoC 셋업 체크리스트 마크다운 (canned_pack.json export.markdown 형식 참조)
  섹션: 비즈니스 질문 / 온톨로지 / 워크플로우 / 필요 데이터 / 데모
"""

from __future__ import annotations

from typing import Any, Dict, List

from schemas import DomainPackState, RequiredSources


# ---------------------------------------------------------------------------
# build_required_sources
# ---------------------------------------------------------------------------


def build_required_sources(state: DomainPackState) -> RequiredSources:
    """available / needed 계산 (결정론).

    available:
      - 모든 business_questions 의 linked_sources 를 수집
      - ontology.nodes 의 maps_from 에서 테이블명 수집
      - seed.gap.missing 의 expected 값은 제외 (확보 안 된 소스)

    needed:
      - seed.gap.missing 의 각 항목 → "<expected> (<note>)" 형식
    """
    questions = state.get("business_questions", []) or []
    ontology = state.get("ontology", {}) or {}
    seed = state.get("seed", {}) or {}
    gap = seed.get("gap", {}) or {}

    # 1) linked_sources 수집 (q.linked_sources: ["orders.o_orderdate", ...])
    linked: set = set()
    for q in questions:
        for src in (q or {}).get("linked_sources", []):
            if src:
                linked.add(src)

    # 2) ontology.nodes.maps_from 수집 (테이블명 단위)
    maps_from_set: set = set()
    for node in (ontology or {}).get("nodes", []):
        for tbl in (node or {}).get("maps_from", []):
            if tbl:
                maps_from_set.add(tbl)

    # 3) gap.missing 의 expected (확보 안 된 소스 — available에서 제외)
    missing_items: List[dict] = gap.get("missing", []) or []
    missing_expected: set = set()
    for m in missing_items:
        exp = (m or {}).get("expected", "")
        if exp:
            missing_expected.add(exp)

    # available = linked ∪ maps_from — missing_expected
    available_set = (linked | maps_from_set) - missing_expected
    available: List[str] = sorted(available_set)

    # needed = "<expected> (<note>)" 형식
    needed: List[str] = []
    for m in missing_items:
        exp = (m or {}).get("expected", "")
        note = (m or {}).get("note", "")
        if exp and note:
            needed.append(f"{exp} ({note})")
        elif exp:
            needed.append(exp)

    return {"available": available, "needed": needed}


# ---------------------------------------------------------------------------
# render_markdown_checklist
# ---------------------------------------------------------------------------


def render_markdown_checklist(state: DomainPackState) -> str:
    """PoC 셋업 체크리스트 마크다운 생성 (결정론).

    섹션 순서: 비즈니스 질문 / 온톨로지 / 워크플로우 / 필요 데이터 / 데모
    """
    questions = state.get("business_questions", []) or []
    ontology = state.get("ontology", {}) or {}
    workflows = state.get("workflows", []) or []
    demo = state.get("demo_scenario", {}) or {}

    seed = state.get("seed", {}) or {}
    gap = seed.get("gap", {}) or {}
    missing_items: List[dict] = gap.get("missing", []) or []

    lines: List[str] = ["# PoC 셋업 체크리스트"]

    # --- 비즈니스 질문 섹션 ---
    lines.append("## 비즈니스 질문")
    if questions:
        for q in questions:
            qid = (q or {}).get("id", "")
            question_text = (q or {}).get("question", "")
            data_status = (q or {}).get("data_status", "available")
            suffix = ""
            if data_status and data_status.startswith("missing:"):
                missing_what = data_status[len("missing:"):]
                suffix = f" (데이터 확보 필요: {missing_what})"
            elif data_status and data_status != "available":
                suffix = f" ({data_status})"
            lines.append(f"- [{qid}] {question_text}{suffix}")
    else:
        lines.append("- (질문 없음)")

    # --- 온톨로지 섹션 ---
    lines.append("## 온톨로지")
    nodes = (ontology or {}).get("nodes", [])
    if nodes:
        node_parts: List[str] = []
        for n in nodes:
            name = (n or {}).get("name", "")
            maps = (n or {}).get("maps_from", [])
            if maps:
                node_parts.append(f"{name}({', '.join(maps)})")
            else:
                node_parts.append(name)
        lines.append("- " + ", ".join(node_parts))
    else:
        lines.append("- (온톨로지 없음)")

    # --- 워크플로우 섹션 ---
    lines.append("## 워크플로우")
    if workflows:
        for wf in workflows:
            wf_id = (wf or {}).get("id", "")
            wf_name = (wf or {}).get("name", "")
            aq = (wf or {}).get("answers_question", "")
            lines.append(f"- {wf_id} {wf_name} ({aq})")
    else:
        lines.append("- (워크플로우 없음)")

    # --- 필요 데이터 섹션 ---
    lines.append("## 필요 데이터")

    # 확보된 소스: maps_from 테이블명 목록
    maps_from_tables: List[str] = []
    seen_tables: set = set()
    for n in nodes:
        for tbl in (n or {}).get("maps_from", []):
            if tbl and tbl not in seen_tables:
                maps_from_tables.append(tbl)
                seen_tables.add(tbl)

    if maps_from_tables:
        lines.append(f"- 확보됨: {', '.join(maps_from_tables)}")
    else:
        # linked_sources 에서 테이블명 추출 (table.column → table)
        linked_tables: List[str] = []
        seen_linked: set = set()
        for q in questions:
            for src in (q or {}).get("linked_sources", []):
                tbl = src.split(".")[0] if src and "." in src else src
                if tbl and tbl not in seen_linked:
                    linked_tables.append(tbl)
                    seen_linked.add(tbl)
        if linked_tables:
            lines.append(f"- 확보됨: {', '.join(linked_tables)}")
        else:
            lines.append("- 확보됨: (없음)")

    if missing_items:
        for m in missing_items:
            exp = (m or {}).get("expected", "")
            if exp:
                lines.append(f"- 확보 필요: {exp}")
    else:
        lines.append("- 확보 필요: (없음)")

    # --- 데모 섹션 ---
    lines.append("## 데모")
    based_on = (demo or {}).get("based_on", "")
    narrative = (demo or {}).get("narrative", "")
    if based_on and narrative:
        lines.append(f"- {based_on} 기반 {narrative}")
    elif based_on:
        lines.append(f"- {based_on} 기반 시연")
    elif narrative:
        lines.append(f"- {narrative}")
    else:
        lines.append("- (데모 시나리오 없음)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 노드
# ---------------------------------------------------------------------------


def assemble_export(state: DomainPackState) -> Dict[str, Any]:
    """required_sources + export.markdown 조립 (전부 결정론, LLM 없음)."""
    required_sources = build_required_sources(state)
    markdown = render_markdown_checklist(state)
    return {
        "required_sources": required_sources,
        "export": {"markdown": markdown},
    }
