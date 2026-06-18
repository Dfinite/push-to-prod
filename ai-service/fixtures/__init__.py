"""공유 픽스처 로더.

S1/S2/S3 병렬 워크트리가 상류 노드를 기다리지 않고 고정 입력으로 개발·테스트하기 위한
한 벌(ABC상사 / distribution / TPC-H 시나리오). 모든 파일은 schemas.py 계약과 정합.

사용 예:
    from fixtures import load_problem_profile, load_seed
    profile = load_problem_profile()            # ProblemProfile (S2/S3 입력)
    seed = load_seed()                           # Seed (S3 입력)
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name: str) -> Any:
    with open(os.path.join(_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def load_input() -> Dict[str, Any]:
    """PackInput — S1 intake 입력 (진입점)."""
    return _load("sample_input.json")


def load_problem_profile() -> Dict[str, Any]:
    """ProblemProfile — S1 golden 출력 / S2·S3 입력."""
    return _load("problem_profile.json")["problem_profile"]


def load_problem() -> str:
    """problem 요약 문자열 — S1 golden 출력."""
    return _load("problem_profile.json")["problem"]


def load_seed() -> Dict[str, Any]:
    """Seed(reference·expected·gap·gold_questions) — S2 golden 출력 / S3 입력."""
    return _load("seed.json")


def load_business_questions() -> List[Dict[str, Any]]:
    """list[BusinessQuestion] — S3 golden 출력 / (A1) gen_ontology 입력."""
    return _load("business_questions.json")["business_questions"]
