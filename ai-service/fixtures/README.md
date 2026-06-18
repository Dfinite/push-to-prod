# fixtures — 공유 고정 입력 (병렬 개발용)

S1/S2/S3 를 **병렬 워크트리**에서 개발할 때, 각 노드가 상류 노드를 기다리지 않고
이 고정 픽스처를 입력으로 개발·테스트하기 위한 한 벌.

- 시나리오: **ABC상사 / `distribution` / TPC-H(DuckDB, sf=0.01)** — 전 문서·노드 일관.
- 모든 파일은 `schemas.py` 계약과 정합 (CI/검증 스크립트로 키 확인).
- **T10 이후 픽스처 형태(키) 변경 금지** — 변경 시 3 워크트리 동시 반영.

## 파일 ↔ 노드 매핑

| 파일 | 생산(golden out) | 소비(in) | 비고 |
|---|---|---|---|
| `sample_input.json` | — (그래프 진입) | **S1 intake** | `PackInput` {industry, documents[], problem:null} |
| `problem_profile.json` | **S1 intake** | **S2 retrieve**, **S3 gen_questions** | `{problem_profile: ProblemProfile, problem: str}` (state 출력 형태) |
| `seed.json` | **S2 retrieve** | **S3 gen_questions** | `Seed` — reference 스키마는 실제 tpch introspect (컬럼명 정확) |
| `business_questions.json` | **S3 gen_questions** | (A1 gen_ontology) | `{business_questions: BusinessQuestion[], review_questions}` — id 안정(q1,q2) |

## 사용 (각 워크트리 공통)

```python
from fixtures import (
    load_input,               # S1: PackInput
    load_problem_profile,     # S2/S3: ProblemProfile
    load_problem,
    load_seed,                # S3: Seed
    load_business_questions,  # 검증용 golden
)
```

## 병렬 개발 계약

- **S1 intake**: `load_input()` 로 개발 → 출력이 `problem_profile.json` 과 동형이어야(키·`sources` 채움). golden 으로 자기 출력 비교.
- **S2 retrieve**: `load_problem_profile()` 입력 → 출력이 `seed.json` 과 동형. `reference.schema` 컬럼은 실제 tpch 와 일치해야 함.
- **S3 gen_questions**: `load_problem_profile()` + `load_seed()` 입력 → `linked_sources` 는 `seed.json` 의 실제 컬럼이어야 validate 통과. `data_status`·안정 `id`(q1,q2…) 필수.

## 재생성

`seed.json` 의 reference(schema/samples/profile)는 tpch 에서 생성됨. 컬럼이 바뀌면
DuckDB introspection 으로 다시 만든다 (`INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.01)`).
`expected`/`gap`/`gold_questions` 는 시나리오 고정값(수기).
