"""gen_ontology 노드 — fk_skeleton → LLM tool-calling → validate.

파이프라인:
  1. fk_skeleton (결정론): schema.Table → OntologyNode, FK → OntologyRelation
  2. LLM tool-calling (모드 b 토글): ANTHROPIC_API_KEY 있으면 enhance, 없으면 canned fallback
  3. validate_ontology (결정론): 관계 양끝 존재·answers q-id 일치·dedup

D-6: LLM 실패(RuntimeError/Exception) → None 처리, fk_skeleton + fallback 그대로 통과.
D-11: ONTOLOGY_TOOL 정의는 llm.py에 이미 존재하므로 import 재사용.
      새 inline 정의가 필요하면 _ONTOLOGY_TOOL_INLINE 으로 별도 유지 (현재는 llm.ONTOLOGY_TOOL 사용).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# llm.py 는 A2 owner — 변경 금지. call_tool, ONTOLOGY_TOOL import.
import llm as _llm
from schemas import Ontology, OntologyNode, OntologyRelation

# ---------------------------------------------------------------------------
# D-11: tool 정의는 노드 모듈 안에 inline 정의가 원칙.
# llm.py 에 이미 완성된 ONTOLOGY_TOOL 이 있어서 중복 정의는 DRY 위반이므로
# 여기서는 llm.ONTOLOGY_TOOL 을 참조하고, ONTOLOGY_TOOL 이름으로 re-export 한다.
# ---------------------------------------------------------------------------
ONTOLOGY_TOOL: Dict[str, Any] = _llm.ONTOLOGY_TOOL

# ---------------------------------------------------------------------------
# canned fallback — LLM 없을 때 사용
# ---------------------------------------------------------------------------
_CANNED_PATH = Path(__file__).parent.parent / "canned_pack.json"


def _load_canned_ontology() -> Ontology:
    """canned_pack.json 의 ontology 를 읽어 반환. 파일 없으면 빈 Ontology."""
    try:
        data = json.loads(_CANNED_PATH.read_text(encoding="utf-8"))
        return data.get("ontology", {"nodes": [], "relations": []})
    except Exception:
        return {"nodes": [], "relations": []}


# ---------------------------------------------------------------------------
# Step 1 — fk_skeleton (결정론)
# ---------------------------------------------------------------------------


def _fk_skeleton(schema: List[Dict[str, Any]]) -> Ontology:
    """schema.Table 목록 → OntologyNode/OntologyRelation 생성.

    - 노드: id='n_<table>', type='entity', maps_from=[table_name], answers=[]
    - 관계: FK 1건 → OntologyRelation(source='n_<src>', target='n_<dst>',
             label='<src>-<col>→<dst>')
    - 빈 schema → {"nodes": [], "relations": []}
    """
    if not schema:
        return {"nodes": [], "relations": []}

    node_ids: set[str] = set()
    nodes: List[OntologyNode] = []
    relations: List[OntologyRelation] = []

    for table_def in schema:
        table_name: str = table_def.get("table", "")
        if not table_name:
            continue
        node_id = f"n_{table_name}"
        if node_id not in node_ids:
            node_ids.add(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "name": table_name,  # LLM 가 business name 으로 enhance 할 예정
                    "type": "entity",
                    "maps_from": [table_name],
                    "answers": [],
                }
            )

        # FK → relation
        for fk in table_def.get("fk", []):
            col: str = fk.get("col", "")
            ref: str = fk.get("ref", "")  # e.g. "customer.c_custkey"
            if not col or not ref:
                continue
            dst_table = ref.split(".")[0] if "." in ref else ref
            dst_id = f"n_{dst_table}"
            relations.append(
                {
                    "source": node_id,
                    "target": dst_id,
                    "label": f"{table_name}-{col}→{dst_table}",
                }
            )

    return {"nodes": nodes, "relations": relations}


# ---------------------------------------------------------------------------
# Step 2 — LLM enhance (모드 b)
# ---------------------------------------------------------------------------


def _api_key_available() -> bool:
    """ANTHROPIC_API_KEY 환경변수 존재 여부 (llm.is_available 대체)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _build_llm_user_prompt(
    skeleton: Ontology,
    business_questions: List[Dict[str, Any]],
) -> str:
    """LLM 호출용 user 프롬프트 구성."""
    q_lines = "\n".join(
        f"  - {q.get('id','')}: {q.get('question','')}" for q in business_questions
    )
    node_lines = "\n".join(
        f"  - {n['id']} (table: {', '.join(n['maps_from'])})" for n in skeleton["nodes"]
    )
    rel_lines = "\n".join(
        f"  - {r['source']} --[{r['label']}]--> {r['target']}"
        for r in skeleton["relations"]
    )
    return (
        "## FK Skeleton (자동 생성)\n"
        f"### 노드\n{node_lines or '  (없음)'}\n\n"
        f"### 관계\n{rel_lines or '  (없음)'}\n\n"
        "## 비즈니스 질문 (승인본)\n"
        f"{q_lines or '  (없음)'}\n\n"
        "## 지시\n"
        "1. 각 노드에 비즈니스 한국어 name 을 부여하라 (예: orders → 고객주문).\n"
        "2. 각 노드의 type 을 entity/event/kpi/property 중 적절히 지정하라.\n"
        "3. 각 노드의 answers 에 연관된 비즈니스 질문 id 를 채워라 (여러 개 가능).\n"
        "4. 관계를 한국어 label 로 풍부하게 기술하라.\n"
        "5. 필요하다면 노드나 관계를 추가해도 된다 (단, maps_from 은 실제 schema 테이블만).\n"
        "6. fk skeleton 에 없는 테이블을 maps_from 에 넣지 말라.\n"
    )


