# PRD — Domain Pack Builder

| 항목 | 내용 |
|---|---|
| 제품/기능명 | Domain Pack Builder (DARVIS PoC 부트스트래퍼) |
| 상태 | Draft (해커톤 MVP 설계) |
| 작성자 | — |
| 대상 릴리스 | 해커톤 2시간 MVP → 이후 프로덕션 단계 |
| 코어 팀 | 코어엔지니어링, 솔루션 본부 |
| 기술 스택 | LangGraph (Python), Claude (tool-calling), DuckDB(TPC-H) |

---

## 1. 요약 (Executive Summary)

고객의 산업과 운영 문제(BRD·영업 메모 등 실문서)를 입력하면, DARVIS PoC를 빠르게 시작하기 위한 **비즈니스 질문셋, 온톨로지 초안, 워크플로우, 데모 시나리오, PoC 셋업 체크리스트**를 생성하는 시스템.

단순 생성기가 아니라 **(1) 실제 데이터 스키마에 그라운딩된 산출물**, **(2) 산출물 간 `id` 상호 링크(질문↔온톨로지↔워크플로우)**, **(3) 사람이 한 번 조종하는 휴먼인더루프 검토**를 핵심 차별점으로 둔다. LangGraph 단계형 파이프라인(10노드)으로 구현하며, 각 노드는 결정론(코드)과 LLM 작업을 명확히 분담한다.

---

## 2. 문제 & 배경 (Problem & Background)

PoC 세팅에는 고객 산업 이해, 데이터 구조 파악, 운영 질문 설계, 데모 시나리오 작성이 필요하다. 매번 처음부터 하면 리드타임이 길어지고 코어 재사용이 어렵다. 또한 산출물이 일반론에 그치면 범용 LLM 프롬프트와 차별화되지 않는다.

Domain Pack Builder는 **반복 가능한 산업별 시작점**을 제공하고, 입력 문서와 실제 데이터 스키마에 근거를 묶어 "이 고객, 이 문제, 이 데이터"에 특화된 PoC 출발점을 만든다.

---

## 3. 목표 & 성공 지표 (Goals & Success Metrics)

### 목표
- PoC 세팅 리드타임 단축 (문서 입력 → 초안 Pack 생성까지 수 분 내).
- DARVIS Core와의 제품적 연결 강화 (온톨로지·질문이 실제 데이터/문제에 바인딩).
- 코어 재사용률 향상 (산업별 레퍼런스 스키마를 교체 가능한 구조).

### 성공 지표
- **질문 구체성**: 생성 질문의 대다수가 실제 데이터 컬럼(`linked_sources`)에 바인딩된다.
- **연결성**: 모든 온톨로지 노드가 1개 이상의 `question.id`(`answers`)에 연결된다.
- **검토 효율**: 라이브 검토 ①에서 소수의 수정/재생성으로 승인에 도달한다.
- **갭 정확성**: 문서가 함의하나 표준 스키마에 없는 데이터(`needed`)를 올바르게 식별한다.
- **실용성(정성)**: 생성된 PoC 셋업 체크리스트가 솔루션 본부에서 "그대로 쓸 만하다" 평가를 받는다.

### Non-goals (비목표)
- 실제 DARVIS Core와의 라이브 연동 (MVP 범위 아님).
- 완전 자동화된 무인 생성 (휴먼인더루프 검토를 의도적으로 둔다).
- 다수 산업 동시 지원 (MVP는 단일 레퍼런스 산업).

---

## 4. 타깃 사용자 & 페르소나 (Target Users & Personas)

- **1차 사용자** — 코어엔지니어링, 솔루션 본부: PoC 세팅을 주도. 질문셋·온톨로지·체크리스트를 검토·활용.
- **2차 사용자** — 가치전략실: 산업별 문제 정의와 가치 가설 점검.
- **보조 사용자** — 세일즈/데모 담당: 생성된 데모 시나리오로 빠른 시연.

---

## 5. 솔루션 개요 (Solution Overview)

LangGraph 기반 **단계형 + 휴먼리뷰** 파이프라인. 그라운딩은 **레퍼런스 스키마(표준) + 입력 문서 기반 예상 스키마**의 갭 분석으로 한다.

