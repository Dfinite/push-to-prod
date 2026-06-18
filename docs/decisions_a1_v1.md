# A1 — 결정 카탈로그 (v1)

> A1 영역(인프라 + 후반 노드 + 서버)에서 누적된 비사소 결정을 옵션·확정·근거·되돌릴 수 있는지·revisit 트리거로 추적한다.
> 관련 LLD: [[lld_a1_v1]]
> 관련 작업 카드: `task_A1_infra_v2.md`

## 한눈에 보기

| ID | 영역 | 결정 요약 | 되돌릴 수 있나 | 상태 |
|---|---|---|---|---|
| D-1 | review_questions | max_attempts 도달 시 `coerced=True` 마킹, status는 `"rejected"` 유지 | 쉬움 (1줄 변경) | 적용 |
| D-2 | interface | 입력 검증을 `dto.py` Pydantic 모델로 (FastAPI 422 자동화) | 쉬움 (body: dict 회귀) | 적용 |
| D-3 | infra | `langgraph>=0.2.40,<0.3` 상한 핀 | 쉬움 (상한 해제) | 적용 |
| D-4 | interface | ResumeDecision action을 `Literal`로 강제, 필수 필드 누락 시 422 | 쉬움 (validator 제거) | 적용 |
| D-5 | nodes | state 접근은 `.get(key, default)` 방어적 | 쉬움 | 적용 |
| D-6 | nodes | LLM 실패: `gen_questions` 1회 재시도 / `ontology·workflows·demo` 빈 결과 + 다음 단계 통과 | 중간 (review②③ 라이브 시 재검토) | 적용 (docstring 명시) |
| D-7 | review_questions | edit 시 빈 `q.id`는 백엔드가 `q{maxN+1}` 결정론적 부여 | 쉬움 | 적용 |
| D-8 | schemas 정본 | `coerced` 키는 runtime extra (`NotRequired[bool]` 정식화는 보류) | 쉬움 (3인 합의 후 schemas 변경) | 보류 (정식화 미적용) |
| D-9 | dev infra | 개발 머신에서는 동적 빈 포트 탐색, 운영 표준 포트는 :8000 유지 | 쉬움 | 적용 (검증 스크립트) |
| D-10 | 검증 스크립트 | curl 응답은 파일로 직접 받고 shell 변수 경유 금지 | 쉬움 (관행) | 적용 |
| D-11 | 병렬 워크트리 | `nodes/__init__.py` + `llm.py` 는 양 워크트리(A1/A2) 동일 내용 유지, A2 가 owner | 중간 (owner 이동 가능) | 적용 |

---

## D-1. max_attempts 도달 시 review_questions 출력 형태

**문제.** PRD §11이 "재시도는 `max_attempts`로 상한, 초과 시 강제 통과"라고 했지만 강제 통과 시 `review_questions.status`를 어떻게 표기할지 정책 부재.

**옵션.**

1. status를 `"approved"`로 덮어쓰고 다음 노드로 진행.
2. status는 `"rejected"` 유지하고 `coerced=True` 플래그 추가.
3. 별도 `"force_passed"` 상태값 도입.

**확정: 옵션 2.**

**근거.**
- 감사 추적: "사용자가 승인해서 통과"와 "한도 도달로 강제 통과"는 의미가 다르다. 옵션 1은 두 케이스를 구분 못 함 → 사후 분석/리텔링 시 손실.
- schemas.py 변경 최소화: 옵션 3은 `ReviewState.status`의 `Literal` 확장이 필요해 정본 변경 + 모든 사용처 검토. 옵션 2는 runtime extra key만 추가.
- 라우터는 status·attempts만 보면 분기 결정 가능 — coerced 플래그가 라우터 동작을 바꾸지 않음.

**되돌릴 수 있는지.** 쉬움. `coerced` 플래그를 읽는 코드가 없으면 무해. 옵션 1로 회귀 시 `review_questions.review_out["status"] = "approved"` 1줄.

**Revisit 트리거.** review②③가 라이브로 활성화될 때(F-G7/G8) 동일 패턴(coerced) 일관 적용 검토.

