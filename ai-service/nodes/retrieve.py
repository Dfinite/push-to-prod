"""S2 retrieve 노드 — 레퍼런스 DB(TPC-H) 적재 + expected 추론 + schema gap 정합.

intake 출력(`state["problem_profile"]`)을 받아 Seed(reference·expected·gap·gold_questions)를 만든다.

- load_reference(): DuckDB tpch 확장으로 sf=0.01 데이터를 생성하고, 8개 핵심 테이블의
  컬럼/타입/pk/fk(정본 주입)·5행 샘플·count+date min/max 프로파일을 결정론적으로 수집.
- infer_expected(): problem_profile 을 LLM(EXPECTED_TOOL)으로 엔티티/needed_fields/from 추론.
  (빈 profile 이면 LLM 미호출 → {"entities": []})
- schema_gap(): expected 의 각 엔티티 needed_field 를 SYNONYMS·difflib 로 실컬럼에 매핑.
  매핑되면 matched, 안 되면 missing(note), 사용되지 않은 테이블은 extra.

키 없이도 모듈 import 가능하도록 llm import 는 infer_expected 내부에서 지연 수행한다.
"""

from __future__ import annotations

import datetime
import difflib
import json
import re
from decimal import Decimal
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

TABLE_ORDER: List[str] = [
    "region",
    "nation",
    "supplier",
    "customer",
    "part",
    "partsupp",
    "orders",
    "lineitem",
]

# TPC-H 정본 키.
# NOTE: 정본 TPC-H 는 lineitem→partsupp 복합 FK(L_PARTKEY, L_SUPPKEY)를 갖지만,
#       frozen seed.json 과의 일관성을 위해 분해형(l_partkey→part, l_suppkey→supplier)을 사용한다.
TPCH_KEYS: Dict[str, Dict[str, Any]] = {
    "region": {"pk": ["r_regionkey"], "fk": []},
    "nation": {
        "pk": ["n_nationkey"],
        "fk": [{"col": "n_regionkey", "ref": "region.r_regionkey"}],
    },
    "supplier": {
        "pk": ["s_suppkey"],
        "fk": [{"col": "s_nationkey", "ref": "nation.n_nationkey"}],
    },
    "customer": {
        "pk": ["c_custkey"],
        "fk": [{"col": "c_nationkey", "ref": "nation.n_nationkey"}],
    },
    "part": {"pk": ["p_partkey"], "fk": []},
    "partsupp": {
        "pk": ["ps_partkey", "ps_suppkey"],
        "fk": [
            {"col": "ps_partkey", "ref": "part.p_partkey"},
            {"col": "ps_suppkey", "ref": "supplier.s_suppkey"},
        ],
    },
    "orders": {
        "pk": ["o_orderkey"],
        "fk": [{"col": "o_custkey", "ref": "customer.c_custkey"}],
    },
    "lineitem": {
        "pk": ["l_orderkey", "l_linenumber"],
        "fk": [
            {"col": "l_orderkey", "ref": "orders.o_orderkey"},
            {"col": "l_partkey", "ref": "part.p_partkey"},
            {"col": "l_suppkey", "ref": "supplier.s_suppkey"},
        ],
    },
}

# 골든 seed.json 의 gold_questions 8개 (그대로 복사).
GOLD_QUESTIONS: List[str] = [
    "배송 우선순위가 높은(약속 납기 임박) 상위 주문은?",
    "수익 기여가 큰 상위 공급사는?",
    "약속 납기 대비 실제 출하가 지연된 라인의 비중은?",
    "지역(nation)별 매출 상위 고객은?",
    "부품-공급사 조합 중 공급 가능 수량이 낮은 항목은?",
    "월별 출하량 추세는 어떻게 변하는가?",
    "주문 우선순위(o_orderpriority)별 평균 처리 리드타임은?",
    "반품/할인(l_discount)이 큰 품목의 마진 영향은?",
]

# DuckDB 원시 타입(대문자) → 골든 seed.json 표기(소문자). 정규식 기반.
# 매칭 실패 시 .lower() fallback.
_TYPE_PATTERNS = [
    (re.compile(r"^\s*INTEGER\s*$", re.I), "integer"),
    (re.compile(r"^\s*BIGINT\s*$", re.I), "bigint"),
    (re.compile(r"^\s*DATE\s*$", re.I), "date"),
    (re.compile(r"^\s*VARCHAR.*$", re.I), "varchar"),
    (re.compile(r"^\s*DECIMAL\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*$", re.I), None),
]


def TYPE_NORMALIZE(raw: str) -> str:
    """DuckDB data_type 문자열을 골든 표기(소문자)로 정규화."""
    s = str(raw).strip()
    for pat, repl in _TYPE_PATTERNS:
        m = pat.match(s)
        if m:
            if repl is None:  # decimal(p,s)
                return f"decimal({m.group(1)},{m.group(2)})"
            return repl
    return s.lower()