### 파이프라인 (10 노드)
```
intake → retrieve → gen_questions → review① → gen_ontology → review②
       → gen_workflows → gen_demo → review③ → assemble_export
```
- 생성 단계(질문·온톨로지)에 휴먼 검토 ①②, 마지막에 ③ 배치.
- ①②는 "재생성(피드백)" 시 해당 생성 노드로 복귀하는 루프, ③은 워크플로우/데모로 개별 복귀하는 서브라우터.
- 각 검토는 **승인 / 인라인 수정 / 피드백 재생성** 중 선택. 재시도는 `max_attempts`로 상한.

### 노드별 책임 (코드=결정론, LLM=생성/추론)
1. **intake** — 다중 문서를 map-reduce로 정규화. parser(코드)는 `kind/title/content` 인입, LLM은 의미 추출, 근거 매핑은 코드(후보 검색)+LLM(검증), 근거 없는 항목 제거(할루시 가드) → `ProblemProfile`.
2. **retrieve** — 산업별 레퍼런스 스키마 로드(코드) + 문서 기반 예상 스키마 추론(LLM) → 갭 분석(코드: matched/missing/extra) → `seed`.
3. **gen_questions** — 컨텍스트 조립(코드) → LLM 생성(tool-calling, 스키마 바인딩 + 갭 태그) → 검증(코드: 참조·커버리지·dedup), 실패 시 자동 보정 1회.
4. **review①** — `interrupt`로 정지, 외부 UI 3버튼, `resume`로 state 반영 (유일한 라이브 지점).
5. **gen_ontology** — FK 스켈레톤(코드) → LLM 매핑(추상·relabel·질문 링크) → 검증(코드).
6. **review②** — MVP 자동승인 스텁(배선 유지).
7. **gen_workflows** — LLM 생성(tool-calling, `id` 강제) → 가벼운 검증(코드).
8. **gen_demo** — 대표 워크플로우 선택(코드) → LLM 생성(내러티브·스텝).
9. **review③** — MVP 자동승인 스텁(서브라우터 배선 유지).
10. **assemble_export** — 소스 집계(코드) + 마크다운 렌더(코드) → `required_sources` + `export`.

---

## 6. 사용자 스토리 (User Stories)

- 솔루션 본부 담당자로서, 고객 BRD와 영업 메모를 올리면 산업·문제에 맞는 비즈니스 질문셋 초안을 받아 PoC 설계를 빠르게 시작하고 싶다.
- 코어엔지니어로서, 생성된 질문셋을 검토해 일부를 수정하거나 "재고 회전 관점도 추가" 같은 피드백으로 재생성하고 싶다.
- 솔루션 본부로서, 각 질문이 어떤 데이터 컬럼에 연결되는지, 어떤 데이터는 아직 확보가 필요한지(갭) 명확히 보고 싶다.
- 세일즈 담당자로서, 자동 생성된 데모 시나리오로 즉시 시연을 준비하고 싶다.
- 누구든 결과를 PoC 셋업 체크리스트(마크다운)로 복사해 가고 싶다.

---

## 7. 기능 요구사항 (Functional Requirements)

### 7.1 입력 & 정규화 (intake)
- 다중 입력 문서(`documents[]`: BRD, 영업 메모, 이메일/통화 등)를 받는다.
- LLM으로 `goals / pain_points / kpis / systems / constraints / stakeholders`를 추출한다.
- 각 추출 항목에 **지지 문서(provenance)** 를 매핑한다(코드 후보 검색 + LLM 검증). 근거 없는 항목은 제거한다.

### 7.2 그라운딩 & 갭 분석 (retrieve)
- 산업 키에 따라 **레퍼런스 스키마**를 로드한다(MVP: distribution = TPC-H, 교체 가능).
- `ProblemProfile`에서 **예상 스키마**(필요 엔티티·필드)를 추론한다.
- **갭**을 산출한다: `matched`(레퍼런스 커버) / `missing`(문서 요구 but 표준에 없음) / `extra`.

### 7.3 생성 (gen_questions / gen_ontology / gen_workflows / gen_demo)
- 질문은 구조화 출력(tool-calling)으로 스키마를 강제하며, `linked_sources`는 실제 컬럼만 참조하고 갭에 따라 `available` / `missing:...`로 태깅한다.
- 온톨로지는 FK 스켈레톤을 비즈니스 개념으로 추상화하고, 각 노드는 `maps_from`(출처)과 `answers`(`question.id`)를 가진다.
- 워크플로우는 `answers_question`(질문)과 `uses_nodes`(노드)를 강제 참조한다.
- 데모는 대표 워크플로우 1개를 엔드투엔드 시나리오로 만든다.

