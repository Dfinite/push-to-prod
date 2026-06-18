"""nodes.intake 단위/골든/엣지 테스트 (네트워크·키 불필요).

순수 함수(1–11)는 LLM seam 없이, 골든(12)·엣지(13–18)는 fake extract/verify 주입.
fake extract 는 텍스트만 담은 6키 dict 를, fake verify 는 후보 title 을 echo 한다.
"""

from __future__ import annotations

import copy

import fixtures as fx
from nodes.intake import (
    _candidate_docs,
    _chunk_text,
    _containment,
    _is_junk,
    _key,
    _merge_items,
    _merge_set,
    _norm,
    _set_key,
    _synthesize,
    intake,
)

ITEM_FIELDS = ("goals", "pain_points", "kpis", "constraints")
SET_FIELDS = ("systems", "stakeholders")


# ---------------------------------------------------------------------------
# fake seam 빌더 (골든·엣지 공용)
# ---------------------------------------------------------------------------


def _make_extract(by_title: dict):
    """title → {필드: [text,...]} 매핑을 받아 fake extract(title, text) 를 만든다.

    각 문서는 단일 청크라고 가정(샘플 input 길이 < CHUNK_SIZE). 항상 6키 보장.
    """
    empty = {f: [] for f in ITEM_FIELDS + SET_FIELDS}

    def extract(title: str, text: str) -> dict:
        out = dict(empty)
        out.update({k: list(v) for k, v in by_title.get(title, {}).items()})
        return out

    return extract


def _echo_verify(item_text, docs):
    """후보 문서 title 을 그대로 echo (모든 후보가 지지한다고 본다)."""
    return [d["title"] for d in docs]


# ---------------------------------------------------------------------------
# 1–3. _chunk_text
# ---------------------------------------------------------------------------


def test_chunk_empty():
    assert _chunk_text("") == []
    assert _chunk_text("   \n\t ") == []


def test_chunk_single():
    assert _chunk_text("짧은 본문") == ["짧은 본문"]
    s = "x" * 6000
    assert _chunk_text(s) == [s]  # len == size 경계


def test_chunk_overlap_coverage():
    text = "y" * 13000  # 6000*2 보다 큼 → 다중 청크
    chunks = _chunk_text(text, size=6000, overlap=200)
    # 모든 글자 커버: 재조립이 원문 길이 이상이고, 합집합이 전체를 덮음
    assert "".join(chunks) != ""
    assert chunks[0] == text[:6000]
    # step == size - overlap
    assert chunks[1] == text[5800 : 5800 + 6000]
    # 마지막 청크가 원문 끝까지 커버
    assert text.endswith(chunks[-1])
    # overlap >= size 인 비정상 입력에서도 step>0 (무한루프 방지)
    odd = _chunk_text("z" * 100, size=10, overlap=50)
    assert len(odd) > 0 and all(odd)


# ---------------------------------------------------------------------------
# 4. _norm / _key
# ---------------------------------------------------------------------------


def test_norm_and_key():
    # NFKC + casefold + 공백 collapse
    assert _norm("  ERP   WMS  ") == "erp wms"
    assert _norm("ＥＲＰ") == "erp"  # fullwidth → NFKC
    # _key 는 '·'/구두점 제거, _norm surface 는 유지
    s = "PoC 8주 · 거래처 20곳"
    assert "·" in _norm(s)  # surface base 는 구두점 유지
    assert "·" not in _key(s)  # key 는 구두점 제거
    # Hangul 보존 (쉼표/느낌표 제거, 공백은 유지)
    assert _key("납기, 준수율!") == "납기 준수율"
    # 구두점은 공백 치환이 아니라 삭제 → '·' 가 사라진다
    assert _key("ERP·WMS") == "erpwms"


# ---------------------------------------------------------------------------
# 5. _containment
# ---------------------------------------------------------------------------


