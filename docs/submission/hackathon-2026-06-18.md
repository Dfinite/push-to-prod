# 해커톤 Submission — Domain Pack Builder (2026-06-18)

> 제출자: A1 (graph-infra) · A2 (content-nodes) · W (push-to-prod-core + push-to-prod-view)
> 베이스: `Dfinite/push-to-prod` branch `dev` (= feat/graph-infra HEAD 미러)
> 관련 정본: [`../api/spec.md`](../api/spec.md), [`../integration/handoff-to-W-2026-06-18.md`](../integration/handoff-to-W-2026-06-18.md), [`../decisions_a1_v1.md`](../decisions_a1_v1.md)

---

## 사용한 AI 도구

- [ ] Replit 활용
- [x] **Claude 활용**

> 추가: **Claude Code (CLI)** — `backend-engineer` 서브에이전트를 병렬로 위임해 S2(`gen_ontology`)와 S3(`gen_workflows / gen_demo / assemble_export`) 노드를 동시 개발. 단일 세션에서 코드 · 검증 · 13건 결정 카탈로그 · 21건 리스크 인벤토리 · 700줄 API 스펙 · 250줄 W 핸드오프까지 한 흐름으로 산출.

---

# 1. 문제 (Problem)

## 한국어

**누가 — 솔루션 본부 · 코어 엔지니어링 · 세일즈/데모 담당자.**
이들은 신규 PoC 의 시작 단계마다 같은 일을 반복합니다. 고객 산업을 이해하고, BRD·영업 메모·이메일 같은 흩어진 문서를 정리하고, 데이터 구조를 파악하고, 운영 질문을 설계하고, 데모 시나리오를 짭니다.

**문제는 세 가지**입니다.

1. **반복과 비효율** — 같은 산업의 같은 문제도 매번 처음부터 다시. 코어 자산의 재사용률이 낮고, 1 PoC 당 며칠~몇 주의 리드타임이 들어갑니다.
2. **그라운딩 부재** — 범용 LLM 에 입력만 넣고 받은 결과는 "일반론" 입니다. 실제 고객 데이터 스키마에 묶여있지 않아 시연 직전에 "이 컬럼이 우리한테 없는데요?" 가 터집니다.
3. **추적성 부재** — 질문, 온톨로지, 워크플로우, 데모가 따로 놉니다. "이 질문은 어떤 데이터를 보고 어떤 워크플로우로 답하는가" 가 한 화면에 안 보입니다.

**기존 방식의 한계** — 가내수공업 또는 단발 LLM 프롬프트. 둘 다 재현 가능하지 않고, 결과 품질이 사람·세션마다 들쭉날쭉합니다.

## English

**Who** — solution-engineering teams, core engineering, and sales/demo staff inside a B2B AI vendor. Every new customer PoC restarts from scratch: read the BRD and sales notes, understand the industry, sketch the data model, design operational questions, prepare a demo.

**Three pain points:**

1. **Repetition without reuse** — the same industry and the same problem are rebuilt by hand each time. Days to weeks of lead time per PoC.
2. **No grounding** — a generic LLM prompt returns generic answers. They are not bound to the customer's real schema, so the gap shows up at the worst time — during the live demo.
3. **No traceability** — questions, ontology, workflows, and demo all live in separate documents. Nobody can show "this question is answered by *that* data via *this* workflow" on one screen.

**What's wrong with the status quo** — either a hand-crafted process or one-shot LLM prompting. Neither is reproducible; quality swings wildly across people and sessions.

---

# 2. 솔루션 (Solution)

## 한국어

**Domain Pack Builder** — LangGraph 기반 10 노드 파이프라인이 고객 문서를 받아 PoC 시작에 필요한 5종 산출물을 한 번에 생성합니다.

```
intake → retrieve → gen_questions → review① → gen_ontology
       → review② → gen_workflows → gen_demo → review③ → assemble_export
```

**빌드 시간 내 실제 구현 완료된 핵심 기능 (해커톤 종료 시점):**

