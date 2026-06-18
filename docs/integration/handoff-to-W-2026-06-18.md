# A1 → W 통합 핸드오프 (2026-06-18)

> 베이스: `Dfinite/push-to-prod` branch `dev` (= `feat/graph-infra` HEAD 미러)
> 상대 레포: `Marhead/push-to-prod-core` (Go+Gin BFF) / `Marhead/push-to-prod-view` (React+Vite)
> 관련 정본: [[domain_pack_builder_PRD]], [[260617-dev-plan-v1-by-claude]], [[task_W_webapp_v2]]

---

## 0. 한 줄

ai-service 의 `POST /runs` / `POST /runs/{id}/resume` 두 엔드포인트 + `RunResponse` 계약을 **BFF가 1:1 프록시**, **프론트가 `RunResponse.stage`로 화면 라우팅**.

---

## 1. 시스템 토폴로지

```
[browser]
    ↓ http
[push-to-prod-view] (React+Vite, :5173 dev)
    ↓ http (fetch/axios)
[push-to-prod-core] (Go+Gin BFF, :8080 권장)
    ↓ http (1:1 proxy + CORS)
[ai-service] (FastAPI+LangGraph)
    ├ 개발: 127.0.0.1:<dynamic>  ← :8000 은 Cursor 점유 가능 (D-9)
    └ 운영: ai:8000
```

---

## 2. push-to-prod-core (Go+Gin BFF) — 가이드

### 2.1 엔드포인트 (모두 ai-service로 패스스루)

| BFF | ai-service | 비고 |
|---|---|---|
| `POST /runs` | `POST {AI_BASE}/runs` | body 그대로, 응답 그대로 |
| `POST /runs/:id/resume` | `POST {AI_BASE}/runs/{id}/resume` | 동일 |
| `GET /healthz` | (자체) | `{status:"ok"}` |

### 2.2 책임
- **얇은 프록시만**. 비즈니스 로직 0 (task_W_webapp_v2.md 원칙).
- CORS 미들웨어 (`Access-Control-Allow-Origin: <view origin>`).
- `run_id`는 ai-service 응답 그대로 전달 (BFF 자체 세션 만들지 않음).
- 422 응답도 그대로 패스스루 (D-4).

### 2.3 환경변수 권장 (`.env.example` 추가)
```
AI_BASE_URL=http://127.0.0.1:8888
CORS_ALLOW_ORIGIN=http://localhost:5173
PORT=8080
```

### 2.4 ai-service 가동 방법 (W BFF가 호출 전에 알아야 할 것)
```bash
cd ai-service
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
# 빈 포트 자동 탐색 — D-9
for p in 8765 8888 9000; do lsof -i :$p >/dev/null 2>&1 || { PORT=$p; break; }; done
uvicorn app:app --host 127.0.0.1 --port $PORT --log-level warning
```

---

## 3. push-to-prod-view (React) — 가이드

### 3.1 TypeScript 타입 (schemas.py 1:1, `src/types/index.ts`에 옮김)