### 7.4 휴먼인더루프 검토 (review①, MVP 라이브)
- 생성된 질문셋 + 커버리지를 UI에 표시하고 **승인 / 인라인 수정 / 피드백 재생성** 버튼을 제공한다.
- 재생성 시 피드백을 누적해 생성 노드로 복귀하며, `max_attempts` 초과 시 강제 통과한다.

### 7.5 Export (assemble_export)
- `required_sources`를 `{available, needed}`로 집계한다.
- 전체 산출물을 **PoC 셋업 체크리스트(마크다운)** 로 렌더한다. (Notion은 MVP에서 "MD 복사"로 목업)

---

## 8. 비기능 요구사항 (Non-functional Requirements)

- **근거성/추적성**: 모든 프로파일 항목은 지지 문서에 연결되고, 근거 없는 생성은 제거된다(할루시 가드).
- **결정론 우선**: 파싱·집계·검증·렌더는 코드로 처리해 재현성을 높이고 LLM 변동성을 줄인다.
- **루프 안전**: 재생성 루프는 `max_attempts`(예: 3)로 상한을 두어 토큰·시간 폭주를 방지한다.
- **상태/체크포인트**: `interrupt` 사용을 위해 checkpointer 필요(MVP: `MemorySaver` + `thread_id`).
- **보안/프라이버시**: 영업 문서의 민감정보(고객명·단가) 마스킹은 운영 단계 플래그(미해결).
- **확장성**: 레퍼런스 스키마는 산업별 교체 가능, 다중 문서는 map-reduce로 처리.

---

## 9. 데이터 모델 (Data Model)

### 입력
```json
{
  "industry": "distribution",
  "documents": [
    {"kind": "brd", "title": "유통 운영 BRD v1.2", "content": "..."},
    {"kind": "sales_note", "title": "ABC상사 미팅 메모", "content": "..."}
  ],
  "problem": null
}
```

### ProblemProfile (intake 출력)
```json
{
  "goals":       [{"text": "납기 준수율 95% 달성", "sources": ["유통 운영 BRD v1.2"]}],
  "pain_points": [{"text": "성수기 납기 지연 클레임", "sources": ["ABC상사 미팅 메모"]}],
  "kpis":        [{"text": "납기 준수율", "sources": ["유통 운영 BRD v1.2"]}],
  "systems":      ["ERP", "WMS"],
  "constraints": [{"text": "PoC 8주 · 거래처 20곳", "sources": ["유통 운영 BRD v1.2"]}],
  "stakeholders": ["물류팀", "영업"]
}
```

### seed (retrieve 출력 — 갭 분석)
```json
{
  "reference": {"name": "tpch", "schema": [/* 테이블·컬럼·PK/FK */], "samples": {}, "profile": {}},
  "expected":  {"entities": [{"name": "공급사", "needed_fields": ["리드타임"], "from": ["물류팀 회신"]}]},
  "gap": {
    "matched": [{"expected": "출하", "reference": "lineitem", "via": ["l_commitdate", "l_shipdate"]}],
    "missing": [{"expected": "공급사 리드타임", "note": "표준 미보유 → 별도 소스 필요"}],
    "extra":   ["region", "nation"]
  },
  "gold_questions": ["배송 우선순위 상위 주문", "수익 기여 상위 공급사"]
}
```

### 최종 산출물 (DomainPackOutput)
```json
{
  "industry": "distribution",
  "business_questions": [
    {"id": "q1", "question": "약속 납기 대비 출하 지연 위험 주문은?",
     "category": "납기·리드타임", "rationale": "납기 95% 목표 직결",
     "linked_sources": ["orders.o_orderdate", "lineitem.l_commitdate", "lineitem.l_shipdate"],
     "data_status": "available"}
  ],
  "ontology": {
    "nodes": [
      {"id": "n_order", "name": "고객주문", "type": "entity", "maps_from": ["orders"], "answers": ["q1"]},
      {"id": "n_line",  "name": "출하라인", "type": "event",  "maps_from": ["lineitem"], "answers": ["q1"]}
    ],
    "relations": [{"source": "n_order", "target": "n_line", "label": "주문-포함→출하라인"}]
  },
  "workflows": [
    {"id": "wf1", "name": "납기 지연 조기 경보",
     "steps": ["지연 라인 추출", "주문 위험도 집계", "임계 초과 경보"],
     "answers_question": "q1", "uses_nodes": ["n_order", "n_line"]}
  ],
  "demo_scenario": {"narrative": "...", "steps": ["..."], "based_on": "wf1"},
  "required_sources": {
    "available": ["orders.o_orderdate", "lineitem.l_commitdate", "lineitem.l_shipdate"],
    "needed":    ["공급사 리드타임 (표준 미보유 → 확보 필요)"]
  },
  "export": {"markdown": "# PoC 셋업 체크리스트 ..."}
}
```