- **그라운딩**: TPC-H 표준 스키마 + 문서 기반 예상 스키마를 코드로 갭 분석 → `matched / missing / extra` 자동 분류. "확보됨 / 확보 필요" 가 데모 첫 화면에 박힘.
- **id 사슬**: 모든 산출물이 `q1 → ontology.answers → workflows.answers_question → required_sources` 로 묶임. 어떤 질문이 어떤 워크플로우로 어떤 데이터를 보는지 한 줄로 추적.
- **휴먼인더루프**: review① (질문셋 검토) 만 라이브. **승인 · 인라인 수정 · 피드백 재생성** 3 버튼. `max_attempts` 도달 시 강제 통과 + `coerced` 플래그 (감사 추적).
- **결정론 + LLM 하이브리드**: 파싱·집계·검증·렌더는 코드, 의미 추출·매핑·생성은 Claude tool-calling. `validate_ontology` 같은 결정론 가드가 LLM 환각을 drop.
- **안정성**: `ANTHROPIC_API_KEY` 토글. 키 있으면 LLM enhance, 없으면 `canned_pack.json` 폴백. 데모 사고 위험을 0 에 가깝게.
- **Export**: 비즈니스 질문·온톨로지·워크플로우·데이터 갭·데모 시나리오를 묶은 **PoC 셋업 체크리스트 (Markdown)** 와 그래프 적재용 데이터를 즉시 다운로드.
- **API**: `POST /runs` → `POST /runs/{id}/resume` 두 엔드포인트만으로 라이브 검토를 포함한 전 흐름. Pydantic DTO 로 422 자동 검증.

**아키텍처 분리**: Python · FastAPI · LangGraph (ai-service) / Go · Gin (얇은 BFF) / React · Vite · shadcn · Cytoscape · Zustand (view). 백엔드 변경 없이 두 클라이언트가 동일 계약을 쓰도록 OpenAPI 정본을 별도 유지.

## English

**Domain Pack Builder** — a 10-node LangGraph pipeline that takes the customer's input documents and emits five outputs in a single run:

```
intake → retrieve → gen_questions → review① → gen_ontology
       → review② → gen_workflows → gen_demo → review③ → assemble_export
```

**Shipped within the hackathon build window:**

- **Grounding** — diff the TPC-H reference schema against a document-inferred expected schema (deterministic code), categorize into `matched / missing / extra`. The "have / need" data gap is rendered on the very first demo screen.
- **The id chain** — every artifact is linked: `q1 → ontology.answers → workflows.answers_question → required_sources`. One sentence answers "which question, via which workflow, against which data."
- **Human-in-the-loop** — only review① is live: three buttons (approve / inline edit / regenerate-with-feedback). When `max_attempts` is hit, the run force-passes with a `coerced=true` audit flag.
- **Deterministic + LLM hybrid** — parsing, aggregation, validation, and rendering are pure code; semantic extraction, mapping, and generation are Claude tool-calling. Hallucinated nodes get dropped by validators like `validate_ontology`.
- **Reliability** — `ANTHROPIC_API_KEY` toggle. With a key, the LLM enhances; without one, a canned-pack fallback keeps the demo intact. Risk of a live failure is near zero.
- **Export** — a Markdown "PoC setup checklist" that bundles business questions, ontology, workflows, the data gap, and a demo scenario — copy-ready.
- **API surface** — just `POST /runs` and `POST /runs/{id}/resume` carry the entire flow including the live review gate; Pydantic DTOs validate every request, returning 422 on contract breaches.

**Architecture split** — Python · FastAPI · LangGraph (ai-service) / Go · Gin (thin BFF) / React · Vite · shadcn · Cytoscape · Zustand (view), with a single OpenAPI source-of-truth so both clients hold the same contract without backend changes.

---

# 3. AI 활용

## 한국어

**Claude 가 제품 핵심 가치의 두 자리를 차지합니다.**

### 3.1 제품 안의 Claude — 도메인 변환의 핵심

