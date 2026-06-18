"""S2 retrieve 노드 테스트.

- 오프라인(키·DB 불필요): schema_gap(LLM mapped_fields 검증·조립) / infer_expected(call_tool
  monkeypatch) / retrieve 통합(load_reference·call_tool monkeypatch) / 상수·헬퍼.
  frozen `fixtures/seed.json` 의 reference 를 오프라인 레퍼런스로 사용.
- 라이브(레퍼런스 Postgres 접근 가능할 때만): load_reference 가 실 DB 를 introspect 하는지.
  접근 불가하면 skip.
"""

from __future__ import annotations

import json

import pytest

import schemas
from nodes import retrieve as R


def _keys(td) -> set:
    return set(td.__annotations__)


# ---------------------------------------------------------------------------
# schema_gap (오프라인, frozen reference)
# ---------------------------------------------------------------------------


def test_schema_gap_matched(seed):
    ref = seed["reference"]
    expected = {
        "entities": [
            {
                "name": "출하",
                "needed_fields": ["약속납기", "실제출하일"],
                "from": ["x"],
                "mapped_fields": [
                    {"field": "약속납기", "column": "lineitem.l_commitdate"},
                    {"field": "실제출하일", "column": "lineitem.l_shipdate"},
                ],
            }
        ]
    }
    gap = R.schema_gap(expected, ref)
    assert len(gap["matched"]) == 1
    m = gap["matched"][0]
    assert m["reference"] == "lineitem"
    assert set(m["via"]) == {"lineitem.l_commitdate", "lineitem.l_shipdate"}
    assert m["via"] == sorted(m["via"])  # 정렬 안정(결정론)


def test_schema_gap_via_real_columns(seed, reference_columns):
    ref = seed["reference"]
    expected = {
        "entities": [
            {
                "name": "공급사",
                "needed_fields": ["공급사ID"],
                "from": [],
                "mapped_fields": [{"field": "공급사ID", "column": "supplier.s_suppkey"}],
            }
        ]
    }
    gap = R.schema_gap(expected, ref)
    for m in gap["matched"]:
        for v in m["via"]:
            assert v in reference_columns


def test_schema_gap_field_split(seed):  # EC-3
    ref = seed["reference"]
    expected = {
        "entities": [
            {
                "name": "공급사",
                "needed_fields": ["공급사ID", "리드타임"],
                "from": [],
                "mapped_fields": [{"field": "공급사ID", "column": "supplier.s_suppkey"}],
            }
        ]
    }
    gap = R.schema_gap(expected, ref)
    assert any(m["reference"] == "supplier" for m in gap["matched"])
    assert any("리드타임" in x["expected"] for x in gap["missing"])
    assert all(x["note"] for x in gap["missing"])


def test_schema_gap_absent_entity_no_crash(seed):  # EC-2
    ref = seed["reference"]
    expected = {
        "entities": [
            {"name": "창고로봇", "needed_fields": ["배터리잔량"], "from": [], "mapped_fields": []}
        ]
    }
    gap = R.schema_gap(expected, ref)
    assert not gap["matched"]
    assert any(x["expected"] == "창고로봇" for x in gap["missing"])


def test_schema_gap_drops_hallucinated_column(seed):
    """LLM 이 reference 에 없는 컬럼을 매핑하면 검증 단계에서 버려져 missing 처리."""
    ref = seed["reference"]
    expected = {
        "entities": [
            {
                "name": "x",
                "needed_fields": ["f"],
                "from": [],
                "mapped_fields": [{"field": "f", "column": "lineitem.l_nonexistent"}],
            }
        ]
    }
    gap = R.schema_gap(expected, ref)
    assert not gap["matched"]
    assert any(x["expected"] == "x" for x in gap["missing"])


def test_schema_gap_extra_subset(seed):
    ref = seed["reference"]
    tables = {t["table"] for t in ref["schema"]}
    gap = R.schema_gap({"entities": []}, ref)
    assert set(gap["extra"]) <= tables
    assert set(gap["extra"]) == tables  # 매칭 0 → 전부 extra
    assert gap["extra"] == sorted(gap["extra"])


def test_schema_gap_missing_has_note(seed):
    ref = seed["reference"]
    expected = {
        "entities": [{"name": "a", "needed_fields": ["b"], "from": [], "mapped_fields": []}]
    }
    gap = R.schema_gap(expected, ref)
    assert gap["missing"] and all(x["note"] for x in gap["missing"])


def test_schema_gap_deterministic(seed):
    ref = seed["reference"]
    expected = {
        "entities": [
            {
                "name": "출하",
                "needed_fields": ["약속납기"],
                "from": [],
                "mapped_fields": [{"field": "약속납기", "column": "lineitem.l_commitdate"}],
            }
        ]
    }
    assert R.schema_gap(expected, ref) == R.schema_gap(expected, ref)