def _llm_enhance(
    skeleton: Ontology,
    business_questions: List[Dict[str, Any]],
) -> Optional[Ontology]:
    """Claude tool-calling 으로 fk_skeleton 을 enhance.

    D-6: RuntimeError / Exception → None 반환 (호출자가 fallback 처리).
    """
    system_prompt = (
        "너는 비즈니스 온톨로지 설계 전문가다. "
        "주어진 FK skeleton 과 비즈니스 질문을 바탕으로 온톨로지 노드/관계를 풍부하게 생성하라. "
        "반드시 emit_ontology tool 만 사용해 구조화된 결과를 반환하라."
    )
    user_prompt = _build_llm_user_prompt(skeleton, business_questions)

    try:
        result: Dict[str, Any] = _llm.call_tool(
            system=system_prompt,
            user=user_prompt,
            tool=ONTOLOGY_TOOL,
            temperature=0.2,
            max_tokens=4096,
        )
        nodes_raw = result.get("nodes", [])
        rels_raw = result.get("relations", [])

        # 최소 필드 방어적 변환
        nodes: List[OntologyNode] = []
        for n in nodes_raw:
            if not isinstance(n, dict):
                continue
            if not n.get("id") or not n.get("name"):
                continue
            nodes.append(
                {
                    "id": str(n["id"]),
                    "name": str(n["name"]),
                    "type": str(n.get("type", "entity")),
                    "maps_from": list(n.get("maps_from", [])),
                    "answers": list(n.get("answers", [])),
                }
            )

        relations: List[OntologyRelation] = []
        for r in rels_raw:
            if not isinstance(r, dict):
                continue
            if not r.get("source") or not r.get("target"):
                continue
            relations.append(
                {
                    "source": str(r["source"]),
                    "target": str(r["target"]),
                    "label": str(r.get("label", "")),
                }
            )

        return {"nodes": nodes, "relations": relations}

    except (RuntimeError, Exception):
        # D-6: LLM 실패 → None
        return None


# ---------------------------------------------------------------------------
# Step 3 — validate_ontology (결정론, 공개 export)
# ---------------------------------------------------------------------------