- **Tool-calling 구조화 출력** — `PROFILE_TOOL`, `QUESTIONS_TOOL`, `ONTOLOGY_TOOL`, `WORKFLOWS_TOOL`, `DEMO_TOOL` 5종을 정의해 Claude 가 자유 텍스트 대신 JSON 만 내도록 강제. 파싱 오류 0, 다운스트림 validator 가 즉시 검증 가능.
- **노드별 분담** — intake (의미 추출), retrieve (예상 스키마 추론), gen_questions (실 컬럼 바인딩), gen_ontology (비즈니스 의미화 + 질문 링크). 각 노드는 **Claude → 결정론 검증 → 다음 노드** 사이클로 환각을 차단.
- **휴먼-AI 협업 패턴** — 리뷰 게이트에서 사용자가 "재고 회전 관점도 추가" 같은 자연어 피드백을 주면 Claude 가 그 컨텍스트를 누적해 재생성. 단순 wrapping 이 아니라, **사람의 도메인 직관을 AI 추론에 다시 주입** 하는 루프가 제품의 차별점입니다.
- **할루시 가드** — Claude 가 추출한 모든 항목은 지지 문서(`sources`)에 매핑되고, 근거 0 개 항목은 코드가 제거. AI 가 그럴듯하게 만드는 답을 시스템이 차단.

### 3.2 개발 안의 Claude — 빌드 자체를 가속

- **Claude Code (CLI) 로 한 세션에서 코드 + 검증 + 문서를 동시 산출** — 10 노드 그래프, FastAPI 엔드포인트, Pydantic DTO, 13 건 결정 카탈로그, 21 건 리스크 인벤토리, API 스펙 700 줄, W 핸드오프 250 줄 + 회귀 검증 모두 단일 흐름.
- **backend-engineer 서브에이전트 병렬 위임** — S2 (gen_ontology) 와 S3 (workflows/demo/export) 를 동시에 두 에이전트로 분산. 시간을 절반 가까이 단축하면서 같은 워크트리에서 모듈 분리로 충돌 0.
- **결정 카탈로그 자동 추적** — 코드 변경이 누적될 때마다 옵션·확정·근거·되돌릴 수 있는지를 문서화. 추후 운영 단계에서 "왜 이렇게 결정했는지" 가 코드 옆에 그대로 남아 있습니다.

**핵심 메시지** — AI 는 보조가 아니라 **도메인 ↔ 데이터 변환의 본체** 입니다. 사람의 직관과 결정론 검증을 양 끝에서 잡아주면, AI 가 잘 못하는 "정합성" 까지 함께 해결됩니다.

## English

**Claude sits in two seats of the product's core value.**

### 3.1 Claude inside the product — the engine of domain translation

- **Structured outputs via tool-calling** — five tools (`PROFILE_TOOL`, `QUESTIONS_TOOL`, `ONTOLOGY_TOOL`, `WORKFLOWS_TOOL`, `DEMO_TOOL`) force Claude to emit JSON instead of prose. Zero parse errors; downstream validators run immediately.
- **Per-node responsibility** — intake (semantic extraction), retrieve (expected-schema inference), gen_questions (binding to real columns), gen_ontology (business naming + question links). Every node runs **Claude → deterministic validation → next node**, blocking hallucinations at the boundary.
- **Human–AI collaboration loop** — at the review gate a user can drop natural feedback like "add an inventory-turnover lens," which Claude accumulates as context and regenerates against. The differentiator is not the wrapping but this loop: **human domain intuition flowing back into AI reasoning**.
- **Provenance gate** — every item Claude extracts is mapped to a supporting document (`sources`); zero-evidence items are dropped by code. The system catches plausible-but-baseless output before it reaches the user.

### 3.2 Claude inside the build — accelerating delivery itself

- **Claude Code (CLI), one-session full stack** — 10-node graph, FastAPI endpoints, Pydantic DTOs, 13-entry decision catalog, 21-entry risk inventory, 700-line API spec, and a 250-line handoff doc — all produced and verified in a single flow.
- **Parallel sub-agent delegation** — backend-engineer instances ran S2 (gen_ontology) and S3 (workflows/demo/export) in parallel on the same worktree, with module separation so merging stayed conflict-free. Roughly half the wall-clock.
- **Decision catalog as a side effect** — each non-trivial choice is captured as options · selected · reasoning · reversibility · revisit trigger. The "why" lives next to the code, not in someone's head.

**The core claim** — AI is not an assistant here, it *is* the domain-to-data translation engine. With human intuition on one side and deterministic validation on the other, the parts AI is weakest at — internal consistency — get covered too.

---

# 4. 기대 결과 / 임팩트

## 한국어

### 4.1 누구에게 어떤 임팩트인가