def test_containment_asymmetric():
    item = "납기 준수율"
    doc = "목표는 납기 준수율 95% 달성이며 부진재고 비중도 관리한다"
    # 짧은 item 이 긴 doc 안에 전부 들어감 → 1.0
    assert _containment(item, doc) == 1.0
    # 비대칭: 뒤집으면 1.0 이 아님
    assert _containment(doc, item) < 1.0
    # 빈 item 토큰 → 0.0
    assert _containment("", doc) == 0.0
    assert _containment("!!!", doc) == 0.0  # 구두점만 → key 빈문자열


# ---------------------------------------------------------------------------
# 6–7. merge
# ---------------------------------------------------------------------------


def test_merge_items_dedup_union():
    pairs = [
        ("납기 준수율", "DocA"),
        ("납기, 준수율", "DocB"),  # 구두점만 다름 → 동일 key
        ("부진재고 비중", "DocA"),
    ]
    merged = _merge_items(pairs)
    assert len(merged) == 2
    # first-seen surface 유지
    assert merged[0]["text"] == "납기 준수율"
    # sources = first-seen 순서 union
    assert merged[0]["sources"] == ["DocA", "DocB"]
    assert merged[1] == {"text": "부진재고 비중", "sources": ["DocA"]}


def test_merge_set():
    pairs = [("ERP", "DocA"), ("WMS", "DocA"), ("erp", "DocB")]
    # erp 는 ERP 와 동일 key → first-seen surface 'ERP' 유지
    assert _merge_set(pairs) == ["ERP", "WMS"]


# ---------------------------------------------------------------------------
# 6b. _is_junk / junk 필터 (placeholder · 빈 토큰)
# ---------------------------------------------------------------------------


def test_is_junk_predicate():
    # 꺾쇠 placeholder (대소문자 무관 — _norm casefold)
    assert _is_junk("<UNKNOWN>")
    assert _is_junk("<none>")
    assert _is_junk("  < placeholder >  ")  # 공백 collapse 후에도 매치
    # 빈 / 공백 / 구두점만 → key 빈 문자열
    assert _is_junk("")
    assert _is_junk("   \t ")
    assert _is_junk("!!!")
    # 정상 콘텐츠는 절대 버리지 않는다
    assert not _is_junk("ERP")
    assert not _is_junk("김성태 (구매SCM본부장)")
    assert not _is_junk("납기 준수율 95% 달성")
    # 꺾쇠가 양끝이 아니면(예: 비교 표현) placeholder 아님
    assert not _is_junk("매출 < 목표")


def test_merge_set_drops_junk():
    pairs = [
        ("ERP", "DocA"),
        ("<UNKNOWN>", "DocA"),  # placeholder → drop
        ("   ", "DocB"),  # 공백 → drop
        ("WMS", "DocB"),
    ]
    assert _merge_set(pairs) == ["ERP", "WMS"]


def test_merge_items_drops_junk():
    pairs = [
        ("납기 준수율", "DocA"),
        ("<none>", "DocA"),  # placeholder → drop
        ("", "DocB"),  # 빈 토큰 → drop
    ]
    merged = _merge_items(pairs)
    assert len(merged) == 1
    assert merged[0]["text"] == "납기 준수율"


# ---------------------------------------------------------------------------
# 6c. _set_key — entity 본체 dedup (괄호 descriptor 무시)
# ---------------------------------------------------------------------------


def test_set_key_strips_parenthetical():
    # 후행 괄호 설명 제거 후 동일 entity → 같은 키
    assert _set_key("김성태 (구매SCM본부장)") == _set_key("김성태 (본부장, 의사결정자)")
    assert _set_key("김성태 (구매SCM본부장)") == "김성태"
    # 전각 괄호도 동일 처리
    assert _set_key("김성태（본부장）") == "김성태"


def test_merge_set_collapses_descriptor_duplicates():
    pairs = [
        ("김성태 (구매SCM본부장)", "DocA"),
        ("김성태 (본부장, 의사결정자)", "DocB"),  # 같은 인물, 다른 설명 → 1개로 병합
    ]
    merged = _merge_set(pairs)
    assert len(merged) == 1
    # first-seen surface 유지
    assert merged[0] == "김성태 (구매SCM본부장)"