# 정규화 키(한/영) → ["테이블.컬럼", ...].
# NOTE: 리드타임은 의도적으로 등록하지 않는다 (missing 유도).
SYNONYMS: Dict[str, List[str]] = {
    "납기": ["lineitem.l_commitdate"],
    "약속납기": ["lineitem.l_commitdate"],
    "출하": ["lineitem.l_shipdate"],
    "출고": ["lineitem.l_shipdate"],
    "실제출하일": ["lineitem.l_shipdate"],
    "입고": ["lineitem.l_receiptdate"],
    "수령": ["lineitem.l_receiptdate"],
    "주문": ["orders.o_orderdate", "orders.o_orderkey"],
    "발주": ["orders.o_orderdate"],
    "고객": ["customer.c_custkey"],
    "거래처": ["customer.c_custkey", "customer.c_name"],
    "공급사": ["supplier.s_suppkey", "supplier.s_name"],
    "공급업체": ["supplier.s_suppkey", "supplier.s_name"],
    "부품": ["part.p_partkey"],
    "품목": ["part.p_partkey"],
    "sku": ["part.p_partkey"],
    "수량": ["partsupp.ps_availqty", "lineitem.l_quantity"],
    "재고": ["partsupp.ps_availqty", "lineitem.l_quantity"],
    "가격": ["orders.o_totalprice", "lineitem.l_extendedprice"],
    "매출": ["orders.o_totalprice", "lineitem.l_extendedprice"],
    "수익": ["orders.o_totalprice", "lineitem.l_extendedprice"],
}

_EXPECTED_SYSTEM = (
    "너는 데이터 분석 설계자다. 입력으로 주어지는 problem_profile(목표/통점/KPI/제약/"
    "시스템/이해관계자)을 보고, 분석에 필요한 엔티티와 각 엔티티에 필요한 필드를 추론한다.\n"
    "규칙:\n"
    "- 근거 없는 엔티티/필드는 절대 만들지 말 것(할루시네이션 금지).\n"
    "- needed_fields 는 문서에 드러난 도메인 표현 그대로(한/영 무관) 사용한다.\n"
    "- from 은 그 엔티티의 근거가 된 문서 title 만 넣되, problem_profile 의 sources 에 "
    "실재하는 title 만 사용한다.\n"
    "- emit_expected tool 로만 답한다."
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _jsonable(v: Any) -> Any:
    """샘플 셀 값을 JSON 직렬화 가능 형태로 변환."""
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _normalize_token(s: str) -> str:
    """SYNONYMS 키 매칭용 정규화: 소문자화 + 공백·기호 제거."""
    return re.sub(r"[\s\W_]+", "", str(s).lower())


# ---------------------------------------------------------------------------
# load_reference
# ---------------------------------------------------------------------------


def load_reference() -> Dict[str, Any]:
    """DuckDB tpch(sf=0.01) → Reference dict (결정론적)."""
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.01)")

    schema: List[Dict[str, Any]] = []
    samples: Dict[str, Any] = {}
    profile: Dict[str, Any] = {}

    for table in TABLE_ORDER:
        # 컬럼 + 타입 (ordinal_position 순)
        rows = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        columns = [{"name": name, "type": TYPE_NORMALIZE(dtype)} for name, dtype in rows]
        date_cols = [c["name"] for c in columns if c["type"] == "date"]

        keys = TPCH_KEYS[table]
        schema.append(
            {
                "table": table,
                "columns": columns,
                "pk": list(keys["pk"]),
                "fk": [dict(fk) for fk in keys["fk"]],
            }
        )

        # samples: pk 정렬 LIMIT 5
        order_by = ", ".join(keys["pk"])
        sample_rows = con.execute(
            f"SELECT * FROM {table} ORDER BY {order_by} LIMIT 5"
        ).fetchall()
        col_names = [c["name"] for c in columns]
        samples[table] = [
            {col: _jsonable(val) for col, val in zip(col_names, row)}
            for row in sample_rows
        ]

        # profile: count(*) + date 컬럼 min/max (단일 쿼리)
        selects = ["count(*) AS _rows"]
        for dc in date_cols:
            selects.append(f"min({dc}) AS _min_{dc}")
            selects.append(f"max({dc}) AS _max_{dc}")
        agg = con.execute(f"SELECT {', '.join(selects)} FROM {table}").fetchone()
        tprofile: Dict[str, Any] = {"rows": int(agg[0])}
        idx = 1
        for dc in date_cols:
            vmin = _jsonable(agg[idx])
            vmax = _jsonable(agg[idx + 1])
            tprofile[dc] = {"min": vmin, "max": vmax}
            idx += 2
        profile[table] = tprofile

    return {"name": "tpch", "schema": schema, "samples": samples, "profile": profile}


# ---------------------------------------------------------------------------
# infer_expected
# ---------------------------------------------------------------------------