| 대상 | 임팩트 |
|---|---|
| **솔루션 본부 · 코어 엔지니어링** | PoC 셋업 리드타임 단축 → 같은 인원으로 더 많은 PoC 진행 가능 |
| **세일즈 / 데모** | 고객 입력 → 데모 자료 자동 생성 → 영업 사이클 단축 |
| **고객** | "확보됨 / 확보 필요" 가 데모 자리에서 보임 → 의사결정 가속 |
| **회사** | 산업별 레퍼런스 스키마가 자산화 → 산업 확장 시 한계 비용 감소 |

### 4.2 정량 지표 — 6 주 검증 계획

| # | 지표 | 베이스라인 (현재) | 6 주 목표 | 측정 방법 |
|---|---|---|---|---|
| 1 | PoC 셋업 평균 리드타임 | 3 ~ 5 일 (수기) | < 30 분 (도구 사용) | 신규 사례 3 건 실측 (산업 1 + 2 신규) |
| 2 | 솔루션 본부 1 인당 동시 진행 PoC 수 | 1 ~ 2 건 | 3 ~ 4 건 | 본부 운영 데이터 |
| 3 | 산출물 "그대로 쓸 만하다" 평가 비율 | — | ≥ 70 % | 본부 내부 평가 (5 점 리커트) |
| 4 | 갭 식별률 (놓친 데이터 추후 발견 건수) | 평균 2 ~ 3 건 / PoC | ≤ 0.5 건 / PoC | 시연 후 회고 |
| 5 | 산업 레퍼런스 스키마 커버리지 | 1 종 (distribution / TPC-H) | 3 ~ 4 종 (+ foodservice + manufacturing 최소) | 스키마 자산 카운트 |
| 6 | 휴먼리뷰 평균 재생성 횟수 (질문셋) | — | ≤ 1.5 회 / PoC | 그래프 `review_questions.attempts` 로그 |

### 4.3 6 주 검증 일정

