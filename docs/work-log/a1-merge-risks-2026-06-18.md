# A1-S2/S3 머지 후 리스크 점검 (2026-06-18)

> 머지 commit: `08cf720 feat(a1-s2/s3): merge gen_ontology + workflows/demo/export`
> 관련 work-log: [[a1-s1-2026-06-18]], [[a1-s2-2026-06-18]], [[a1-s3-2026-06-18]]
> 관련 decisions: [[decisions_a1_v1]]

---

## 0. 한 줄

S2/S3 머지 + E2E 검증 통과 후 식별된 **총 21건 리스크의 분류·처리 방침** 기록. 즉시 해결 가능 2건, 환경 의존 3건, 도메인/입력 데이터 7건, 시스템적/장기 5건, 운영 검토 4건.

---

## 1. 점검 컨텍스트

- `git fetch + pull` (main 워킹디렉터리·워크트리): **main 변경 없음**, **A2 추가 푸시 없음**.
- 새 origin 브랜치 발견: `feat/gen-questions`, `feat/retrieve` — A2가 노드별 워크트리 패턴으로 작업 중. **현재 우리(feat/graph-infra)와 충돌 없음**.
- D-11 공용 모듈 동기화 검증: `schemas.py` / `llm.py` / `nodes/__init__.py` 모두 `origin/feat/content-nodes` 와 빈 diff ✅
- E2E (port 8888, no `ANTHROPIC_API_KEY` → canned fallback):
  - `/runs` → interrupted(stage=questions)
  - `/resume(approve)` → done, 7 필수 필드, id 사슬 무결성 3건 모두 True

---

## 2. 리스크 분류

### 2.1 즉시 해결 가능 (별도 후속 commit으로 처리)

| ID | 리스크 | 해결 방법 |
|---|---|---|
| R-2 | `decisions_a1_v1.md` 한눈에 보기 표에서 D-12/D-13이 D-5와 D-6 사이로 끼어 들어가 ID 순서 깨짐 | Edit으로 D-11 뒤로 재정렬 |
| R-13 | `lld_a1_v1.md` §12 "S1 검증 완료"가 S2/S3 머지 결과를 아직 반영 안 함 | §12 갱신 또는 §13 신규 |

### 2.2 환경/외부 의존 (해커톤 당일 또는 운영 단계에서 검증)

| ID | 리스크 | 방침 |
|---|---|---|
| R-1 | LLM enhance 경로(`ANTHROPIC_API_KEY` 있는 환경)의 `gen_ontology`/`gen_workflows`/`gen_demo` 실 호출 미검증 | 키 발급 후 수동 검증. D-6 fallback이 있어 그래프 동작은 깨지지 않음 |
| R-7 | `llm.get_client`의 `@lru_cache(maxsize=1)`이 런타임 키 변경 시 동작 — RuntimeError는 캐시 안 되지만 정상 동작은 캐시됨 | 운영 전환 시 cache invalidation 검토 또는 `lru_cache` 제거 |
| R-10 | A2가 `llm.py` 시그니처를 또 바꿀 경우 우리 `nodes/*` 가 깨짐 | D-11 정책상 수동 sync. CI에서 contract test 추가 검토 |

### 2.3 도메인 / 입력 데이터 (대부분 A2 머지 시점에 영향 확정)

| ID | 리스크 | 방침 |
|---|---|---|
| R-3 | retrieve stub이 빈 seed → gen_ontology의 fk_skeleton이 작동 못함 → canned fallback. `required_sources.needed=0` 가짜 결과 | A2 `feat/retrieve` 머지 후 자동 해소 |
| R-6 | canned_pack은 TPC-H/ABC상사 시나리오 전용. foodco 입력 시 fallback ontology가 도메인 불일치 | LLM 키 없으면 foodco 시연 부적합. **D-15(industry 분기) 신설 필요** |
| R-8 | canned fallback의 q-id가 실제 `business_questions` 와 불일치 시 ontology.answers 전부 drop (S2 보고서 §6) | 정상 graceful degradation. 노드는 생존, answers만 비워짐. 별 조치 불요 |
| R-15 | 40건 .md 입력 시 청킹/map-reduce 필수 (총 ~300KB, STT 1건이 30KB) | A2 intake 책임. uvicorn `--timeout-keep-alive` 검토 |
| R-16 | 단일 POST body가 비대 → 타임아웃·메모리 부담 | multi-part upload 또는 thread 누적 검토. 현 계약 유지 + 청킹은 intake 내부 |
| R-17 | `industry` 키 두 가지(distribution vs foodservice) — reference loader 분기 부재 | **D-15 신설** + A2 retrieve가 industry로 branch |
| R-19 | README.md도 입력 후보. 시나리오 정의서로 가장 가치 있는 단일 문서 | `kind="brd"` 또는 `"readme"` (schemas는 자유 str이라 OK). intake가 가중치 부여 검토 |

