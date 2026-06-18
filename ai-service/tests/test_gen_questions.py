"""S3 gen_questions 노드 테스트.

call_tool 을 monkeypatch 로 교체 → API 키 불필요·네트워크 0.
공유 conftest fixture(problem_profile, seed, reference_columns) 사용.
"""

from __future__ import annotations

import schemas
from nodes import gen_questions as gq


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _valid_questions():
    """실컬럼·category 3종 이상·6개 질문 (id 없음) — 통과해야 하는 LLM 출력."""
    return [
        {
            "question": "약속 납기 대비 출하 지연 라인의 비중은?",
            "category": "납기·리드타임",
            "rationale": "납기 준수율 진단",
            "linked_sources": ["lineitem.l_commitdate", "lineitem.l_shipdate"],
            "data_status": "available",
        },
        {
            "question": "수익 기여가 큰 상위 주문은?",
            "category": "수익·마진",
            "rationale": "매출 집중도",
            "linked_sources": ["orders.o_totalprice"],
            "data_status": "available",
        },
        {
            "question": "공급 가능 수량이 낮은 부품은?",
            "category": "재고 건전성",
            "rationale": "품절 위험",
            "linked_sources": ["partsupp.ps_availqty"],
            "data_status": "available",
        },
        {
            "question": "공급사 리드타임 리스크가 큰 공급사는?",
            "category": "공급·공급사 리스크",
            "rationale": "공급 안정성",
            "linked_sources": ["supplier.s_suppkey"],
            "data_status": "missing:공급사 리드타임",
        },
        {
            "question": "주문 규모가 큰 핵심 고객은?",
            "category": "고객·주문",
            "rationale": "거래처 관리",
            "linked_sources": ["orders.o_custkey", "customer.c_custkey"],
            "data_status": "available",
        },
        {
            "question": "월별 출하량 추세는?",
            "category": "납기·리드타임",
            "rationale": "수요 추세",
            "linked_sources": ["lineitem.l_shipdate"],
            "data_status": "available",
        },
    ]


def _patch_call_tool(monkeypatch, side):
    """nodes.gen_questions.call_tool 을 교체하고, 호출 인자 기록 리스트를 반환한다."""
    calls = []
    if callable(side):
        responses = None
    else:
        responses = list(side)

    def fake(*, system, user, tool, **kwargs):
        calls.append({"system": system, "user": user, "tool": tool, "kwargs": kwargs})
        if responses is not None:
            return responses[len(calls) - 1]
        return side(calls)

    monkeypatch.setattr(gq, "call_tool", fake)
    return calls


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_happy_path(monkeypatch, problem_profile, seed, reference_columns):
    calls = _patch_call_tool(monkeypatch, [{"questions": _valid_questions()}])

    out = gq.gen_questions({"problem_profile": problem_profile, "seed": seed})

    bqs = out["business_questions"]
    assert [q["id"] for q in bqs] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    bq_keys = set(schemas.BusinessQuestion.__annotations__)
    for q in bqs:
        assert set(q) == bq_keys
        assert q["category"] in schemas.COVERAGE_AREAS
        for src in q["linked_sources"]:
            assert src in reference_columns
    assert len({q["category"] for q in bqs}) >= 3
    assert out["review_questions"] == {"status": "pending", "feedback": [], "attempts": 0}
    assert len(calls) == 1


def test_regeneration_on_coverage_fail(monkeypatch, problem_profile, seed, reference_columns):
    only_two_cats = [
        {
            "question": "약속 납기 지연 라인은?",
            "category": "납기·리드타임",
            "rationale": "r",
            "linked_sources": ["lineitem.l_commitdate"],
            "data_status": "available",
        },
        {
            "question": "출하 지연 추세는?",
            "category": "납기·리드타임",
            "rationale": "r",
            "linked_sources": ["lineitem.l_shipdate"],
            "data_status": "available",
        },
        {
            "question": "수익 기여 상위 주문은?",
            "category": "수익·마진",
            "rationale": "r",
            "linked_sources": ["orders.o_totalprice"],
            "data_status": "available",
        },
        {
            "question": "라인 매출 상위 품목은?",
            "category": "수익·마진",
            "rationale": "r",
            "linked_sources": ["lineitem.l_extendedprice"],
            "data_status": "available",
        },
        {
            "question": "할인 큰 품목은?",
            "category": "수익·마진",
            "rationale": "r",
            "linked_sources": ["lineitem.l_discount"],
            "data_status": "available",
        },
    ]
    calls = _patch_call_tool(
        monkeypatch,
        [{"questions": only_two_cats}, {"questions": _valid_questions()}],
    )

    out = gq.gen_questions({"problem_profile": problem_profile, "seed": seed})

    assert len(calls) == 2
    # 2차 호출 user 인자에 재생성 피드백 문구가 포함
    assert "재생성 피드백" in calls[1]["user"]
    bqs = out["business_questions"]
    assert 5 <= len(bqs) <= 8
    assert len({q["category"] for q in bqs}) >= 3


