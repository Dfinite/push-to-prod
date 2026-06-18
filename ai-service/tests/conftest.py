"""pytest 공통 설정 — 공유 픽스처를 pytest fixture 로 노출.

각 노드 워크트리는 이 fixture 들로 상류를 기다리지 않고 테스트한다:
    def test_my_node(problem_profile, seed): ...
"""

from __future__ import annotations

import pytest

import fixtures as fx


@pytest.fixture
def pack_input():
    """PackInput — S1 intake 입력."""
    return fx.load_input()


@pytest.fixture
def problem_profile():
    """ProblemProfile — S1 golden / S2·S3 입력."""
    return fx.load_problem_profile()


@pytest.fixture
def seed():
    """Seed — S2 golden / S3 입력."""
    return fx.load_seed()


@pytest.fixture
def business_questions():
    """list[BusinessQuestion] — S3 golden."""
    return fx.load_business_questions()


@pytest.fixture
def reference_columns(seed):
    """{'table.column'} 집합 — linked_sources 실컬럼 검증용."""
    return {
        f"{t['table']}.{c['name']}"
        for t in seed["reference"]["schema"]
        for c in t["columns"]
    }