- **주 1 ~ 2** — 솔루션 본부 자원자 2 명에게 사내 사용 권한 부여. 진행 중 PoC 1 건에 적용해 베이스라인 측정.
- **주 3 ~ 4** — A2 노드 머지 후 실 데이터 흐름 검증. foodco 도메인 레퍼런스 추가 (manufacturing 후속).
- **주 5** — 외부 가상 고객 2 사 시나리오로 데모 시연. 결과 평가 (지표 #3, #4).
- **주 6** — 회고 + 운영 전환 계획. PII 마스킹, 인증, structured logging, review ② ③ 라이브 활성화 의사결정.

### 4.4 정성 임팩트 — 일하는 방식의 변화

- "PoC 는 항상 0 에서 시작" 이라는 가정 자체가 깨집니다. 매 사례마다 코어가 한 단계씩 풍부해지는 양의 피드백 루프가 생깁니다.
- 산출물이 추적 가능해지면 — "이 데모는 어떤 데이터가 있어서 가능한가, 무엇이 부족한가" 가 객관화됩니다. 가설이 아니라 데이터로 의사결정.
- 휴먼인더루프가 살아 있으니, AI 결과를 사람이 마지막 결정하는 가치 흐름이 보존됩니다. 자동화의 도덕적 / 운영적 리스크 둘 다 완화.

### 4.5 리스크와 한계 (정직 공시)

- 산업별 레퍼런스 스키마 확장에는 도메인 인력 시간이 필요합니다 ( ↑ #5 지표). 단순 자동화 만으로 안 됩니다.
- 휴먼리뷰가 효과적이려면 사용자가 도메인 직관을 가져와야 합니다 — 신규 산업 진입 초기에는 효과가 제한적.
- 외부 LLM 의존성 — 운영 단계에서 비용 / 지연 / 인증 / PII 정책을 별도 설계 필요.

## English

### 4.1 Stakeholder impact

| Audience | Impact |
|---|---|
| **Solution engineering · core eng** | Shorter PoC setup → same headcount can run more PoCs |
| **Sales / demo** | Customer input → auto-generated demo materials → faster sales cycle |
| **Customer** | "Have / need" data visible in the first demo → faster decision-making |
| **Company** | Industry-specific reference schemas become reusable assets → lower marginal cost of expansion |

### 4.2 Quantitative targets — 6-week validation plan

| # | Metric | Baseline | 6-week target | Measurement |
|---|---|---|---|---|
| 1 | Average PoC setup lead time | 3 – 5 days (manual) | < 30 min (with the tool) | 3 fresh cases (1 existing industry + 2 new) |
| 2 | Concurrent PoCs per solution engineer | 1 – 2 | 3 – 4 | Internal ops data |
| 3 | "Usable as-is" rating on outputs | — | ≥ 70 % | 5-point internal Likert |
| 4 | Missed-data findings discovered late | 2 – 3 per PoC | ≤ 0.5 per PoC | Post-demo retros |
| 5 | Reference-schema coverage | 1 (distribution / TPC-H) | 3 – 4 (+ foodservice + manufacturing at minimum) | Asset count |
| 6 | Average regenerate count at review① | — | ≤ 1.5 per PoC | `review_questions.attempts` logs |

### 4.3 6-week schedule

- **Week 1–2** — onboard two solution engineers, apply to one in-flight PoC, capture baselines.
- **Week 3–4** — after A2 nodes merge, validate the live data flow; add the foodco reference (manufacturing next).
- **Week 5** — demo against two simulated customer scenarios; collect metrics #3 and #4.
- **Week 6** — retro and ops-readiness decisions (PII masking, auth, structured logging, activating review ② and ③).

### 4.4 Qualitative impact — how work itself changes

- The assumption that "every PoC starts at zero" breaks. Each engagement now thickens the core, creating a positive feedback loop.
- Outputs become traceable — "what makes this demo possible, what's missing" is objectified. Decisions move from hypothesis to data.
- The human-in-the-loop preserves the value chain in which humans make the final call. Both ethical and operational risks of automation are mitigated.

### 4.5 Risks and honest limits

- Expanding industry reference schemas (metric #5) still needs domain-expert time; automation alone is not enough.
- The review loop only works when the user brings domain intuition — its value is limited at the very start of a new industry.
- External LLM dependency — production cost, latency, auth, and PII policy need separate design before scaling.

---

## 한 줄 마무리

> **한국어** — 범용 LLM 이 "그럴듯한 답" 을 주는 동안, Domain Pack Builder 는 **고객의 실제 데이터에 묶인 PoC 시작점** 을 줍니다. 매번 다시 짓던 것을 다시 쓰는 자산으로.

> **English** — While a generic LLM gives plausible answers, Domain Pack Builder gives you a **PoC starting point bound to the customer's real data** — turning what you used to rebuild every time into an asset you reuse.

---

## 부록 — 산출물 인덱스 (제출 시점)

| 영역 | 위치 | 비고 |
|---|---|---|
| AI 서버 코드 | [`ai-service/`](../../ai-service/) | FastAPI · LangGraph · Pydantic · anthropic SDK |
| API 정본 | [`docs/api/openapi.json`](../api/openapi.json) | FastAPI 자동 생성 (392줄) |
| API 가이드 | [`docs/api/spec.md`](../api/spec.md) | 사람용 자세한 스펙 (~700줄, TS 타입·curl·시퀀스) |
| 결정 카탈로그 | [`docs/decisions_a1_v1.md`](../decisions_a1_v1.md) | D-1 ~ D-13 (옵션·확정·근거·되돌릴 수 있는지) |
| LLD | [`docs/lld_a1_v1.md`](../lld_a1_v1.md) | A1 영역 Low-Level Design |
| 리스크 인벤토리 | [`docs/work-log/a1-merge-risks-2026-06-18.md`](../work-log/a1-merge-risks-2026-06-18.md) | 21건 분류 (즉시 해결·환경·도메인·시스템·운영) |
| W 핸드오프 | [`docs/integration/handoff-to-W-2026-06-18.md`](../integration/handoff-to-W-2026-06-18.md) | core/view 분리 가이드 |
| 작업 일지 | [`docs/work-log/`](../work-log/) | a1-s1 / a1-s2 / a1-s3 / a1-merge-risks |
| 정본 (사전 정의) | [`docs/`](..) | PRD · dev-plan · 노드별설계 · execution_sequence · feature_list · task 카드 W/A1/A2 |
