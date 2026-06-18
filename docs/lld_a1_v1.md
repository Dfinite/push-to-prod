# A1 — Low-Level Design (v1)

> 범위: A1 영역(인프라 + 후반 노드 + FastAPI 서버).
> 정본 참조: `domain_pack_builder_PRD.md`, `노드별설계.md`, `domain_pack_builder_feature_list.md`, `task_A1_infra_v2.md`.
> 결정 사항은 별도 `decisions_a1_v1.md` 카탈로그에서 추적한다.

---

## 1. Scope

A1 책임 영역:
- 인프라: `DomainPackState`, `StateGraph` 조립, 라우터, `MemorySaver`, FastAPI 2엔드포인트.
- 후반 노드: `review_questions`(라이브), `gen_ontology`, `review_ontology`(스텁), `gen_workflows`, `gen_demo`, `review_final`(스텁), `assemble_export`.
- A2 영역(intake / retrieve / gen_questions)은 노드 시그니처(입출력 키)만 정의하고 구현은 위임.

Non-goals (A1 외부):
- 실제 LLM tool-calling 구현은 A1-S2/S3에서 노드별로 채움. A1-S1은 canned_pack 흘려보내기.
- Notion API, PII 마스킹, RAG, Neo4j 적재는 본 LLD 범위 밖.

---

## 2. 레이어 / 모듈 분해 (Clean Architecture 권장 구조)

현 worktree는 단일 디렉토리(`ai-service/{schemas,dto,app,graph,canned_pack}`)에 평탄하게 두는 MVP 단순화 버전이지만, 노드 수·validator 추가 시 아래 4레이어로 분리하는 것이 정합성·테스트 작성에 큰 이득.

```
ai-service/
├── domain/                       # 순수, 외부 무관 (테스트 가능)
│   ├── schemas.py                # ✅ TypedDicts (이미 존재, typing only)
│   ├── constants.py              # COVERAGE_AREAS, MAX_ATTEMPTS_DEFAULT
│   ├── validators.py             # validate_ontology / validate_workflows / build_required_sources
│   ├── rendering.py              # render_markdown_checklist(pack) -> str
│   └── policies.py               # assign_stable_ids, coerce_after_max_attempts
│
├── application/                  # orchestration — domain 호출, infra 주입
│   ├── graph.py                  # StateGraph 조립
│   ├── routers.py                # route_questions / route_ontology / route_final
│   └── nodes/
│       ├── intake.py             # [A2] map-reduce + provenance + 할루시 가드
│       ├── retrieve.py           # [A2] reference + gap
│       ├── gen_questions.py      # [A2] build_ctx → LLM → validate
│       ├── review_questions.py   # [A1] interrupt + ResumeDecision 처리
│       ├── gen_ontology.py       # [A1] fk_skeleton → LLM → validate
│       ├── review_ontology.py    # [A1] 자동승인 스텁
│       ├── gen_workflows.py      # [A1] LLM → 참조 검증
│       ├── gen_demo.py           # [A1] pick_top → LLM
│       ├── review_final.py       # [A1] 자동승인 스텁
│       └── assemble_export.py    # [A1] required_sources + markdown
│
├── interface/                    # HTTP 경계
│   ├── app.py                    # FastAPI
│   └── dto.py                    # Pydantic: PackInputDTO / ResumeDecisionDTO / RunResponse
│
├── infrastructure/               # 기술 세부 (port/adapter)
│   ├── llm_client.py             # Anthropic SDK wrapper + tool-calling
│   ├── reference_store.py        # DuckDB TPC-H loader [A2 retrieve가 사용]
│   ├── checkpointer.py           # MemorySaver factory (운영 시 교체 가능)
│   └── canned.py                 # canned_pack.json 로더 (USE_MOCK / 폴백)
│
└── tests/
    ├── unit/                     # domain 함수 단위 (pure → 빠름)
    ├── nodes/                    # 노드 단위 (state in → state out)
    └── integration/              # /runs → interrupt → /resume → done E2E
```

> **MVP는 단일 디렉토리**도 충분. 위 분리는 노드 실 구현이 들어가는 S2 이후에 점진적으로 적용한다.

---

## 3. 의존성 방향 (절대 규칙)

```
interface ──→ application ──→ domain
                  ↑
            infrastructure  (port/adapter)
```

- **domain**은 langgraph / anthropic / duckdb / fastapi import 금지. `schemas.py`는 이미 typing만 사용 (`from __future__ import annotations` + `typing` 모듈) ✓.
- **application**은 langgraph import 허용. anthropic 직접 import 대신 `LlmPort` 인터페이스 주입 (infra가 구현).
- **interface**는 application만 호출. domain은 DTO 변환에만 사용.
- 절대 금지: `domain → application/infra`, `application → interface`.

---

## 4. 데이터 흐름 (정상 경로)