# ---------------------------------------------------------------------------
# infer_expected (call_tool monkeypatch — API 키 불필요)
# ---------------------------------------------------------------------------


def test_infer_expected_calls_tool(monkeypatch, problem_profile, seed):
    captured = {}

    def fake(*, system, user, tool, temperature=0.2, **kw):
        captured["user"] = user
        captured["tool"] = tool
        return {
            "entities": [
                {"name": "E", "needed_fields": ["f"], "from": [], "mapped_fields": []}
            ]
        }

    monkeypatch.setattr("llm.call_tool", fake)
    out = R.infer_expected(problem_profile, seed["reference"])
    assert out["entities"][0]["name"] == "E"
    assert captured["tool"]["name"] == "emit_expected"
    # reference 스키마 vocab 이 user 메시지에 포함되어 LLM 이 실컬럼에 매핑 가능
    assert "lineitem(" in captured["user"]


def test_infer_expected_empty_profile_no_llm(monkeypatch, seed):
    def boom(**kw):
        raise AssertionError("빈 profile 에서는 LLM 을 호출하면 안 됨")

    monkeypatch.setattr("llm.call_tool", boom)
    empty = {
        "goals": [],
        "pain_points": [],
        "kpis": [],
        "constraints": [],
        "systems": [],
        "stakeholders": [],
    }
    assert R.infer_expected(empty, seed["reference"]) == {"entities": []}


# ---------------------------------------------------------------------------
# retrieve 통합 (load_reference + call_tool monkeypatch → 완전 오프라인)
# ---------------------------------------------------------------------------


def test_retrieve_integration(monkeypatch, problem_profile, seed):
    monkeypatch.setattr(R, "load_reference", lambda *a, **k: seed["reference"])

    def fake(*, system, user, tool, temperature=0.2, **kw):
        return {
            "entities": [
                {
                    "name": "공급사",
                    "needed_fields": ["공급사ID"],
                    "from": [],
                    "mapped_fields": [
                        {"field": "공급사ID", "column": "supplier.s_suppkey"}
                    ],
                }
            ]
        }

    monkeypatch.setattr("llm.call_tool", fake)
    out = R.retrieve({"problem_profile": problem_profile, "industry": "distribution"})
    seed_out = out["seed"]
    assert set(seed_out) == _keys(schemas.Seed)
    assert seed_out["gold_questions"] == R.GOLD_QUESTIONS
    assert any(m["reference"] == "supplier" for m in seed_out["gap"]["matched"])
    json.dumps(seed_out, ensure_ascii=False, default=str)  # 직렬화 가능


# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------


def test_gold_questions_count():
    assert len(R.GOLD_QUESTIONS) >= 8


def test_schema_vocab_format(seed):
    v = R._schema_vocab(seed["reference"])
    assert "lineitem(" in v and "l_commitdate" in v


def test_type_normalize():
    assert R.TYPE_NORMALIZE("character varying") == "varchar"
    assert R.TYPE_NORMALIZE("INTEGER") == "integer"
    assert R.TYPE_NORMALIZE("bigint") == "bigint"
    assert R.TYPE_NORMALIZE("timestamp without time zone") == "timestamp"


# ---------------------------------------------------------------------------
# 라이브 load_reference (레퍼런스 Postgres 접근 가능 시에만)
# ---------------------------------------------------------------------------


def _db_available() -> bool:
    try:
        import os

        import psycopg
        from dotenv import load_dotenv

        load_dotenv()
        dsn = os.environ.get("REFERENCE_DATABASE_URL")
        if not dsn:
            return False
        with psycopg.connect(dsn, connect_timeout=5):
            return True
    except Exception:
        return False


_DB_OK = _db_available()
_skip_db = pytest.mark.skipif(not _DB_OK, reason="레퍼런스 Postgres 접근 불가 (오프라인)")


@_skip_db
def test_load_reference_live_structure():
    ref = R.load_reference()
    assert isinstance(ref["name"], str) and ref["name"]
    assert set(ref) == _keys(schemas.Reference)
    assert len(ref["schema"]) >= 1
    for t in ref["schema"]:
        assert set(t) == _keys(schemas.Table)
        for c in t["columns"]:
            assert set(c) == _keys(schemas.Column)
            assert c["type"] == c["type"].lower()  # 타입 소문자 정규화
        for fk in t["fk"]:
            assert set(fk) == {"col", "ref"} and "." in fk["ref"]
    json.dumps(ref, ensure_ascii=False, default=str)  # 직렬화 가능
    for tp in ref["profile"].values():
        assert tp["rows"] >= 0


@_skip_db
def test_load_reference_live_deterministic():
    a = R.load_reference()
    b = R.load_reference()
    # 스키마·PK·FK 는 결정론적이어야 함
    sig = lambda ref: [(t["table"], t["pk"], t["fk"]) for t in ref["schema"]]
    assert sig(a) == sig(b)
