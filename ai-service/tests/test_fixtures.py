"""공유 픽스처가 schemas.py 계약과 정합하고 상호 일관됨을 잠그는 baseline 테스트.

T10 이후 픽스처/계약이 깨지면 여기서 즉시 빨강 → 3 워크트리 모두 보호.
또한 llm.py 의 tool 정의가 schemas 와 어긋나지 않는지 확인.
"""

from __future__ import annotations

import schemas


def _keys(td):
    return set(td.__annotations__)


def test_pack_input(pack_input):
    assert set(pack_input) == _keys(schemas.PackInput)
    assert pack_input["industry"] == "distribution"
    for d in pack_input["documents"]:
        assert set(d) == _keys(schemas.InputDoc)


def test_problem_profile_keys_and_sources(problem_profile):
    assert set(problem_profile) == _keys(schemas.ProblemProfile)
    # 근거(sources) 가 비면 S2 EvidenceBadge 가 깨진다 → 전부 채워져야 함
    for field in ("goals", "pain_points", "kpis", "constraints"):
        for item in problem_profile[field]:
            assert set(item) == _keys(schemas.ProfileItem)
            assert item["sources"], f"{field} 항목의 sources 가 비었음"


def test_seed_reference_is_real_tpch(seed):
    assert set(seed) == _keys(schemas.Seed)
    assert set(seed["reference"]) == _keys(schemas.Reference)
    assert set(seed["gap"]) == _keys(schemas.Gap)
    tables = {t["table"] for t in seed["reference"]["schema"]}
    # retrieve 가 다루는 핵심 테이블이 존재해야 함
    assert {"orders", "lineitem", "supplier", "partsupp"} <= tables


def test_business_questions_contract(business_questions, reference_columns):
    ids = [q["id"] for q in business_questions]
    assert ids == ["q1", "q2"], f"id 가 불안정: {ids}"
    for q in business_questions:
        assert set(q) == _keys(schemas.BusinessQuestion)
        assert q["category"] in schemas.COVERAGE_AREAS
        # linked_sources 는 seed 의 실제 컬럼이어야 gen_questions.validate 통과
        for src in q["linked_sources"]:
            assert src in reference_columns, f"{q['id']} linked_source 비실존: {src}"


def test_llm_tools_align_with_schemas():
    # llm.py 는 anthropic import 를 지연시키므로 키 없이도 import 가능
    import llm

    # category enum 은 COVERAGE_AREAS 와 동일 출처
    props = llm.QUESTIONS_TOOL["input_schema"]["properties"]
    cat_enum = props["questions"]["items"]["properties"]["category"]["enum"]
    assert cat_enum == list(schemas.COVERAGE_AREAS)
    # PROFILE_TOOL 은 ProblemProfile 의 항목 필드를 모두 다룸
    prof_props = set(llm.PROFILE_TOOL["input_schema"]["properties"])
    assert prof_props == _keys(schemas.ProblemProfile)