def test_sanitize_drops_phantom_columns(reference_columns):
    questions = [
        {
            "question": "정상 질문",
            "category": "납기·리드타임",
            "rationale": "r",
            # 실컬럼 + 허위컬럼 섞임 → 허위만 제거
            "linked_sources": ["lineitem.l_shipdate", "lineitem.fake_col"],
            "data_status": "available",
        },
        {
            "question": "전부 허위 질문",
            "category": "수익·마진",
            "rationale": "r",
            # 전부 허위 → linked_sources 비게 됨 → 드롭
            "linked_sources": ["orders.ghost", "phantom.col"],
            "data_status": "available",
        },
    ]
    out = gq._sanitize(questions, reference_columns)

    assert len(out) == 1
    assert out[0]["question"] == "정상 질문"
    assert out[0]["linked_sources"] == ["lineitem.l_shipdate"]
    assert "lineitem.fake_col" not in out[0]["linked_sources"]


def test_topup_guarantees_minimum(monkeypatch, problem_profile, seed, reference_columns):
    # mock 이 단 1개(category 1종)만 반환해도 fallback 으로 최종 5~8·distinct>=3 보장
    one_question = [
        {
            "question": "약속 납기 지연 라인은?",
            "category": "납기·리드타임",
            "rationale": "r",
            "linked_sources": ["lineitem.l_commitdate"],
            "data_status": "available",
        }
    ]
    # validate 가 위반(개수/커버리지)을 잡아 재생성 1회 → 둘 다 동일하게 빈약
    calls = _patch_call_tool(
        monkeypatch,
        [{"questions": one_question}, {"questions": one_question}],
    )

    out = gq.gen_questions({"problem_profile": problem_profile, "seed": seed})

    bqs = out["business_questions"]
    assert 5 <= len(bqs) <= 8
    assert len({q["category"] for q in bqs}) >= 3
    for q in bqs:
        assert q["linked_sources"]
        for src in q["linked_sources"]:
            assert src in reference_columns
    # call_tool 은 2회까지만
    assert len(calls) == 2


def test_build_context_includes_schema_and_coverage(problem_profile, seed):
    system, user = gq.build_context(problem_profile, seed)

    # 실제 컬럼명 포함
    assert "l_commitdate" in user
    # COVERAGE_AREAS 일부 포함
    assert "납기·리드타임" in user
    assert "재고 건전성" in user
    # gap.missing 관련 (공급사 리드타임) 포함
    assert "공급사 리드타임" in user
    # gold 앵커는 일부만 (8개 전체 아님) — 4번째 gold 문장은 미포함
    assert seed["gold_questions"][0] in user
    assert seed["gold_questions"][3] not in user
    # 결정론 — 동일 입력 동일 출력
    system2, user2 = gq.build_context(problem_profile, seed)
    assert (system, user) == (system2, user2)


def test_validate_flags(reference_columns):
    valid = _valid_questions()
    assert gq.validate(valid, reference_columns) == []

    # 허위 컬럼
    bad_col = [dict(q) for q in valid]
    bad_col[0] = {**bad_col[0], "linked_sources": ["lineitem.nope"]}
    probs = gq.validate(bad_col, reference_columns)
    assert any("허용 컬럼이 아님" in p for p in probs)

    # category < 3 (모두 동일 category)
    one_cat = [
        {**valid[0], "question": f"q{i}", "linked_sources": ["lineitem.l_shipdate"]}
        for i in range(5)
    ]
    probs = gq.validate(one_cat, reference_columns)
    assert any("최소 3종 필요" in p for p in probs)

    # 중복 질문
    dup = [dict(valid[0]) for _ in range(5)]
    probs = gq.validate(dup, reference_columns)
    assert any("중복 질문" in p for p in probs)

    # 개수 위반
    probs = gq.validate(valid[:3], reference_columns)
    assert any("5~8개 필요" in p for p in probs)