**적용 위치.** `graph.py::review_questions` (regenerate 분기 끝).

---

## D-2. 입력 검증 — Pydantic DTO 도입

**문제.** `app.py`가 `body: Dict[str, Any]`로 받음 → 잘못된 입력에 대해 422를 못 보내고 노드 안에서 KeyError/AttributeError 발생. 디버깅 비용.

**옵션.**

1. dict 그대로 유지 (MVP 절충).
2. `dto.py`에 Pydantic 모델 추가, schemas TypedDict와 필드 1:1 매핑.
3. schemas.py에 직접 Pydantic 모델 추가.

**확정: 옵션 2.**

**근거.**
- schemas.py는 "런타임 의존성 없음 — 단순 import 가능"이 명시된 정본 ([schemas.py:4](../.claude/worktrees/graph-infra/ai-service/schemas.py#L4)). Pydantic을 schemas에 넣으면 의존성 폭발 + W의 TS 변환(`src/types/index.ts`) 시 노이즈.
- 옵션 1은 데모 도중 잘못된 페이로드로 silent fallback → 디버깅 시간 소모.
- 옵션 2는 schemas TypedDict의 typing 계약을 유지하면서 경계에서만 검증.

**되돌릴 수 있는지.** 쉬움. `app.py`의 `body: PackInputDTO`를 `body: Dict[str, Any]`로 회귀.

**Revisit 트리거.** schemas.py에 Pydantic을 받아들이는 합의가 생기면(예: W도 zod 외에 OpenAPI 자동 생성 사용으로 전환) DTO 위치 재검토.

**적용 위치.** `dto.py` 신규, `app.py::start_run`/`resume_run` 시그니처 변경.

---

## D-3. langgraph 버전 상한 핀

**문제.** `requirements.txt`가 `langgraph>=0.2.40`로만 핀 → 자동 업그레이드로 0.3.x / 1.x 진입 시 `Command`/`interrupt`/`__interrupt__` API가 깨질 수 있음.

**옵션.**

1. 핀 그대로 (`>=0.2.40`).
2. `>=0.2.40,<0.3` 상한.
3. `==0.2.x` 완전 고정.

**확정: 옵션 2.**

**근거.**
- 옵션 1: 0.3 / 1.x 자동 진입 → API 깨짐 → 해커톤 당일 사고.
- 옵션 3: 0.2.x 내 보안 패치도 못 받음. 너무 좁음.
- 옵션 2: 0.2 마이너 패치는 받되 0.3 메이저는 의도적으로만.

**되돌릴 수 있는지.** 쉬움. 상한 제거 1줄.

**Revisit 트리거.** 운영 전환 시 langgraph 0.3 / 1.x changelog 확인 후 마이그레이션 이슈 분리해서 상한 올림.

**적용 위치.** `requirements.txt`.

---

## D-4. ResumeDecision action 강제 검증

**문제.** UI가 잘못된 페이로드(예: `action='regenerate'`인데 `feedback` 빈 값) 보내면 노드 안에서 default `action='approve'`로 silent 통과. 데모 중 "왜 재생성 안 되지?" 사고.

**옵션.**

1. silent fallback 유지 (`action`을 default `"approve"`로).
2. `Literal["approve","edit","regenerate"]` + 필수 필드 누락 시 422.
3. 422 대신 `review_questions` 출력에 에러만 기록하고 통과.

**확정: 옵션 2.**

**근거.**
- 데모 시연 시 422가 즉시 보이는 게 silent fallback보다 디버깅에 유리.
- W의 USE_MOCK fixture가 잘못되어도 422로 즉시 발견 → 통합 직전 발견 가능성.
- 옵션 3은 그래프 진행은 되지만 사용자 의도와 다른 결과 → 더 나쁜 UX.

**되돌릴 수 있는지.** 쉬움. `dto.py::ResumeDecisionDTO._enforce_per_action` 제거.

**Revisit 트리거.** UI가 항상 정합한 페이로드를 보장하는 단계가 되면 (e.g., 폼 zod 검증) 422 빈도가 0에 수렴 → 그래도 server-side 검증은 유지 권장.

**적용 위치.** `dto.py::ResumeDecisionDTO` + `app.py`.

**Side effect (통합 전 W에 전달 필요).** USE_MOCK fixture가 `regenerate` 시 `feedback`을 빈 문자열이 아닌 의미 있는 값으로 채워야 함.

---

## D-5. state 접근 방어성

**문제.** `review_questions` 노드가 `state["problem_profile"]`로 직접 인덱싱. `DomainPackState`는 `total=False`라 키가 없을 수 있음 → KeyError. 현재는 intake가 항상 채워 안전하지만 노드 순서 변경 시 깨짐.

**옵션.**

1. 직접 인덱싱 유지 (intake 선행을 invariant로 가정).
2. `.get(key, default)`로 방어적 접근.

**확정: 옵션 2.**

**근거.**
- TypedDict total=False는 명시적 옵셔널 — 인덱싱 자체가 정합성 위반.
- 비용 0 (1줄 변경).
- 향후 노드 순서 변경 / review_brd 노드 삽입 시 재발 방지.

**되돌릴 수 있는지.** 쉬움.

**Revisit 트리거.** state schema에 invariant 보장 메커니즘(예: pydantic state model, langgraph reducer)이 도입되면 직접 접근 가능.

**적용 위치.** `graph.py` 모든 노드의 state 접근부.

---

## D-6. LLM 실패 정책

**문제.** S2/S3에서 노드 실 구현 시 LLM 호출 실패(timeout / tool-call 위반 / 인증 실패) 정책 부재.

**옵션.**

1. 모든 노드 1회 재시도 후 실패 시 graph abort.
2. `gen_questions`만 1회 자동 보정 재시도 (F-D8 패턴), `gen_ontology`/`gen_workflows`/`gen_demo`는 빈 결과 + 다음 단계 통과.
3. 모든 노드 실패 시 review로 보내 인간이 결정.

**확정: 옵션 2.**

**근거.**
- `gen_questions`는 데모 히어로 — 실패 시 리뷰 게이트가 의미 잃음. F-D8(자동 보정 1회)와 일관.
- `gen_ontology/workflows/demo`는 graceful degradation 가능: 빈 결과여도 `assemble_export`가 markdown 렌더 → 데모 흐름 유지.
- 옵션 3은 review②③를 라이브로 만드는 것 — MVP OOS (PRD §10).

**되돌릴 수 있는지.** 중간. review②③ 라이브 활성화 시(F-G7/G8) 옵션 3으로 전환 가능.

**Revisit 트리거.** review②③를 라이브로 올릴 때.

**적용 위치.** A1-S2/S3 노드 실 구현 시. 현재는 `graph.py` docstring에 정책 명시.

---

## D-7. edit 시 q.id 안정성

**문제.** review① 의 edit action에서 사용자가 질문을 추가했을 때 빈 `id`에 누가 어떻게 부여하나? gen_ontology의 `OntologyNode.answers`가 q-id에 의존하므로 안정적이어야 함.

**옵션.**

1. UI가 직접 부여 (프론트가 next id 계산).
2. 백엔드가 edit 받을 때 빈 id에 `q{maxN+1}` 결정론적 부여.
3. 백엔드가 edit 시 모든 id를 재부여 (q1부터 다시).

**확정: 옵션 2.**

**근거.**
- 옵션 1: 프론트가 정합성 책임 → 다중 UI(앞으로 모바일/CLI 등) 일관성 보장 어려움.
- 옵션 3: 기존 id가 바뀌면 이전 ontology가 참조 못 함. 재생성 루프 안전성 깨짐.
- 옵션 2: 기존 id는 보존, 새 항목만 결정론적 채움 — id 사슬 안정성 유지.

**되돌릴 수 있는지.** 쉬움. `_assign_stable_ids` 호출 제거 후 `decision.get("edited_items", current)` 그대로 사용.

**Revisit 트리거.** id 체계를 UUID나 hash 기반으로 바꾸는 경우.

**적용 위치.** `graph.py::review_questions` (edit 분기) + `graph.py::_assign_stable_ids` 헬퍼.

---

## D-8. `coerced` 키의 schemas 정식화 시점

**문제.** D-1에서 도입한 `coerced` 키가 `schemas.ReviewState`의 TypedDict 정의에 없다. mypy/pylance 타입 체커 경고 가능 (런타임은 dict이라 OK).

**옵션.**

1. 즉시 schemas.py 변경 — `ReviewState`에 `coerced: NotRequired[bool]` (typing_extensions).
2. runtime extra로만 두고 schemas는 변경하지 않음 (보류).
3. 별도 TypedDict `ReviewMeta`로 분리해서 DomainPackState에 추가.

**확정: 옵션 2 (보류).**

**근거.**
- `execution_sequence.md`의 생존 규칙 1번: "T10 이후 schemas 변경 금지, 불가피 시 3명 동시". 현재 A1 단독 결정 권한 밖.
- runtime 동작에 영향 없음 (`dict`이라 추가 키 허용).
- 타입 체커 경고는 ignore 주석으로 임시 회피 가능, 정식화 시점에 일괄 정리.

**되돌릴 수 있는지.** 쉬움. 3인 합의 후 schemas에 `NotRequired[bool]` 추가하면 정식화. 또는 `coerced` 키 자체를 삭제.

**Revisit 트리거.** T105 코드 프리즈 후 / 운영 전환 시 schemas 정본 정리 라운드.

**적용 위치.** 현재 `graph.py::review_questions` 출력 dict만 추가, schemas.py 무변경.

---

## D-9. 개발 머신 포트 정책

**문제.** uvicorn `:8000` 기동 시 `[Errno 48] address already in use`. Cursor IDE의 내부 서비스가 :8000(irdmi)을 LISTEN 중이며 pkill로 해제 불가.

**옵션.**

1. Cursor 종료 후 :8000 사용.
2. 개발 머신은 동적 빈 포트 탐색(`lsof -i :$p`), 운영은 :8000 유지.
3. ai-service 표준 포트를 :8001로 변경.

**확정: 옵션 2.**

**근거.**
- 옵션 1: 개발 중 IDE를 끄는 건 비현실적.
- 옵션 3: dev-plan §1.5와 task 카드의 :8000 가정을 깸. W의 Go BFF 프록시 대상도 변경 필요 — 영향 범위 큼.
- 옵션 2: 운영 가정은 유지하면서 개발만 우회. 검증 스크립트에 빈 포트 탐색 루프 박아넣음.

**되돌릴 수 있는지.** 쉬움. 검증 스크립트의 `$PORT` 변수만 고정으로 바꿈.

**Revisit 트리거.** 팀 표준 개발 환경 정비 시 (예: Cursor 대신 다른 에디터, 또는 :8000을 Cursor가 양보).

**적용 위치.** 검증 스크립트의 빈 포트 탐색 루프. uvicorn 명령 자체에는 영향 없음 — `--port $PORT` 인자만 동적.

---

## D-10. 검증 스크립트의 응답 파싱 컨벤션

**문제.** `RESP=$(curl ...)` 로 응답을 shell 변수에 저장 후 `echo "$RESP" | python3 -m json.tool` 했더니 `Invalid control character at: line 1 column 1732`. 응답 자체는 valid JSON(2830 bytes)이었음.

**옵션.**

1. shell 변수 경유 유지하되 `printf '%s'` 또는 quote 강화.
2. curl 응답을 **파일로 직접 받고** 파서에 파일 경로 전달.
3. `jq` 도입하여 파이프 처리.

**확정: 옵션 2.**

**근거.**
- 옵션 1: 근본 원인(shell 변수의 binary-safe 미보장)을 우회만 함. control char 종류에 따라 또 깨질 수 있음.
- 옵션 3: jq는 macOS 기본 미설치 — 의존성 추가. 굳이 필요 없음.
- 옵션 2: shell 변수 경유 자체를 피함. 파일 I/O 비용은 검증 스크립트 수준에서 무시 가능.

**되돌릴 수 있는지.** 쉬움 (관행). 검증 스크립트 변경만.

**Revisit 트리거.** 응답이 커서 `/tmp` 비용이 부담스러워지면 (수십 MB+) `jq` 또는 streaming 파서로 전환.

**적용 위치.** [[work-log/a1-s1-2026-06-18#3-2-json-파싱-실패]] 사례 기록. 향후 검증 스크립트(`scripts/`)에 동일 패턴 적용.

---

## 변경 이력

- v1 (2026-06-18) — 초안. A1-S1 진행 중 누적된 8건(D-1~D-8) 정리.
- v1.1 (2026-06-18) — A1-S1 검증·트러블슈팅에서 도출된 2건 추가 (D-9 개발 포트, D-10 응답 파싱).
- v1.2 (2026-06-18) — A2 워크트리(`feat/content-nodes`) 진행 발견 후 공용 모듈 동기화 정책 추가 (D-11).

---

## D-11. 병렬 워크트리 공용 모듈 동기화 정책

**문제.** `feat/graph-infra`(A1)와 `feat/content-nodes`(A2)가 같은 `ai-service/nodes/` 패키지와 `ai-service/llm.py`를 각자 만들고 있어 origin 통합 머지 시 충돌 거의 확실. A1-S2 진행 직전(2026-06-18) `git fetch origin feat/content-nodes`에서 A2 가 `53fb2f1 chore: nodes/ skeleton` + `c421b20 feat: shared Claude client` 를 이미 push 한 것을 확인.

**옵션.**

1. 각자 만들고 머지 시점에 conflict 해결.
2. 한 쪽 베이스를 채택해 cherry-pick. 다른 쪽이 합류.
3. 공용 모듈(`nodes/__init__.py`, `llm.py`)을 양 워크트리에 **동일 내용**으로 유지. 변경 시 한 쪽이 먼저 commit/push → 다른 쪽이 fetch+동기화.

**확정: 옵션 3.**

**근거.**
- 옵션 1: 머지 시점에 충돌 → 통합 비용 큼. 해커톤 당일 사고 위험.
- 옵션 2: cherry-pick 은 history 가 지저분. 두 브랜치가 같은 commit hash 를 안 가지므로 fetch 시 또 충돌.
- 옵션 3: 두 워크트리가 동일 내용 → 머지 시 자동 통합(no conflict). 동기화 책임 owner 만 정하면 됨.

**책임 분담.**
- `nodes/__init__.py` 는 **빈 파일 + 편집 금지** (A2 가 `53fb2f1` 에서 결정). A1 은 따름.
- `llm.py` 의 `call_tool` 시그니처(kwargs-only: `system/user/tool/temperature/max_tokens/model`) 는 A2 가 `c421b20` 에서 확정. A1 은 따름.
- A2 가 `llm.py` 에 `PROFILE_TOOL` / `QUESTIONS_TOOL` 정의를 둠. **A1 의 `ONTOLOGY_TOOL` / `WORKFLOWS_TOOL` / `DEMO_TOOL` 은 각 노드 모듈(`nodes/{ontology,workflows,demo}.py`) 에 inline 정의** — `llm.py` 추가 변경 없음.
- 의존성: `python-dotenv` 가 A2 가정. A1 은 requirements.txt 에 추가.

**되돌릴 수 있는지.** 중간. owner 이동(A1 → A2 또는 반대)은 협의 가능. 한 쪽이 시그니처를 바꾸면 다른 쪽도 동기화.

**Revisit 트리거.** A2 의 `llm.py` 가 호환 깨는 변경(시그니처 바뀜)을 push 하면 즉시 동기화. 또는 두 브랜치 머지 후 단일 owner 로 통합.

**적용 위치.** A1 워크트리(`feat/graph-infra`)의 `ai-service/{llm.py, nodes/__init__.py}` 를 A2 베이스(`origin/feat/content-nodes`) 에 맞춤. `requirements.txt` 에 `python-dotenv>=1.0,<2` 추가.

**Side effect.**
- `app.py` / `dto.py` / `graph.py` (A1 단독 소유) 는 A2 가 변경 안 함 — 충돌 없음.
- A2 의 `pyproject.toml + uv.lock` 은 A1 의 `requirements.txt` 와 별도 존재. 머지 시 둘 다 보존 (deduplication 은 T105 이후 정리).
