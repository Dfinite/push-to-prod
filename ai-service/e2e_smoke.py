"""E2E 스모크 러너 — 실제 data/ 문서 + 실 Postgres 로 3노드 파이프라인 실행.

intake → retrieve(실 DB introspect) → gen_questions 를 순서대로 돌리고
각 단계 산출물을 out/<scenario>/NN_*.json 으로 떨군다 (단계별 확인용).

사용:
    uv run python e2e_smoke.py foodco      # demo_foodco_stock
    uv run python e2e_smoke.py tpch        # tpch
    uv run python e2e_smoke.py all         # 둘 다

입력 문서: <repo>/data/<...>-ontology-data/{emails,stt,chats}/*.md
  - kind = 상위 폴더명(email|stt|chat), title = "<폴더>/<파일stem>" (provenance 고유키), content = 파일 본문.
환경변수: ANTHROPIC_API_KEY(.env), REFERENCE_DATABASE_URL(스크립트가 시나리오별로 주입).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# ai-service 루트(이 파일 위치)를 import 경로에 보장.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# data/ 는 메인 워크트리(push-to-prod)에 있다. 이 워크트리(ai-content)에는 없으므로
# 메인 워크트리 경로를 명시적으로 가리킨다(환경변수로 override 가능).
_DEFAULT_DATA_ROOT = Path(
    os.environ.get("E2E_DATA_ROOT", "/Users/sr-dfnt/Documents/GitHub/push-to-prod/data")
)

# 시나리오 정의: data 폴더명 + 레퍼런스 DB DSN.
_DB_PW = os.environ.get("E2E_DB_PASSWORD", "Audghkrgka!")
SCENARIOS: Dict[str, Dict[str, str]] = {
    "foodco": {
        "data_dir": "foodco-stock-ontology-data",
        "industry": "food_distribution",
        "dsn": f"host=db.dfinite.ai port=50016 user=postgres password={_DB_PW} dbname=demo_foodco_stock",
    },
    "tpch": {
        "data_dir": "tpch-ontology-data",
        "industry": "distribution",
        "dsn": f"host=db.dfinite.ai port=50015 user=postgres password={_DB_PW} dbname=tpch",
    },
}

# 폴더명 → InputDoc.kind
_KIND_BY_DIR = {"emails": "email", "stt": "stt", "chats": "chat"}


def load_documents(data_dir: Path) -> List[Dict[str, str]]:
    """data/<scenario>/{emails,stt,chats}/*.md → InputDoc[] (파일명 정렬, 폴더 prefix 로 title 고유화)."""
    docs: List[Dict[str, str]] = []
    for sub, kind in _KIND_BY_DIR.items():
        d = data_dir / sub
        if not d.is_dir():
            continue
        for md in sorted(d.glob("*.md")):
            docs.append(
                {
                    "kind": kind,
                    "title": f"{sub}/{md.stem}",
                    "content": md.read_text(encoding="utf-8"),
                }
            )
    return docs


def _dump(out_dir: Path, name: str, payload: Any) -> None:
    path = out_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    └─ wrote {path.relative_to(_HERE)}")


def _summarize_profile(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        k: (len(p.get(k, [])) if isinstance(p.get(k), list) else p.get(k))
        for k in ("goals", "pain_points", "kpis", "constraints", "systems", "stakeholders")
    }


def run_scenario(key: str, out_root: Path) -> Dict[str, Any]:
    cfg = SCENARIOS[key]
    data_dir = _DEFAULT_DATA_ROOT / cfg["data_dir"]
    out_dir = out_root / key
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== [{key}] scenario start ===")
    print(f"  data: {data_dir}")
    print(f"  db:   {cfg['dsn'].split('password=')[0]}... dbname={cfg['dsn'].split('dbname=')[-1]}")

    # 시나리오별 레퍼런스 DB 주입 (retrieve.load_reference 가 환경에서 읽음).
    os.environ["REFERENCE_DATABASE_URL"] = cfg["dsn"]

    # 지연 import — 환경(.env/REFERENCE_DATABASE_URL) 세팅 후.
    from nodes.intake import intake
    from nodes.retrieve import retrieve
    from nodes.gen_questions import gen_questions

    timings: Dict[str, float] = {}
    summary: Dict[str, Any] = {"scenario": key, "industry": cfg["industry"]}

    # --- 0. 입력 문서 ---
    docs = load_documents(data_dir)
    if not docs:
        raise SystemExit(f"입력 문서가 없습니다: {data_dir}")
    pack_input = {"industry": cfg["industry"], "documents": docs, "problem": None}
    _dump(out_dir, "00_input.json", pack_input)
    by_kind: Dict[str, int] = {}
    for d in docs:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
    summary["input"] = {"total_docs": len(docs), "by_kind": by_kind}
    print(f"  [0] input: {len(docs)} docs {by_kind}")

    # --- 1. intake (LLM extract + verify) ---
    t = time.time()
    print("  [1] intake (LLM extract/verify per doc·chunk) ...")
    intake_out = intake(pack_input)
    timings["intake"] = round(time.time() - t, 1)
    _dump(out_dir, "01_intake.json", intake_out)
    prof = intake_out["problem_profile"]
    summary["intake"] = {
        "profile_counts": _summarize_profile(prof),
        "problem": intake_out.get("problem", ""),
        "seconds": timings["intake"],
    }
    print(f"      profile counts: {_summarize_profile(prof)}  ({timings['intake']}s)")

    # --- 2. retrieve (실 Postgres introspect + LLM expected + gap) ---
    t = time.time()
    print("  [2] retrieve (introspect DB + LLM infer_expected + schema_gap) ...")
    retr_out = retrieve({"problem_profile": prof})
    timings["retrieve"] = round(time.time() - t, 1)
    _dump(out_dir, "02_retrieve_seed.json", retr_out["seed"])
    seed = retr_out["seed"]
    ref = seed["reference"]
    gap = seed["gap"]
    summary["retrieve"] = {
        "reference_db": ref.get("name"),
        "tables": len(ref.get("schema", [])),
        "table_names": [t_["table"] for t_ in ref.get("schema", [])],
        "expected_entities": len(seed.get("expected", {}).get("entities", [])),
        "gap": {"matched": len(gap.get("matched", [])), "missing": len(gap.get("missing", [])), "extra": len(gap.get("extra", []))},
        "seconds": timings["retrieve"],
    }
    print(f"      ref={ref.get('name')} tables={len(ref.get('schema', []))} "
          f"gap(matched/missing/extra)={summary['retrieve']['gap']}  ({timings['retrieve']}s)")

    # --- 3. gen_questions (LLM + 결정론 후처리) ---
    t = time.time()
    print("  [3] gen_questions (LLM + sanitize/topup) ...")
    gq_out = gen_questions({"problem_profile": prof, "seed": seed})
    timings["gen_questions"] = round(time.time() - t, 1)
    _dump(out_dir, "03_gen_questions.json", gq_out)
    bqs = gq_out["business_questions"]
    cats = sorted({q["category"] for q in bqs})
    summary["gen_questions"] = {
        "count": len(bqs),
        "categories": cats,
        "data_status": {q["id"]: q["data_status"] for q in bqs},
        "seconds": timings["gen_questions"],
    }
    print(f"      {len(bqs)} questions, {len(cats)} categories  ({timings['gen_questions']}s)")
    for q in bqs:
        print(f"        {q['id']} [{q['category']}] ({q['data_status']}) {q['question']}")

    summary["total_seconds"] = round(sum(timings.values()), 1)
    _dump(out_dir, "summary.json", summary)
    print(f"  === [{key}] done in {summary['total_seconds']}s ===")
    return summary


def main(argv: List[str]) -> int:
    arg = (argv[1] if len(argv) > 1 else "all").lower()
    keys = list(SCENARIOS) if arg == "all" else [arg]
    bad = [k for k in keys if k not in SCENARIOS]
    if bad:
        raise SystemExit(f"unknown scenario(s): {bad}. choices: {list(SCENARIOS)} or 'all'")

    out_root = _HERE / "out"
    out_root.mkdir(exist_ok=True)
    results = []
    for k in keys:
        results.append(run_scenario(k, out_root))

    print("\n===== E2E SMOKE SUMMARY =====")
    for r in results:
        gq = r.get("gen_questions", {})
        print(f"  [{r['scenario']}] db={r['retrieve']['reference_db']} "
              f"tables={r['retrieve']['tables']} questions={gq.get('count')} "
              f"cats={len(gq.get('categories', []))} total={r['total_seconds']}s")
    (out_root / "_summary_all.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  full summary → {(out_root / '_summary_all.json').relative_to(_HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