def test_merge_set_keeps_distinct_entities():
    # 괄호 없는 진짜 다른 entity 는 그대로 유지
    assert _merge_set([("ERP", "D"), ("WMS", "D")]) == ["ERP", "WMS"]
    # 괄호 밖(본체)이 다르면 별개 entity 로 유지
    pairs = [("이영희 (구매팀장)", "D"), ("박철수 (구매팀장)", "D")]
    merged = _merge_set(pairs)
    assert len(merged) == 2
    assert merged == ["이영희 (구매팀장)", "박철수 (구매팀장)"]


def test_merge_items_not_over_merged_by_parenthetical():
    # item 필드는 _key 사용 → 괄호 디테일만 다른 두 항목은 별개로 남는다 (over-merge 가드)
    pairs = [
        ("리드타임 단축 (국내)", "DocA"),
        ("리드타임 단축 (해외)", "DocB"),
    ]
    merged = _merge_items(pairs)
    assert len(merged) == 2
    assert [m["text"] for m in merged] == ["리드타임 단축 (국내)", "리드타임 단축 (해외)"]


# ---------------------------------------------------------------------------
# 8. _candidate_docs
# ---------------------------------------------------------------------------


def test_candidate_docs_seeds_origin():
    item = {"text": "공급사 리드타임 변동 큼", "sources": ["메모X"]}
    doc_texts = {
        "메모X": "전혀 무관한 짧은 글",  # 임계 미달이지만 origin → 항상 포함
        "리드타임문서": "공급사 리드타임 변동 큼 이 문제가 반복된다",  # 높은 점수
        "잡음": "관계없는 다른 주제",
    }
    cands = _candidate_docs(item, doc_texts)
    titles = [c["title"] for c in cands]
    # origin 항상 포함
    assert "메모X" in titles
    # 점수 높은 문서 포함, 무관 문서 제외
    assert "리드타임문서" in titles
    assert "잡음" not in titles
    # 점수 내림차순 정렬 → 리드타임문서가 메모X 보다 앞
    assert titles.index("리드타임문서") < titles.index("메모X")


# ---------------------------------------------------------------------------
# 9–11. _synthesize
# ---------------------------------------------------------------------------


def test_synthesize_verbatim():
    assert _synthesize("이미 명시된 문제", [{"text": "통점1", "sources": []}]) == "이미 명시된 문제"


def test_synthesize_join():
    pains = [
        {"text": "성수기 납기 지연 클레임", "sources": ["x"]},
        {"text": "특정 SKU 반복 품절", "sources": ["y"]},
        {"text": "공급사 리드타임 변동 큼", "sources": ["z"]},
    ]
    assert _synthesize(None, pains) == "성수기 납기 지연 클레임 / 특정 SKU 반복 품절"


def test_synthesize_empty():
    assert _synthesize(None, []) == ""
    assert _synthesize("", []) == ""


# ---------------------------------------------------------------------------
# 12. 골든 재현 (fake 주입, 네트워크 없음)
# ---------------------------------------------------------------------------

# golden 문서 title → 필드별 텍스트(텍스트만, sources 없음)
_GOLDEN_EXTRACT = {
    "유통 운영 BRD v1.2": {
        "goals": ["납기 준수율 95% 달성"],
        "kpis": ["납기 준수율", "부진재고 비중"],
        "constraints": ["PoC 8주 · 거래처 20곳"],
        "systems": ["ERP", "WMS"],
        "stakeholders": ["물류팀", "영업"],
    },
    "ABC상사 미팅 메모": {
        "pain_points": ["성수기 납기 지연 클레임"],
    },
    "물류팀 회신": {
        "pain_points": ["특정 SKU 반복 품절", "공급사 리드타임 변동 큼"],
    },
}