핵심 불변식: `question.id` → 온톨로지 `answers` → 워크플로우 `answers_question` → `required_sources`까지 하나의 `id` 사슬로 이어진다.

---

## 10. 스코프 & MVP (Scope)

### 실제 구현 (In-scope, 라이브)
- `intake` (다중 문서 정규화 + 근거 매핑)
- `retrieve` (레퍼런스 + 예상 스키마 갭 분석)
- `gen_questions` (구조화 출력 + 자동 보정)
- `gen_ontology` (스키마→온톨로지 하이브리드)
- `review①` (라이브 인터럽트, 3버튼)

### 간소화 (Simplified)
- `gen_workflows`, `gen_demo` (간단한 스키마)
- `assemble_export` (MD 체크리스트, Notion은 목업)

### 목업/스텁 (Out-of-scope for MVP, 배선만)
- `review②`, `review③` (자동승인)
- 실제 RAG (정적/레퍼런스 스키마로 대체)
- 실제 DARVIS Core 연동, Notion API, PII 마스킹

---

## 11. 가정 & 의존성 (Assumptions & Dependencies)

- 입력 문서는 텍스트로 추출 가능(PDF/PPT는 사전 추출).
- distribution 산업의 레퍼런스로 TPC-H를 사용(DuckDB `dbgen`로 즉시 생성).
- LLM은 tool-calling 구조화 출력을 지원(Claude).
- 임베딩/유사도(근거 후보 검색·dedup)는 선택적 업그레이드.

---

## 12. 리스크 & 완화 (Risks & Mitigations)

| 리스크 | 완화 |
|---|---|
| 결과물이 일반적이면 범용 LLM과 차별 약함 | 실제 스키마 그라운딩 + 산출물 간 `id` 링크 + 갭 분석 |
| 온톨로지/도메인팩이 심사자에게 낯섦 | 데이터→온톨로지→질문 링크 체인을 시각적으로 시연 |
| 2시간 MVP에서 전 노드 구현 불가 | 차별화 노드만 실제 구현, 인프라(RAG·Notion·검토②③)는 목업 |
| 레퍼런스(TPC-H)가 제조 정체성과 다소 어긋남 | 산업을 "유통/공급망"으로 리프레임, 레퍼런스 교체 가능 구조 |
| 영업 문서의 민감정보 노출 | 운영 단계 마스킹 플래그(미해결, 12장 Open) |
| LLM 환각 | 근거 매핑 + 근거 없는 항목 제거, 결정론 검증 |

---

## 13. 마일스톤 & 데모 흐름 (Milestones & Demo Flow)

### 데모 흐름
1. "디피니트의 Q는 PoC 세팅을 반복 가능하게 만드는 힘입니다."
2. 고객 문서(BRD·영업 메모) 입력.
3. 정규화된 ProblemProfile + 레퍼런스 대비 데이터 갭 표시.
4. 질문셋 자동 생성 → **라이브 검토 ①** 에서 수정/재생성 시연.
5. 온톨로지·워크플로우·데모가 질문에 링크되어 자동 생성.
6. PoC 셋업 체크리스트(확보됨/확보 필요 구분)로 Export.

### 마일스톤
- M1: state·그래프·라우터 스켈레톤 + TPC-H 시드.
- M2: gen_questions + review① 라이브 동작.
- M3: gen_ontology + 갭 분석 + Export.
- M4: 데모 시나리오 + 시연 리허설.

---

## 14. 미해결 질문 (Open Questions)

- 영업 문서 PII 마스킹을 MVP에서 도입할지, 운영 단계로 미룰지.
- 실제 RAG(과거 PoC 문서 검색)를 언제 도입할지.
- 검토 ②③를 언제 라이브로 활성화할지.
- 레퍼런스 스키마를 산업별로 어떻게 확장·관리할지.
- 근거 후보 검색을 문자 유사도 → 임베딩으로 언제 업그레이드할지.

---

## 변경 이력 (Change Log)
- v0.1 — 초안. 해커톤 MVP 설계(입력 정규화·갭 분석·그래프 10노드·라이브 검토①) 반영.