### 2.4 시스템적 / 장기 (MVP 허용, 운영 전환 시 재검토)

| ID | 리스크 | 방침 |
|---|---|---|
| R-5 | `graph.py`의 `GRAPH = build_graph()`가 module-level → 단위 테스트 격리 어려움 | 알려진 리스크. 운영 전환 시 factory 함수로 분리 |
| R-9 | `fk.ref` 형식이 `table.col` 비표준 케이스 (S2 보고서 §6) | orphan relation drop으로 안전 처리됨 |
| R-11 | `assemble_export.required_sources.available`에 테이블명(`orders`)과 컬럼 경로(`orders.o_orderdate`) 혼재 (S3 보고서 §6) | W(프론트)와 협의 후 결정 — S7 Export UI에서 중복처럼 보일 수 있음 |
| R-14 | 저장소 부모 `CLAUDE.md` §1/§3에 S2/S3 머지 사실 미반영 | 우선순위 낮음. 차기 정리 라운드 |
| R-21 | 외부 수정자(linter/사용자/다른 세션) 개입 패턴 — 이번 세션 5+회 | Write/Edit 전 ls/Read 선행 컨벤션화. 별도 결정 후보 |

### 2.5 운영 검토 (T105 코드 프리즈 이후)

| ID | 리스크 | 방침 |
|---|---|---|
| R-4 | `__pycache__/` 워크트리에 남아 있음 | `.gitignore`로 무시. 영향 없음 |
| R-12 | graph.py 스텁 교체 전까지 E2E가 canned 응답 (S3 보고서 §6) | **이미 해결 완료** (08cf720 commit) |
| R-18 | `stt` kind가 schemas 주석 enum에 없음 | 현재 `kind: str` 자유라 OK. 운영 단계에서 `Literal` 좁히는 결정 |
| R-20 | canned_pack은 tpch 전용 — foodco 시연 부적합 | foodco용 `canned_pack_foodco.json` 추가 검토 (운영) 또는 LLM 호출 강제 |

---

## 3. 다음 작업 (후속 commit으로 묶음 처리)

순서대로:
1. **R-2 해결**: decisions 표 D-12/D-13 위치 재정렬 (D-11 뒤로)
2. **R-13 해결**: lld_a1_v1.md §12 갱신 — S2/S3 머지 + E2E 통과 표기
3. **D-14 신설**: 입력 디렉터리 → InputDoc[] CLI 변환 책임 = 클라이언트(W 또는 별도 script). 백엔드는 InputDoc[] 만 수용.
4. **D-15 신설**: industry 분기 정책 — `"distribution"`(TPC-H 정본) / `"foodservice"`(foodco) — retrieve loader가 industry로 branch
5. **(옵션) scripts/dir_to_packinput.py**: foodco/tpch 디렉터리 → InputDoc[] 변환 CLI (개발/검증용)
6. **A2 통합 시점 점검**: A2가 `feat/retrieve` / `feat/gen-questions` 머지 시점에 D-11 sync 재확인 (`llm.py` / `nodes/__init__.py` 무변경 검증)

---

## 4. 미해결 Open Questions

- **Q-A**: foodco 도메인 시연 시 LLM 토글 없이 의미 있는 데모 가능한가? 현 답: **부적합** — canned가 tpch 전용. 해커톤 당일 시연 시 ANTHROPIC_API_KEY 필수 또는 canned_pack_foodco 추가 작업 필요.
- **Q-B**: A2가 워크트리 3개로 분기 작업 중(`feat/intake`, `feat/retrieve`, `feat/gen-questions`). A1 통합 머지 시점에 어느 브랜치를 head로 둘지? — A2 owner 결정 사안.
- **Q-C**: 머지 결과 기록의 단일 정본 — lld §12 갱신 vs work-log 신규. 현재 둘 다 분산. 차기 라운드에 통합 권장.

---

## 5. 머지 후 상태 요약

```
brain (feat/graph-infra):
  08cf720 feat(a1-s2/s3): merge gen_ontology + workflows/demo/export   <-- HEAD
  4f78c38 chore(a1): sync llm.py + nodes/__init__.py with A2 baseline (D-11)
  f65e37d feat(a1-s1): FastAPI + LangGraph 10-node scaffold
  e0a1fed Reorganize docs into docs/
  867f7ef chore: initial commit

A2 (feat/content-nodes):
  f6118c2 test: pytest scaffold + fixture-contract baseline
  c421b20 feat: shared Claude client + tool-calling helper
  53fb2f1 chore: nodes/ skeleton
  ...

A2 신규 분기:
  feat/retrieve     ← A2-S2 진행 중 (베이스에서 분기)
  feat/gen-questions ← A2-S3 진행 중
```

A1은 후반 노드(`gen_ontology` ~ `assemble_export`) 모두 실 구현 + E2E 통과. A2 머지 시점이 전체 파이프라인의 다음 단계.