```ts
// 입력
export interface InputDoc { kind: string; title: string; content: string }
export interface PackInput {
  industry: string;
  documents: InputDoc[];
  problem: string | null;
  max_attempts?: number;
}

// intake 출력 — S2 BRD Review 표시
export interface ProfileItem { text: string; sources: string[] }
export interface ProblemProfile {
  goals: ProfileItem[];
  pain_points: ProfileItem[];
  kpis: ProfileItem[];
  constraints: ProfileItem[];
  systems: string[];
  stakeholders: string[];
}

// gen_questions 출력 — S3 질문 카드
export interface BusinessQuestion {
  id: string;             // "q1", "q2", ...
  question: string;
  category: string;       // COVERAGE_AREAS enum (납기·리드타임 등)
  rationale: string;
  linked_sources: string[];
  data_status: string;    // "available" | "missing:<설명>"
}

export interface Coverage { covered: string[]; missing: string[] }

// review_questions interrupt payload
export interface QuestionInterrupt {
  stage: "questions";
  brd: ProblemProfile;
  items: BusinessQuestion[];
  coverage: Coverage;
}

// review① 3버튼 응답
export type ResumeDecision =
  | { action: "approve" }
  | { action: "edit"; edited_items: BusinessQuestion[] }
  | { action: "regenerate"; feedback: string };

// gen_ontology 출력 — S4 Cytoscape
export interface OntologyNode {
  id: string;             // "n_order"
  name: string;
  type: string;           // "entity" | "event" | "kpi" | "property"
  maps_from: string[];
  answers: string[];      // BusinessQuestion.id 들
}
export interface OntologyRelation { source: string; target: string; label: string }
export interface Ontology { nodes: OntologyNode[]; relations: OntologyRelation[] }

// gen_workflows / gen_demo / assemble_export
export interface Workflow {
  id: string;
  name: string;
  steps: string[];
  answers_question: string;  // BusinessQuestion.id
  uses_nodes: string[];      // OntologyNode.id 들
}
export interface DemoScenario { narrative: string; steps: string[]; based_on: string }
export interface RequiredSources { available: string[]; needed: string[] }
export interface DomainPackOutput {
  industry: string;
  business_questions: BusinessQuestion[];
  ontology: Ontology;
  workflows: Workflow[];
  demo_scenario: DemoScenario;
  required_sources: RequiredSources;
  export: { markdown: string };
}

// 통합 응답
export interface RunResponse {
  run_id: string;
  status: "interrupted" | "done";
  stage?: "questions";              // status='interrupted' 일 때만
  payload?: QuestionInterrupt;      // status='interrupted' 일 때만
  pack?: DomainPackOutput;          // status='done' 일 때만
}
```

### 3.2 Stage 라우팅
- `response.status === 'interrupted'` && `response.stage === 'questions'` → **S3 Question+Seed** 화면 이동
- `response.payload.brd` → **S2 BRD Review** 표시 (로컬 편집)
- `response.status === 'done'` → **S4 Ontology Graph** 진입, pack 사용
- `response.pack.ontology` → S4 Cytoscape elements 변환 (dev-plan §11)
- `response.pack.export.markdown` → S7 Export 다운로드/복사

### 3.3 review① 3버튼 (D-4 422 케이스)
- `approve` → `POST /runs/:id/resume` `{action:"approve"}`
- `edit` → `{action:"edit", edited_items:[...]}` — **`edited_items` 빈 배열 금지 (422)**
- `regenerate` → `{action:"regenerate", feedback:"..."}` — **빈 문자열 금지 (422)**

### 3.4 USE_MOCK fixture
- `pack` fixture: `ai-service/canned_pack.json` 가져가서 사용 (id 사슬·7필드 정합 보장)
- `interrupted` fixture: ai-service 한 번 띄우고 `/runs` 응답 캡처해서 저장 — graph가 동적 구성이라 정적 fixture 없음

### 3.5 데이터 입력 (S1 Input 화면)
- `industry`: `"distribution"` (TPC-H) / `"foodservice"` (foodco) — 두 가지
- `documents[]`: `kind` ∈ `{"brd","email","chat","stt","readme","sales_note"}` (자유 str)
- 디렉터리 업로드 시 — 변환 책임은 클라이언트(W). 백엔드는 InputDoc[]만 수용
- 입력 예시: `/Users/apple/Downloads/foodco-stock-ontology-data` (40건 .md) / `/Users/apple/Downloads/tpch-ontology-data` (동일 구조)

---

## 4. 진행 상태 / 다음 단계