def test_ids_assigned_last(reference_columns):
    # 중복이 섞인 입력 → _sanitize 로 줄어든 뒤에도 id 가 q1.. 연속(구멍 없음)
    raw = [
        {
            "question": "동일 질문",
            "category": "납기·리드타임",
            "rationale": "r",
            "linked_sources": ["lineitem.l_shipdate"],
            "data_status": "available",
        },
        {
            "question": "동일 질문",  # 중복 → 제거
            "category": "수익·마진",
            "rationale": "r",
            "linked_sources": ["orders.o_totalprice"],
            "data_status": "available",
        },
        {
            "question": "다른 질문",
            "category": "재고 건전성",
            "rationale": "r",
            "linked_sources": ["partsupp.ps_availqty"],
            "data_status": "available",
        },
    ]
    sanitized = gq._sanitize(raw, reference_columns)
    topped = gq._topup(sanitized, reference_columns)
    bqs = gq._assign_ids(topped)

    ids = [q["id"] for q in bqs]
    assert ids == [f"q{i}" for i in range(1, len(bqs) + 1)]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# foodco (demo_foodco_stock) 시나리오 — 실DB introspect 픽스처에 대한 오프라인 회귀
# ---------------------------------------------------------------------------


def _foodco_questions():
    """foodco 실컬럼·category 4종 (id 없음) — 도메인 비종속 동작 확인용."""
    return [
        {
            "question": "유통기한 임박 폐기 위험 자재는?",
            "category": "재고 건전성",
            "rationale": "폐기 손실",
            "linked_sources": ["inv_snapshot_fact.expiry_date", "inv_snapshot_fact.stock_qty"],
            "data_status": "available",
        },
        {
            "question": "소진 예상일이 짧아 품절 위험인 자재는?",
            "category": "납기·리드타임",
            "rationale": "품절 방지",
            "linked_sources": ["pred_forecast_daily.est_deplete_days"],
            "data_status": "available",
        },
        {
            "question": "폐기 리스크 등급이 높은 자재는?",
            "category": "공급·공급사 리스크",
            "rationale": "리스크 관리",
            "linked_sources": ["pred_risk_daily.risk_level", "pred_risk_daily.remaining_days"],
            "data_status": "available",
        },
        {
            "question": "체인(고객)별 출하 집중도가 큰 자재는?",
            "category": "고객·주문",
            "rationale": "거래처 분석",
            "linked_sources": ["agg_by_chain_daily.sales_customer_code", "agg_by_chain_daily.total_shipped_qty"],
            "data_status": "available",
        },
        {
            "question": "재배치 권고 수량이 큰 플랜트 조합은?",
            "category": "재고 건전성",
            "rationale": "재고 재배치",
            "linked_sources": ["pred_relocation_suggest.suggest_qty"],
            "data_status": "available",
        },
    ]


def test_foodco_fixture_offline(monkeypatch):
    """노드가 foodco 실DB introspect 픽스처에서도 계약대로 동작(네트워크 0)."""
    import fixtures as fx

    seed = fx.load_seed_foodco()
    profile = fx.load_problem_profile_foodco()
    ref_cols = gq._reference_columns(seed)

    _patch_call_tool(monkeypatch, [{"questions": _foodco_questions()}])
    out = gq.gen_questions({"problem_profile": profile, "seed": seed})

    bqs = out["business_questions"]
    bq_keys = set(schemas.BusinessQuestion.__annotations__)
    assert 5 <= len(bqs) <= 8
    assert [q["id"] for q in bqs] == [f"q{i}" for i in range(1, len(bqs) + 1)]
    assert len({q["category"] for q in bqs}) >= 3
    for q in bqs:
        assert set(q) == bq_keys
        assert q["category"] in schemas.COVERAGE_AREAS
        for src in q["linked_sources"]:
            assert src in ref_cols  # foodco 실컬럼만
    assert out["review_questions"] == {"status": "pending", "feedback": [], "attempts": 0}