def infer_expected(problem_profile: Dict[str, Any]) -> Dict[str, Any]:
    """problem_profile → {"entities": [...]} (LLM EXPECTED_TOOL)."""
    fields = ("goals", "pain_points", "kpis", "constraints", "systems", "stakeholders")
    if all(not problem_profile.get(f) for f in fields):
        return {"entities": []}

    # 지연 import — 키 없이 모듈 import 가능하도록.
    from llm import EXPECTED_TOOL, call_tool

    return call_tool(
        system=_EXPECTED_SYSTEM,
        user=json.dumps(problem_profile, ensure_ascii=False),
        tool=EXPECTED_TOOL,
        temperature=0.2,
    )


# ---------------------------------------------------------------------------
# schema_gap
# ---------------------------------------------------------------------------


def _synonym_cols(token: str, ref_cols: set) -> List[str]:
    """정규화 토큰에 대해 SYNONYMS 부분 포함 매칭.

    실제 LLM 은 ' 공급사ID', '요청납기일', '주문(Order)' 같은 복합 토큰을 내므로
    정확 일치 대신 양방향 부분 포함(key⊆token 또는 token⊆key)으로 매핑한다.
    """
    out: List[str] = []
    for key, cols in SYNONYMS.items():
        if key and (key in token or token in key):
            out.extend(c for c in cols if c in ref_cols)
    return out


def _map_field(token: str, ref_cols: set, col_names: List[str]) -> List[str]:
    """needed_field 정규화 토큰 → 실컬럼 리스트. SYNONYMS(부분포함) 1차, difflib 2차."""
    syn = _synonym_cols(token, ref_cols)
    if syn:
        return syn
    # 2차: 컬럼명 자체에 대한 근사 매칭 (difflib) — 영문 토큰 보정용
    close = difflib.get_close_matches(token, col_names, n=3, cutoff=0.7)
    resolved: List[str] = []
    for cm in close:
        for full in ref_cols:
            if full.split(".", 1)[1] == cm:
                resolved.append(full)
    return resolved


def schema_gap(expected: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """expected vs reference 정합 → Gap dict (결정론적)."""
    # 1. 전체 컬럼 / 테이블 집합 (TABLE_ORDER 순)
    ref_cols: set = set()
    col_names: List[str] = []
    all_tables: set = set()
    for t in reference["schema"]:
        all_tables.add(t["table"])
        for c in t["columns"]:
            ref_cols.add(f"{t['table']}.{c['name']}")
            col_names.append(c["name"])

    matched: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    used_tables: set = set()

    for entity in expected.get("entities", []):
        name = entity.get("name", "")
        needed = entity.get("needed_fields", []) or []

        cols: set = set()
        unresolved: List[str] = []

        # name 토큰도 주테이블 힌트로 SYNONYMS(부분포함) 룩업
        cols.update(_synonym_cols(_normalize_token(name), ref_cols))

        for field in needed:
            token = _normalize_token(field)
            resolved = _map_field(token, ref_cols, col_names)
            if resolved:
                cols.update(resolved)
            else:
                unresolved.append(field)

        if cols:
            # 주테이블 = cols 에서 가장 많이 등장한 테이블 (동률 시 TABLE_ORDER 우선)
            counts: Dict[str, int] = {}
            for full in cols:
                tbl = full.split(".", 1)[0]
                counts[tbl] = counts.get(tbl, 0) + 1
            main_table = min(
                counts,
                key=lambda t: (-counts[t], TABLE_ORDER.index(t) if t in TABLE_ORDER else 999),
            )
            # via 는 주테이블 컬럼으로 한정해 엔트리를 일관되게 유지.
            # (다른 테이블 매칭분도 used_tables 에는 반영 → extra 정확도 보존)
            via = sorted(c for c in cols if c.split(".", 1)[0] == main_table)
            matched.append(
                {"expected": name, "reference": main_table, "via": via}
            )
            used_tables.update(full.split(".", 1)[0] for full in cols)

            # EC-3: 미해결 needed_field 는 개별 missing
            for field in unresolved:
                missing.append(
                    {
                        "expected": f"{name} {field}".strip(),
                        "note": "TPC-H 레퍼런스에 대응 컬럼 없음 → 별도 소스 필요",
                    }
                )
        else:
            # 엔티티 전체 미매칭
            missing.append(
                {
                    "expected": name,
                    "note": "TPC-H 레퍼런스에 대응 테이블/컬럼 없음 → 별도 소스 필요",
                }
            )

    extra = sorted(all_tables - used_tables)

    return {"matched": matched, "missing": missing, "extra": extra}


# ---------------------------------------------------------------------------
# retrieve (노드 엔트리)
# ---------------------------------------------------------------------------


def retrieve(state: Dict[str, Any]) -> Dict[str, Any]:
    """LangGraph 노드: state["problem_profile"] → {"seed": Seed}."""
    profile = state["problem_profile"]
    reference = load_reference()
    expected = infer_expected(profile)
    gap = schema_gap(expected, reference)
    return {
        "seed": {
            "reference": reference,
            "expected": expected,
            "gap": gap,
            "gold_questions": GOLD_QUESTIONS,
        }
    }
