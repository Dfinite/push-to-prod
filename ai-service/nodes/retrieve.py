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
import json
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

# NOTE: PK/FK 는 Postgres information_schema 에서 실제 introspect 한다(과거 DuckDB tpch 는
#       제약을 노출하지 않아 하드코딩이 필요했지만, 실 Postgres 레퍼런스는 노출함).

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

# Postgres information_schema.data_type → 간결 소문자 표기.
# 매칭 실패 시 .lower() fallback (예: integer/bigint/numeric/real/date 는 그대로 통과).
_TYPE_ALIASES = {
    "character varying": "varchar",
    "character": "char",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "time without time zone": "time",
    "time with time zone": "timetz",
    "double precision": "double",
}

# profile 의 min/max 를 계산할 날짜/시간 타입(정규화 후 기준).
_DATE_TYPES = {"date", "timestamp", "timestamptz"}


def TYPE_NORMALIZE(raw: str) -> str:
    """Postgres data_type 문자열을 간결한 소문자 표기로 정규화."""
    s = str(raw).strip().lower()
    return _TYPE_ALIASES.get(s, s)


_EXPECTED_SYSTEM = (
    "너는 데이터 분석 설계자다. 입력으로 problem_profile(목표/통점/KPI/제약/시스템/이해관계자)과 "
    "레퍼런스 DB 스키마(table(columns) 목록)가 주어진다. 분석에 필요한 엔티티와 각 엔티티의 "
    "needed_fields 를 추론하고, 각 필드를 레퍼런스의 실제 컬럼에 매핑한다.\n"
    "규칙:\n"
    "- 근거 없는 엔티티/필드는 절대 만들지 말 것(할루시네이션 금지).\n"
    "- needed_fields 는 문서에 드러난 도메인 표현 그대로(한/영 무관) 사용한다.\n"
    "- from 은 그 엔티티의 근거가 된 문서 title 만 넣되, problem_profile 의 sources 에 "
    "실재하는 title 만 사용한다.\n"
    "- mapped_fields 에는 레퍼런스 스키마에 의미상 대응되는 컬럼이 실제로 있는 필드만 넣고, "
    "column 은 제공된 스키마의 정확한 'table.column' 문자열만 쓴다. 대응 컬럼이 없으면 "
    "그 필드는 mapped_fields 에서 빼라(→ gap 으로 처리됨). 컬럼명을 추측·날조하지 말 것.\n"
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


def _schema_vocab(reference: Dict[str, Any]) -> str:
    """레퍼런스 스키마를 'table(col1, col2, ...)' 한 줄/테이블 형태로 직렬화(LLM 매핑용)."""
    return "\n".join(
        f"{t['table']}({', '.join(c['name'] for c in t['columns'])})"
        for t in reference["schema"]
    )


def _load_env() -> None:
    """.env 의 REFERENCE_DATABASE_URL 등을 환경에 로드(있으면). 없어도 무해."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# load_reference
# ---------------------------------------------------------------------------


_EXCLUDED_SCHEMAS = ("pg_catalog", "information_schema")
# 고정 상수 → SQL IN 절 리터럴(안전). psycopg 의 NOT IN %s 튜플 확장 미지원 회피.
_EXCL_IN = "(" + ", ".join(f"'{s}'" for s in _EXCLUDED_SCHEMAS) + ")"
_SAMPLE_LIMIT = 5


def _dsn() -> str:
    """레퍼런스 Postgres 접속 문자열. REFERENCE_DATABASE_URL(권장) 우선.

    또는 REFERENCE_DB_HOST/PORT/USER/PASSWORD/NAME 개별 변수로 조립.
    """
    import os

    url = os.environ.get("REFERENCE_DATABASE_URL")
    if url:
        return url
    host = os.environ.get("REFERENCE_DB_HOST")
    if not host:
        raise RuntimeError(
            "REFERENCE_DATABASE_URL (또는 REFERENCE_DB_HOST 등) 가 설정되지 않았습니다. "
            ".env 를 확인하세요."
        )
    parts = {
        "host": host,
        "port": os.environ.get("REFERENCE_DB_PORT", "5432"),
        "user": os.environ.get("REFERENCE_DB_USER", "postgres"),
        "password": os.environ.get("REFERENCE_DB_PASSWORD", ""),
        "dbname": os.environ.get("REFERENCE_DB_NAME", "postgres"),
    }
    return " ".join(f"{k}={v}" for k, v in parts.items() if v != "")


def load_reference(dsn: str | None = None) -> Dict[str, Any]:
    """Postgres 레퍼런스 DB를 introspect → Reference dict (결정론적).

    어떤 Postgres DB(tpch / demo_foodco_stock 등)든 표준 information_schema 로
    테이블·컬럼·타입·PK·FK 를 실제로 수집한다. tpch 처럼 다중 스키마여도 모든
    비시스템 스키마의 BASE TABLE 을 포함한다(테이블명은 unqualified, FK ref 는 "table.col").
    """
    import psycopg
    from collections import defaultdict

    _load_env()
    with psycopg.connect(dsn or _dsn()) as con, con.cursor() as cur:
        db_name = con.info.dbname

        # 1) BASE TABLE 목록 (schema, name) — 스키마·이름 순
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            f"WHERE table_type='BASE TABLE' AND table_schema NOT IN {_EXCL_IN} "
            "ORDER BY table_schema, table_name"
        )
        tables = cur.fetchall()

        # 2) PK 맵 (schema, table) -> [col...] (ordinal 순)
        cur.execute(
            "SELECT tc.table_schema, tc.table_name, kcu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name=kcu.constraint_name "
            " AND tc.table_schema=kcu.table_schema "
            "WHERE tc.constraint_type='PRIMARY KEY' "
            f"  AND tc.table_schema NOT IN {_EXCL_IN} "
            "ORDER BY kcu.ordinal_position"
        )
        pk_map: Dict[tuple, List[str]] = defaultdict(list)
        for sch, tbl, col in cur.fetchall():
            pk_map[(sch, tbl)].append(col)

        # 3) FK 맵 (schema, table) -> [{col, ref:"reftable.refcol"}]
        cur.execute(
            "SELECT tc.table_schema, tc.table_name, kcu.column_name, "
            "       ccu.table_name AS ref_table, ccu.column_name AS ref_col "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name=kcu.constraint_name "
            " AND tc.table_schema=kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name=ccu.constraint_name "
            "WHERE tc.constraint_type='FOREIGN KEY' "
            f"  AND tc.table_schema NOT IN {_EXCL_IN}"
        )
        fk_map: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)
        for sch, tbl, col, rt, rc in cur.fetchall():
            fk_map[(sch, tbl)].append({"col": col, "ref": f"{rt}.{rc}"})

        schema: List[Dict[str, Any]] = []
        samples: Dict[str, Any] = {}
        profile: Dict[str, Any] = {}

        for sch, tbl in tables:
            if tbl in samples:  # 다중 스키마 동명 테이블 충돌 가드 (현 DB엔 없음)
                continue
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                (sch, tbl),
            )
            columns = [
                {"name": name, "type": TYPE_NORMALIZE(dtype)}
                for name, dtype in cur.fetchall()
            ]
            pk = list(pk_map.get((sch, tbl), []))
            schema.append(
                {
                    "table": tbl,
                    "columns": columns,
                    "pk": pk,
                    "fk": [dict(fk) for fk in fk_map.get((sch, tbl), [])],
                }
            )

            qual = f'"{sch}"."{tbl}"'
            col_names = [c["name"] for c in columns]

            # samples: pk 정렬 LIMIT 5 (pk 없으면 정렬 생략)
            order = (" ORDER BY " + ", ".join(f'"{c}"' for c in pk)) if pk else ""
            cur.execute(f"SELECT * FROM {qual}{order} LIMIT {_SAMPLE_LIMIT}")
            samples[tbl] = [
                {c: _jsonable(v) for c, v in zip(col_names, row)}
                for row in cur.fetchall()
            ]

            # profile: count(*) + date/timestamp 컬럼 min/max (단일 쿼리)
            date_cols = [c["name"] for c in columns if c["type"] in _DATE_TYPES]
            selects = ["count(*)"]
            for dc in date_cols:
                selects.append(f'min("{dc}")')
                selects.append(f'max("{dc}")')
            cur.execute(f"SELECT {', '.join(selects)} FROM {qual}")
            agg = cur.fetchone()
            tprofile: Dict[str, Any] = {"rows": int(agg[0])}
            idx = 1
            for dc in date_cols:
                tprofile[dc] = {"min": _jsonable(agg[idx]), "max": _jsonable(agg[idx + 1])}
                idx += 2
            profile[tbl] = tprofile

    return {"name": db_name, "schema": schema, "samples": samples, "profile": profile}


# ---------------------------------------------------------------------------
# infer_expected
# ---------------------------------------------------------------------------


def infer_expected(
    problem_profile: Dict[str, Any], reference: Dict[str, Any]
) -> Dict[str, Any]:
    """problem_profile + reference 스키마 → {"entities": [...]} (LLM EXPECTED_TOOL).

    LLM 이 각 needed_field 를 reference 의 실제 컬럼에 매핑(mapped_fields)하므로
    어떤 DB/언어든 gap 매칭이 동작한다.
    """
    fields = ("goals", "pain_points", "kpis", "constraints", "systems", "stakeholders")
    if all(not problem_profile.get(f) for f in fields):
        return {"entities": []}

    # 지연 import — 키 없이 모듈 import 가능하도록.
    from llm import EXPECTED_TOOL, call_tool

    user = (
        "[problem_profile]\n"
        + json.dumps(problem_profile, ensure_ascii=False)
        + "\n\n[레퍼런스 DB 스키마 — table(columns)]\n"
        + _schema_vocab(reference)
    )
    return call_tool(
        system=_EXPECTED_SYSTEM,
        user=user,
        tool=EXPECTED_TOOL,
        temperature=0.2,
    )


# ---------------------------------------------------------------------------
# schema_gap
# ---------------------------------------------------------------------------


def schema_gap(expected: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """expected(LLM 매핑 포함) vs reference 정합 → Gap dict (결정론적).

    LLM 이 emit_expected 로 채운 entity.mapped_fields([{field, column}])를
    reference 실컬럼에 대해 **검증**(존재하지 않는 컬럼=날조분 제거)하고 조립한다:
    - 검증된 컬럼이 1개+ 있으면 matched(주테이블 기준, via=주테이블 컬럼).
    - 매핑 안 된 needed_field 는 missing(EC-3 필드 분할).
    - 매핑 0개 엔티티는 전체 missing.
    - 어느 matched 에도 안 쓰인 테이블은 extra.
    """
    ref_cols: set = set()
    all_tables: set = set()
    for t in reference["schema"]:
        all_tables.add(t["table"])
        for c in t["columns"]:
            ref_cols.add(f"{t['table']}.{c['name']}")

    matched: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    used_tables: set = set()

    for entity in expected.get("entities", []):
        name = entity.get("name", "")
        needed = entity.get("needed_fields", []) or []
        mapped = entity.get("mapped_fields", []) or []

        # LLM 매핑을 실컬럼으로 검증 (날조 컬럼 제거).
        field_to_col: Dict[str, str] = {}
        for m in mapped:
            col = m.get("column")
            fld = m.get("field")
            if col in ref_cols and fld is not None:
                field_to_col[fld] = col

        cols = sorted(set(field_to_col.values()))
        if cols:
            counts: Dict[str, int] = {}
            for full in cols:
                tbl = full.split(".", 1)[0]
                counts[tbl] = counts.get(tbl, 0) + 1
            main_table = min(
                counts,
                key=lambda t: (
                    -counts[t],
                    TABLE_ORDER.index(t) if t in TABLE_ORDER else 999,
                    t,
                ),
            )
            via = sorted(c for c in cols if c.split(".", 1)[0] == main_table)
            matched.append({"expected": name, "reference": main_table, "via": via})
            used_tables.update(full.split(".", 1)[0] for full in cols)

            # EC-3: 매핑 안 된 needed_field 는 개별 missing.
            for field in needed:
                if field not in field_to_col:
                    missing.append(
                        {
                            "expected": f"{name} {field}".strip(),
                            "note": "레퍼런스에 대응 컬럼 없음 → 별도 소스 필요",
                        }
                    )
        else:
            missing.append(
                {
                    "expected": name,
                    "note": "레퍼런스에 대응 테이블/컬럼 없음 → 별도 소스 필요",
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
    expected = infer_expected(profile, reference)
    gap = schema_gap(expected, reference)
    return {
        "seed": {
            "reference": reference,
            "expected": expected,
            "gap": gap,
            "gold_questions": GOLD_QUESTIONS,
        }
    }