def _validate_ontology(
    ontology: Ontology,
    valid_q_ids: set[str],
) -> Ontology:
    """온톨로지 정합성 검증 + drop.

    규칙:
    1. relation.source / target 이 nodes 안에 존재해야 함 — 위반 relation drop.
    2. node.answers 의 q-id 가 valid_q_ids 에 포함 — 위반 id drop (node 자체는 유지).
    3. nodes dedup (id 기준, 첫 번째 유지).
    4. relations dedup (source+target+label 기준, 첫 번째 유지).
    """
    # --- dedup nodes ---
    seen_node_ids: set[str] = set()
    clean_nodes: List[OntologyNode] = []
    for n in ontology.get("nodes", []):
        nid = n.get("id", "")
        if not nid or nid in seen_node_ids:
            continue
        seen_node_ids.add(nid)
        # drop invalid q-ids from answers
        clean_answers = [qid for qid in n.get("answers", []) if qid in valid_q_ids]
        clean_nodes.append(
            {
                "id": nid,
                "name": n.get("name", nid),
                "type": n.get("type", "entity"),
                "maps_from": list(n.get("maps_from", [])),
                "answers": clean_answers,
            }
        )

    node_id_set: set[str] = {n["id"] for n in clean_nodes}

    # --- dedup + validate relations ---
    seen_rel_keys: set[tuple[str, str, str]] = set()
    clean_relations: List[OntologyRelation] = []
    for r in ontology.get("relations", []):
        src = r.get("source", "")
        tgt = r.get("target", "")
        lbl = r.get("label", "")
        if not src or not tgt:
            continue
        # drop if endpoints not in nodes
        if src not in node_id_set or tgt not in node_id_set:
            continue
        key = (src, tgt, lbl)
        if key in seen_rel_keys:
            continue
        seen_rel_keys.add(key)
        clean_relations.append({"source": src, "target": tgt, "label": lbl})

    return {"nodes": clean_nodes, "relations": clean_relations}


# ---------------------------------------------------------------------------
# 메인 노드 함수
# ---------------------------------------------------------------------------


def gen_ontology(state: Dict[str, Any]) -> Dict[str, Any]:
    """gen_ontology LangGraph 노드.

    입력 (state 키):
      - state['seed']['reference']['schema']: List[Table]
      - state['business_questions']: List[BusinessQuestion]

    출력 (partial state dict):
      - ontology: Ontology
      - review_ontology: ReviewState (initial pending)

    D-5: state 접근은 .get() 방어적.
    D-6: LLM 실패 → fk_skeleton + canned fallback merge.
    """
    # --- 입력 추출 ---
    seed = state.get("seed") or {}
    reference = seed.get("reference") or {}
    schema: List[Dict[str, Any]] = reference.get("schema") or []
    business_questions: List[Dict[str, Any]] = state.get("business_questions") or []

    # valid q-id 집합 (validate 에서 사용)
    valid_q_ids: set[str] = {
        q["id"] for q in business_questions if q.get("id")
    }

    # --- Step 1: fk_skeleton ---
    skeleton: Ontology = _fk_skeleton(schema)

    # --- Step 2: LLM enhance 또는 canned fallback ---
    final_ontology: Ontology

    if _api_key_available():
        enhanced = _llm_enhance(skeleton, business_questions)
        if enhanced is not None:
            # LLM 성공: enhanced 결과 사용
            final_ontology = enhanced
        else:
            # LLM 실패 (D-6): fk_skeleton 사용
            final_ontology = skeleton
    else:
        # API 키 없음: fk_skeleton + canned fallback merge
        canned = _load_canned_ontology()
        if skeleton["nodes"]:
            # schema 있으면 fk_skeleton 우선 사용 (canned 는 무시)
            final_ontology = skeleton
        else:
            # 빈 skeleton (빈 seed) → canned fallback
            final_ontology = canned

    # --- Step 3: validate ---
    validated: Ontology = _validate_ontology(final_ontology, valid_q_ids)

    return {
        "ontology": validated,
        "review_ontology": {"status": "pending", "feedback": [], "attempts": 0},
    }
