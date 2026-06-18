# A2 — ai-service (크리티컬 패스 콘텐츠) 작업 카드 v2

> 변경 결정 반영: LangGraph 유지 · 프론트 7화면 · S2 BRD = 로컬 편집.

## v2 변경점 (v1 대비)
노드 구조는 **변경 없음**. 단, 프론트 7화면이 출력을 바로 소비하므로 아래 **프론트 연동 메모**를 지키며 만든다.
- **S2 BRD** = `problem_profile`를 화면에 그대로 표시 → goals/pain_points/kpis/constraints에 **`sources`(근거 문서)** 가 잘 채워져야 EvidenceBadge가 산다. (할루시 가드로 빈 근거 제거 유지)
- **S3 질문 뱃지** = `business_questions[].data_status`(`available`/`missing:...`)가 프론트 뱃지·색을 결정 → 반드시 채울 것.
- **S4 Ontology 그래프(Cytoscape)** = `ontology.relations`가 깨끗해야(노드 id 양끝 존재) 그래프가 정상 렌더 → gen_ontology는 A1이 하지만, **A2의 질문 id가 안정적이어야**(q1,q2…) 온톨로지 `answers` 링크가 맞는다.

스택: **Python only · FastAPI · LangGraph** · 워크트리 `feat/content-nodes`.

## 셋업 (T0, A1과 함께)
```bash
# main에 schemas.py 커밋 후
git worktree add ../ai-content -b feat/content-nodes && cd ../ai-content && claude   # /init
# 환경: Claude API 키, DuckDB(+tpch) 로컬
```

## 타임박스
| 시간 | 작업 | 핸드오프 |
|---|---|---|
| 0–10 | `schemas.py` 공동, 워크트리/`/init`, Claude·DuckDB 셋업 | schemas → 공유 |
| 10–30 | intake(parse·extract·map-reduce·provenance·dedup·가드) — F-A·B | **profile.sources 채움(S2용)** |
| 30–45 | retrieve(dbgen·introspect/sample/profile·infer_expected·gap) — F-C / **머지** | → A1 seed/profile |
| 45–65 | gen_questions(build_context→LLM→validate→보정), **data_status·안정 id 필수** — F-D / **머지** | → A1 review①/온톨로지 (S3 뱃지) |
| 65–85 | 온톨로지 입력 결합·질문 폴리시·노드 I/O 정합 통합 지원 | A1과 합류 |
| 85–105 | E2E 디버그(갭→data_status→required_sources, sources→S2 근거) | — |
| 105–120 | 녹화 지원(캔드 데이터 준비) | — |

## Claude Code 세션 프롬프트

### A2-S1 — intake (정규화 + 근거 매핑)
```text
Domain Pack Builder ai-service(Python, FastAPI+LangGraph). schemas.py 사용. 계획 후 구현.
intake 노드(map-reduce, 코드+LLM):
1) documents는 {kind,title,content}(텍스트) 가정. 긴 content 청킹(size≈6000, overlap≈200).
2) extract(LLM, Claude tool-calling PROFILE_TOOL): 문서·청크별 goals/pain_points/kpis/systems/constraints/stakeholders(temperature=0.2).
3) merge(코드): goals/pain_points/kpis/constraints는 {text,sources:[title]}, systems/stakeholders는 문자열 집합, 문자 정규화 dedup.
4) map_provenance(코드 후보검색+LLM 검증, 문서 단위): 각 항목의 지지 문서 title 매핑.  ← S2 BRD의 근거 뱃지가 이걸 씀, 꼼꼼히.
5) drop_unsupported(근거 0개 제거) + cap(필드별 ≤8) + synthesize_summary→problem.
출력 state['problem_profile'], state['problem']. ABC상사 예시로 검증. 커밋.
```

### A2-S2 — retrieve (레퍼런스 + 갭)
```text
ai-service feat/content-nodes. 계획 후 구현.
retrieve 노드(LLM은 infer_expected만):
1) load_reference: DuckDB "INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.01)". introspect(테이블·컬럼·PK/FK), sample 5행/테이블, profile(핵심 컬럼), gold_questions 상수.
2) infer_expected(LLM): problem_profile → 필요 엔티티/필드 [{name,needed_fields,from}].
3) schema_gap(코드): expected vs reference → matched/missing/extra.
출력 state['seed']={reference,expected,gap,gold_questions}. 머지(→A1). 커밋.
```

### A2-S3 — gen_questions
```text
ai-service feat/content-nodes. 계획 후 구현.
gen_questions 노드:
1) build_context(코드): COVERAGE_AREAS(profile.kpis 기반) + gold 22 중 2~3 의역 앵커 + 스키마 카드+샘플 + seed.gap.
2) LLM(Claude tool-calling QUESTIONS_TOOL): question/category(enum)/rationale/linked_sources(실 컬럼)/data_status(available|missing:..). profile 우선, 앵커는 형태 참고만.
3) validate(코드): linked_sources 실제 컬럼인지·커버리지·dedup. 실패 시 피드백 붙여 1회 재생성.
4) **id 결정론 부여(q1,q2,...)** — 온톨로지 answers 링크가 이 id에 의존하므로 안정적으로.
출력 state['business_questions'](data_status 반드시 포함). 머지(→A1). 커밋.
```