```
HTTP POST /runs (PackInputDTO)
  ↓
interface.app.start_run  (Pydantic 422 검증 → model_dump dict)
  ↓
application.graph.invoke(initial_state, config={thread_id})
  → intake → retrieve → gen_questions → review_questions
                                          │
                                          interrupt(payload)  ←┐
  ↓                                                            │
state['__interrupt__'] 채워진 채 return                         │
  ↓                                                            │
interface.app._normalize → RunResponse{status:"interrupted",   │
                                       stage:"questions",      │
                                       payload}                │
  ↓                                                            │
[UI 결정 → POST /runs/{id}/resume]                              │
  ↓                                                            │
interface.app.resume_run                                        │
  → graph.invoke(Command(resume=ResumeDecision), config)        │
  → review_questions가 decision 받음 ──────────────────────────┘
     │
     route_questions(state):
       rejected & attempts<max → gen_questions  (loop)
       else                     → gen_ontology
  ↓
gen_ontology → review_ontology → route_ontology → "gen_workflows"
  ↓
gen_workflows → gen_demo → review_final → route_final → "assemble_export"
  ↓
assemble_export → END
  ↓
_normalize → RunResponse{status:"done", pack}
```

---

## 5. 핵심 계약 (API)

### 5.1 POST /runs

- Request: `PackInputDTO`

  ```python
  class PackInputDTO(BaseModel):
      industry: str = "distribution"        # MVP는 distribution, 운영 확장 여지
      documents: List[InputDocDTO] = []
      problem: Optional[str] = None
      max_attempts: int = Field(3, ge=1, le=5)
  ```

- Response (interrupted): `{ run_id, status:"interrupted", stage:"questions", payload: QuestionInterrupt }`
- Response (done): `{ run_id, status:"done", pack: DomainPackOutput }`
- 422 on invalid input / 500 on unexpected.

### 5.2 POST /runs/{run_id}/resume

- Request: `ResumeDecisionDTO`

  ```python
  class ResumeDecisionDTO(BaseModel):
      action: Literal["approve", "edit", "regenerate"]
      edited_items: Optional[List[BusinessQuestionDTO]] = None  # action='edit' 필수
      feedback:     Optional[str] = None                         # action='regenerate' 필수
  ```

- 응답 동일 RunResponse.
- action별 필수 필드 누락 시 **422** (silent fallback 금지 — decisions [[D-4]] 참고).

---

## 6. 노드 책임 명세 (A1 영역 5개)

| 노드 | 입력 (state 키) | 처리 | 출력 (state 키) | 결정/LLM 분담 |
|---|---|---|---|---|
| **review_questions** | `business_questions`, `problem_profile`, `review_questions.attempts`, `max_attempts` | `interrupt()` 호출 → decision 받음 → action별 분기. attempts ≥ max 시 `coerced=True` 마킹 ([[D-1]]) | `business_questions`(edit 시), `review_questions{status,feedback,attempts,coerced?}` | 전부 결정론 |
| **gen_ontology** | `seed.reference.schema` (FK), `business_questions` (승인본) | (a) `fk_skeleton`(코드: 테이블→노드, FK→관계) (b) Claude tool-calling `ONTOLOGY_TOOL` (c) `validate_ontology` — 관계 양끝·질문 커버·dedup·관계테이블(partsupp류) 노드 생략 허용 | `ontology{nodes,relations}`, `review_ontology` initial | 코드+LLM 하이브리드 |
| **gen_workflows** | `business_questions`, `ontology` | Claude tool-calling 2~3개. `validate_workflows`: `answers_question ∈ q.id`, `uses_nodes ⊆ ontology.nodes.id`, dedup | `workflows` | LLM + 결정론 검증 |
| **gen_demo** | `workflows`, `ontology` | `pick_top(workflows)` 결정론 → Claude로 narrative+steps+based_on 생성 | `demo_scenario` | 코드+LLM |
| **assemble_export** | `business_questions`, `ontology`, `seed.gap`, `workflows`, `demo_scenario` | `build_required_sources` = (q.linked_sources ∪ ontology.maps_from ∪ seed.gap.missing→needed). `render_markdown_checklist` | `required_sources`, `export.markdown` | 전부 결정론 |

스텁 노드 (`review_ontology`, `review_final`)는 자동승인만 — `{status:"approved", feedback:[], attempts:0}` 반환.

---

## 7. 라우터

| 라우터 | 진입 노드 | 분기 조건 | 출구 |
|---|---|---|---|
| `route_questions` | review_questions | `status=='rejected'` AND `attempts < max_attempts` | `gen_questions` (재생성 루프) |
|  | | else (approve / edit / 강제 통과) | `gen_ontology` |
| `route_ontology` | review_ontology | 항상 (자동승인) | `gen_workflows` |
| `route_final` | review_final | 항상 (자동승인) | `assemble_export` |

> 강제 통과 시 review_questions 출력에 `coerced=True` 플래그가 함께 들어 있다 ([[D-1]]). 라우터 자체는 단순 분기만 — 정책은 노드 안에서 결정.

