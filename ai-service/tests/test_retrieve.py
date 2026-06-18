"""S2 retrieve 노드 테스트 (API 키 불필요).

conftest fixture(problem_profile, seed, reference_columns) + monkeypatch 사용.
load_reference 는 DuckDB tpch 로 실제 데이터를 만들고, infer_expected 는 monkeypatch 로
llm.call_tool 을 대체해 키 없이 검증한다.
"""

from __future__ import annotations

import json

import schemas
from nodes import retrieve as R


def _keys(td):
    return set(td.__annotations__)


def _ref_cols(reference):
    return {
        f"{t['table']}.{c['name']}"
        for t in reference["schema"]
        for c in t["columns"]
    }


# ---------------------------------------------------------------------------
# load_reference
# ---------------------------------------------------------------------------


def test_load_reference_structure():
    ref = R.load_reference()
    assert ref["name"] == "tpch"
    assert set(ref) == {"name", "schema", "samples", "profile"}


def test_table_column_keys():
    ref = R.load_reference()
    for t in ref["schema"]:
        assert set(t) == _keys(schemas.Table)
        for c in t["columns"]:
            assert set(c) == {"name", "type"}


def test_types_lowercase():
    ref = R.load_reference()
    for t in ref["schema"]:
        for c in t["columns"]:
            assert c["type"] == c["type"].lower()


def test_pk_fk_dot_notation():
    ref = R.load_reference()
    by_table = {t["table"]: t for t in ref["schema"]}
    for t in ref["schema"]:
        for fk in t["fk"]:
            assert set(fk) == {"col", "ref"}
            assert "." in fk["ref"]
    assert by_table["region"]["fk"] == []
    assert by_table["partsupp"]["pk"] == ["ps_partkey", "ps_suppkey"]
    assert by_table["lineitem"]["pk"] == ["l_orderkey", "l_linenumber"]
    assert len(by_table["lineitem"]["fk"]) == 3


def test_json_serializable():
    json.dumps(R.load_reference())


def test_eight_tables_core():
    ref = R.load_reference()
    tables = {t["table"] for t in ref["schema"]}
    assert len(tables) == 8
    assert {"orders", "lineitem", "supplier", "partsupp", "customer", "part"} <= tables


def test_profile_min_le_max():
    ref = R.load_reference()
    for table, prof in ref["profile"].items():
        assert prof["rows"] > 0
        for key, val in prof.items():
            if key == "rows":
                continue
            assert val["min"] <= val["max"]


def test_load_reference_deterministic():
    a = R.load_reference()
    b = R.load_reference()
    assert a["schema"] == b["schema"]
    assert a["profile"] == b["profile"]
    assert a["samples"] == b["samples"]


# ---------------------------------------------------------------------------
# schema_gap
# ---------------------------------------------------------------------------


def test_schema_gap_matched_via_real_columns():
    ref = R.load_reference()
    ref_cols = _ref_cols(ref)
    expected = {
        "entities": [
            {"name": "출하", "needed_fields": ["약속납기", "실제출하일"], "from": []}
        ]
    }
    gap = R.schema_gap(expected, ref)
    assert gap["matched"], "출하 엔티티가 matched 되어야 함"
    for m in gap["matched"]:
        for col in m["via"]:
            assert col in ref_cols
        assert m["reference"] in {t["table"] for t in ref["schema"]}


def test_schema_gap_extra_subset():
    ref = R.load_reference()
    tables = {t["table"] for t in ref["schema"]}
    expected = {"entities": [{"name": "출하", "needed_fields": ["약속납기"], "from": []}]}
    gap = R.schema_gap(expected, ref)
    assert set(gap["extra"]) <= tables


def test_schema_gap_missing_has_note():
    ref = R.load_reference()
    expected = {
        "entities": [
            {"name": "공급사", "needed_fields": ["리드타임"], "from": []},
            {"name": "창고로봇", "needed_fields": ["배터리잔량"], "from": []},
        ]
    }
    gap = R.schema_gap(expected, ref)
    assert gap["missing"]
    for m in gap["missing"]:
        assert m["note"]


def test_schema_gap_deterministic():
    ref = R.load_reference()
    expected = {
        "entities": [
            {"name": "출하", "needed_fields": ["약속납기", "실제출하일"], "from": []},
            {"name": "공급사", "needed_fields": ["공급사ID", "리드타임"], "from": []},
        ]
    }
    assert R.schema_gap(expected, ref) == R.schema_gap(expected, ref)


def test_schema_gap_absent_entity_no_crash():
    ref = R.load_reference()
    expected = {
        "entities": [{"name": "창고로봇", "needed_fields": ["배터리잔량"], "from": []}]
    }
    gap = R.schema_gap(expected, ref)
    names = [m["expected"] for m in gap["missing"]]
    assert any("창고로봇" in n for n in names)


def test_schema_gap_field_split():
    ref = R.load_reference()
    expected = {
        "entities": [
            {"name": "공급사", "needed_fields": ["공급사ID", "리드타임"], "from": []}
        ]
    }
    gap = R.schema_gap(expected, ref)
    # 공급사ID → supplier 매칭
    assert any(m["reference"] == "supplier" for m in gap["matched"])
    # 리드타임 → missing (note 포함)
    assert any("리드타임" in m["expected"] for m in gap["missing"])
    for m in gap["missing"]:
        assert m["note"]


# ---------------------------------------------------------------------------
# infer_expected
# ---------------------------------------------------------------------------


def test_infer_expected_calls_tool(monkeypatch, problem_profile):
    fixed = {"entities": [{"name": "출하", "needed_fields": ["약속납기"], "from": []}]}

    def fake_call_tool(*, system, user, tool, temperature=0.2, **kw):
        return fixed

    monkeypatch.setattr("llm.call_tool", fake_call_tool)
    result = R.infer_expected(problem_profile)
    assert result == fixed
    assert "entities" in result


def test_infer_expected_empty_profile(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("call_tool 가 호출되면 안 됨")

    monkeypatch.setattr("llm.call_tool", boom)
    empty = {
        "goals": [],
        "pain_points": [],
        "kpis": [],
        "constraints": [],
        "systems": [],
        "stakeholders": [],
    }
    assert R.infer_expected(empty) == {"entities": []}


# ---------------------------------------------------------------------------
# retrieve (통합)
# ---------------------------------------------------------------------------


def test_retrieve_integration(monkeypatch, problem_profile):
    fixed = {"entities": [{"name": "출하", "needed_fields": ["약속납기", "실제출하일"], "from": []}]}

    def fake_call_tool(*, system, user, tool, temperature=0.2, **kw):
        return fixed

    monkeypatch.setattr("llm.call_tool", fake_call_tool)
    out = R.retrieve(
        {"problem_profile": problem_profile, "industry": "distribution"}
    )
    assert set(out) == {"seed"}
    seed = out["seed"]
    assert set(seed) == _keys(schemas.Seed)
    assert len(seed) == 4
    assert seed["gold_questions"] == R.GOLD_QUESTIONS


def test_gold_questions_count():
    assert len(R.GOLD_QUESTIONS) >= 8