| 영역 | 상태 | commit |
|---|---|---|
| A1 graph 인프라 + 후반 노드 (S1+S2+S3) | ✅ 완료 | `37d123c` |
| A2 intake/retrieve/gen_questions | 진행 중 | `feat/intake` / `feat/retrieve` / `feat/gen-questions` (별도 브랜치) |
| W BFF (push-to-prod-core) | 미작업 (scaffold만) | — |
| W View (push-to-prod-view) | 미작업 (Vite scaffold + docs 복사본) | — |

**A2 머지 전까지** intake/retrieve/gen_questions는 더미 → ProblemProfile/Seed/Questions가 canned에서 흘러나옴. ⇒ W는 USE_MOCK 또는 실 ai-service 호출 시 동일한 canned 응답을 받음. A2 머지 후 자동으로 실 데이터로 교체.

---

## 5. 협의 필요 사항

### 5.1 R-11 `required_sources.available` 표시 방식
- ai-service가 반환하는 `pack.required_sources.available`에는 **테이블명**(`orders`)과 **컬럼 경로**(`orders.o_orderdate`)가 혼재.
- W의 S7 Export UI에서:
  - (a) 둘을 그대로 표시? (현재 백엔드 의도)
  - (b) 테이블 / 컬럼 분리 섹션?
  - (c) 컬럼 경로로 통일 (테이블명만 있는 항목 제거)?
- **결정 권한 = W 측**. 결정 후 백엔드 변경 필요하면 알려주세요.

### 5.2 데모 도메인
- canned_pack은 TPC-H/ABC상사 시나리오 전용.
- foodco(미가F&B) 시연하려면 **`ANTHROPIC_API_KEY` 필수**(LLM enhance 경로).
- 키 없이 foodco 데모는 부적합 (도메인 불일치 fallback) → 데모 시나리오 확정 시 알려주세요.

---

## 6. 환경변수 (ai-service)

| 변수 | 용도 | 기본값 |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM tool-calling 활성화 (없으면 canned fallback) | (미설정) |
| `LLM_MODEL` | Claude 모델 ID | `claude-sonnet-4-6` |

`.env` 파일로 로드 (`python-dotenv`).

---

## 7. dev 브랜치 fetch 방법 (W용)

```bash
cd <your-clone-of-Dfinite/push-to-prod>
git fetch origin
git checkout dev
# 또는 신규 clone:
git clone -b dev https://github.com/Dfinite/push-to-prod.git
```

본 문서: `docs/integration/handoff-to-W-2026-06-18.md`
ai-service 코드: `ai-service/`
정본 문서: `docs/` (PRD, dev-plan, 노드별설계, decisions_a1_v1, work-log/*)

---

## 8. 슬랙/메일 복붙용 (짧은 버전)

> **AI 인프라(A1) 핸드오프 — Dfinite/push-to-prod `dev` 브랜치**
>
> - ai-service 코드: `ai-service/` (Python·FastAPI·LangGraph), 엔드포인트 `POST /runs`, `POST /runs/:id/resume`, `GET /healthz`
> - 응답 계약: `RunResponse {run_id, status:'interrupted'|'done', stage?, payload?, pack?}` — 자세한 TS 타입은 `docs/integration/handoff-to-W-2026-06-18.md` §3.1
> - core BFF: 얇은 프록시만 (`AI_BASE_URL`로 ai-service base 설정, CORS 허용)
> - view: `stage='questions'`로 S2/S3 라우팅, `pack`으로 S4/S7
> - USE_MOCK: `ai-service/canned_pack.json` 가져가서 사용 (id 사슬·7필드 정합 보장)
> - **협의 1건 (R-11)**: `required_sources.available` 표시 방식 (테이블명+컬럼 경로 혼재) — W 결정 후 알려주세요
> - **데모 도메인**: foodco는 `ANTHROPIC_API_KEY` 필수, tpch는 canned로 OK
> - A2 영역(intake/retrieve/gen_questions)은 진행 중 (feat/retrieve 등 별도 브랜치) → 머지 시 자동으로 실 데이터로 교체. W 영향 없음.
>
> 질문 있으면 답주세요.