---

## 8. 결정 사항 (요약 — 상세는 [[decisions_a1_v1]])

| ID | 영역 | 결정 |
|---|---|---|
| D-1 | review_questions | max_attempts 도달 시 `coerced=True` 추가, status는 `"rejected"` 유지 |
| D-2 | interface | 입력 검증을 `dto.py` Pydantic 모델로 (422 가능) |
| D-3 | infra | `langgraph<0.3` 상한 핀 |
| D-4 | interface | ResumeDecision action을 Literal로 강제, 누락 필드 422 |
| D-5 | nodes | state 접근은 `.get(key, default)` 방어적 |
| D-6 | nodes | LLM 실패: `gen_questions` 1회 재시도 / 나머지는 빈 결과 + 다음 단계 통과 |
| D-7 | review_questions | edit 시 빈 `q.id`는 백엔드가 `q{maxN+1}` 결정론적 부여 |
| D-8 | schemas 정본 | `coerced` 키는 runtime extra (정식화 시 `NotRequired[bool]` 추가 — 3인 합의 필요) |

---

## 9. 테스트 전략

| 층 | 대상 | 도구 |
|---|---|---|
| Unit (pure) | validators / rendering / policies (`assign_stable_ids` 등) | pytest, fixtures (state in→out) |
| Node | 각 노드 함수에 state dict 주입 → 출력 검증 (review_questions: approve/edit/regenerate/coerced 4 케이스) | pytest |
| Graph | `GRAPH.invoke()` 전체 (canned mode) — interrupted 분기 + resume 후 done | pytest + langgraph in-memory |
| API | `TestClient(app)` — /runs → interrupted → /resume(approve/edit/regenerate) → done. 422 케이스 (`regenerate` + feedback="") | pytest + httpx |
| Contract | canned_pack.json 의 키가 `DomainPackOutput` 과 정확히 일치 | pytest schema-check |

**핵심 보호 대상** (회귀 위험 큰 영역):
- `route_questions` 분기 정책 (max_attempts 도달 처리)
- `_normalize` 의 `__interrupt__` 추출 (LangGraph 버전 변경 시 깨질 수 있음 → [[D-3]] 핀)
- ID 사슬 (q → ontology.answers → workflow.answers_question)
- ResumeDecision 누락/잘못 시 fallback

---

## 10. 확장 포인트

- **산업별 reference store** — `infrastructure/reference_store.py` 에 `load_reference(industry) -> Reference`. 현재 `distribution=TPC-H` 한 케이스만.
- **LLM provider 교체** — `LlmPort.tool_call(tool_def, input) -> dict`. 현재 Anthropic 가정.
- **Checkpointer 교체** — `MemorySaver`(MVP) → SQLite/Postgres(운영). `app.py`가 factory 사용.
- **review②③ 활성화** — `review_ontology` / `review_final` 노드를 `review_questions` 패턴(interrupt + ResumeDecision)으로 교체. 라우터 분기만 추가.

---

## 11. Open Questions

1. ~~`state['__interrupt__']` 가 LangGraph 0.2.40 ~ 0.2 최신 사이에 안정 API인가?~~ → **S1 E2E 검증으로 0.2.x 동작 확인** ([[work-log/a1-s1-2026-06-18#2-검증-결과]]). 0.3 / 1.x 마이그레이션 시 재검토는 [[decisions_a1_v1#D-3]] 핀 해제와 동시.
2. PII 마스킹은 어디서? (intake parser 이전 vs 출력 직전) — feature_list F-K3 향후로 분류.
3. ~~canned_pack.json 에 interrupt 페이로드도 같이 둘지?~~ → **불필요.** 현 graph가 interrupt 페이로드를 매 invoke마다 동적 구성. W의 USE_MOCK은 첫 `/runs` 응답을 fixture로 캡처하면 됨.
4. `partsupp` 류 관계테이블의 ontology 노드 생략 정책 — `validate_ontology` 코드 작성 시 (S2) 명시.
5. `coerced` 정식화 시점 — 운영 전환 단계에서 schemas.py에 `NotRequired[bool]` 추가 ([[decisions_a1_v1#D-8]]).

---

## 12. S1 검증 완료 (2026-06-18)

A1-S1 작업 산출물 및 검증 결과 상세: [[work-log/a1-s1-2026-06-18]]

요약:
- 워크트리 untracked: `ai-service/{app,dto,graph}.py`, `requirements.txt` (검증 통과, 커밋 대기)
- curl E2E: `/runs` → interrupted(stage=questions, payload 3종) → `/resume(approve)` → done(pack 7필드 + id 사슬 무결성) **모두 통과**
- 트러블슈팅에서 [[decisions_a1_v1#D-9]] (개발 포트), [[decisions_a1_v1#D-10]] (응답 파싱) 신규 결정.