def test_intake_golden_abc(pack_input):
    extract = _make_extract(_GOLDEN_EXTRACT)
    out = intake(pack_input, extract=extract, verify=_echo_verify)
    assert out["problem_profile"] == fx.load_problem_profile()
    assert out["problem"] == fx.load_problem()
    assert out["problem"] == "성수기 납기 지연 클레임 / 특정 SKU 반복 품절"


# ---------------------------------------------------------------------------
# 13–18. 엣지
# ---------------------------------------------------------------------------


def test_verifier_hallucination_filtered():
    state = {
        "documents": [
            {"kind": "note", "title": "실제문서", "content": "공급사 리드타임 변동 큼 반복"},
        ],
        "problem": "고정",
    }
    extract = _make_extract({"실제문서": {"pain_points": ["공급사 리드타임 변동 큼"]}})

    def bogus_verify(item_text, docs):
        # 후보에 없는 가짜 title + 실제 후보 title 하나
        return ["존재하지않는문서", *[d["title"] for d in docs]]

    out = intake(state, extract=extract, verify=bogus_verify)
    pains = out["problem_profile"]["pain_points"]
    assert len(pains) == 1
    # 가짜 title 제거 → allow set 교집합만
    assert pains[0]["sources"] == ["실제문서"]


def test_verifier_all_dropped_removes_item():
    state = {
        "documents": [
            {"kind": "note", "title": "실제문서", "content": "공급사 리드타임 변동 큼 반복"},
        ],
        "problem": "고정",
    }
    extract = _make_extract({"실제문서": {"pain_points": ["공급사 리드타임 변동 큼"]}})
    # verify 가 후보에 없는 title 만 반환 → 교집합 빈 → drop_unsupported
    out = intake(state, extract=extract, verify=lambda t, d: ["가짜"])
    assert out["problem_profile"]["pain_points"] == []


def test_empty_document():
    state = {
        "documents": [
            {"kind": "note", "title": "빈문서", "content": ""},
            {"kind": "note", "title": "실문서", "content": "성수기 납기 지연 클레임 반복"},
        ],
        "problem": None,
    }
    extract = _make_extract(
        {
            # 빈문서는 청크가 0개 → extract 호출조차 안 됨
            "빈문서": {"pain_points": ["없는내용"]},
            "실문서": {"pain_points": ["성수기 납기 지연 클레임"]},
        }
    )
    out = intake(state, extract=extract, verify=_echo_verify)
    pains = out["problem_profile"]["pain_points"]
    assert len(pains) == 1
    assert pains[0]["text"] == "성수기 납기 지연 클레임"
    assert pains[0]["sources"] == ["실문서"]


def test_no_mutation(pack_input):
    snapshot = copy.deepcopy(pack_input)
    extract = _make_extract(_GOLDEN_EXTRACT)
    intake(pack_input, extract=extract, verify=_echo_verify)
    assert pack_input == snapshot  # 입력 state 불변


def test_unsupported_dropped():
    # verify 가 빈 리스트 → seed 도 없어 sources 비면 제거
    state = {
        "documents": [{"kind": "note", "title": "D", "content": "무관 내용"}],
        "problem": "고정",
    }
    extract = _make_extract({"D": {"goals": ["근거없는 목표"]}})
    out = intake(state, extract=extract, verify=lambda t, d: [])
    assert out["problem_profile"]["goals"] == []


def test_field_cap():
    # 한 문서에서 9개 distinct goal → 8개로 cap, 순서 보존
    goals = [f"목표 항목 {i}" for i in range(9)]
    state = {
        "documents": [{"kind": "note", "title": "D", "content": " ".join(goals)}],
        "problem": "고정",
    }
    extract = _make_extract({"D": {"goals": goals}})
    out = intake(state, extract=extract, verify=_echo_verify)
    capped = out["problem_profile"]["goals"]
    assert len(capped) == 8
    assert [g["text"] for g in capped] == goals[:8]


def test_import_without_key(monkeypatch):
    # 키 없이도 import 가능해야 한다 (anthropic 은 지연 import).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib

    import nodes.intake as mod

    importlib.reload(mod)
    assert hasattr(mod, "intake")
