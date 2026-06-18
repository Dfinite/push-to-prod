# W — web (프론트 7화면 + Go BFF) 작업 카드 v2

> dev-plan(7화면) 반영 + LangGraph 유지 결정 버전.
> W 소유: **Frontend(React)** + **Go+Gin BFF**. AI 서버(Python FastAPI+LangGraph)는 A1·A2.

## 아키텍처
```
Frontend(React/shadcn/Cytoscape/Zustand) —HTTP(stage 라우팅)→ Go+Gin BFF(얇은 프록시) —/runs·/resume→ Python FastAPI+LangGraph
```
- **Go BFF는 얇게**: `/runs`·`/runs/{id}/resume`를 AI로 그대로 프록시 + CORS + run_id 세션만. 비즈니스 로직 금지.
- 프론트는 7화면을 가져가되, **interrupt payload의 `stage` 값으로 화면을 라우팅**한다.
- 레포: `web`(frontend + bff 폴더) 단독 소유. `ai-service`는 별도.

## 스택 (dev-plan 고정값)
pnpm · Vite · React18 · TS(strict) · Tailwind · **shadcn/ui** · react-router-dom · **Zustand + persist(sessionStorage)** · **TanStack Query + axios** · react-hook-form + zod · **Cytoscape + cose-bilkent** · sonner · lucide-react.
Go BFF: Go + Gin (+ CORS 미들웨어).

## 화면 ↔ LangGraph 매핑
| 화면 | 트리거 | BFF | LangGraph stage/노드 | MVP |
|---|---|---|---|---|
| S1 Input (matrix + raw-data) | 생성 시작 | `POST /runs` | intake→retrieve→gen_questions 시작 | **실동작** |
| S2 BRD Review | 자동 표시 + 인라인 편집 | (옵션) `stage:'brd'` interrupt 또는 로컬 편집 | intake 출력(ProblemProfile=BRD) | **실동작** |
| S3 Question + Seed | 승인/수정/재생성 | `POST /resume` | **review①** (gen_questions) | **실동작** |
| S4 Ontology Graph | 자동 표시 | resume 후 done/next | gen_ontology 출력 → Cytoscape | **실동작** |
| S5 DB DDL 입력 | — | — | (없음) | 목업(정적 트리) |
| S6 Alignment | — | — | (없음 — 우리 gap은 레퍼런스 기반, 별개) | 목업 |
| S7 Final + Export | — | done 시 `pack` | assemble_export(MD) | 목업 + `export.markdown` 표시 |

> 개념 매핑: BRD ≈ `problem_profile`, Question Set ≈ `business_questions`, Project Seed ≈ 조립된 pack, Ontology ≈ `ontology`(→ Cytoscape elements, dev-plan §11 변환 규칙).

## ⚠️ 부하 경고 & 완화
한 사람이 **7화면 + Cytoscape + Go BFF** 를 2시간에 데모 품질로 내긴 빠듯하다(dev-plan도 프론트만 140분). 완화:
- BFF는 **15분 얇은 프록시**로 끝낸다(로직 0).
- S5~S7는 **완전 정적 픽스처**.
- S4 Cytoscape가 밀리면 정적 그래프 이미지/단순 렌더로 강등 — **히어로는 S3 review① 라이브**다.
- AI 코어가 먼저 끝나면(~T75) **A1 또는 A2가 S4/Export 프론트 합류** 고려.

## 타임박스 (120분)
| 시간 | 작업 |
|---|---|
| 0–10 | 계약(`stage` 필드 합의), `web` git init, `pnpm create vite` + `shadcn init` + 핵심 패키지 + Router/Zustand/Query providers |
| 10–25 | 공용 컴포넌트(StepHeader/PrimaryButton/ProgressOverlay) + S1 Input + sessionStorage persist |
| 25–30 | **Go BFF 얇은 프록시**(/runs·/resume) — A1 엔드포인트로 패스스루 |
| 30–50 | S2 BRD Review(표시+인라인 편집) + S3 Question+Seed(review① 3버튼) — 목업→실연동(AI /runs ~T30) |
| 50–72 | S4 Ontology Graph(Cytoscape dynamic import + cose-bilkent + cyto-mapper) |
| 72–88 | S5~S7 목업(정적 픽스처) + S7 `export.markdown` 렌더/복사 |
| 88–100 | 전체 E2E(실 AI 연동) + 캔드 폴백 모드 |
| 100–105 | 통합 버그 픽스 |
| 105–120 | **코드 프리즈 → 데모 녹화 주도** |

## Claude Code 세션 프롬프트

### W-S1 — 부트스트랩 + 스토어 + S1
```text
Domain Pack Builder 프론트엔드. 스택 고정: pnpm + Vite + React18 + TS(strict) + Tailwind + shadcn/ui + react-router-dom + Zustand(persist, sessionStorage) + TanStack Query + axios + react-hook-form + zod + cytoscape(+cose-bilkent) + sonner. Next.js/npm/MUI 금지. 계획 후 구현.
1) pnpm create vite (react-ts) → tailwind + `shadcn init`(New York/slate) → add button card tabs sheet dialog badge input textarea select switch progress skeleton scroll-area accordion alert sonner tooltip.
2) App.tsx: Router + QueryClientProvider + Toaster. 라우트 /, /brd, /seed, /graph/ontology, /db, /align, /graph/final + step guard(useEnsureStep).
3) Zustand project-store(persist): projectId, matrix, rawData, brd(profile), questions, projectSeed, ontology, loading/errors. 
4) 공용: StepHeader, PrimaryButton(loading), ProgressOverlay, ErrorBanner.
5) S1 Input(routes/input.tsx): 산업 셀렉터(고정 distribution) + 다중 문서 입력(kind/title/content 추가·삭제) + '생성 시작'. 커밋.
```

### W-S2 — Go BFF 얇은 프록시
```text
web/backend에 Go + Gin BFF를 만든다. 얇은 프록시만. 계획 후 구현.
1) POST /runs → AI(http://ai:8000)/runs 로 그대로 포워드, 응답 패스스루.
2) POST /runs/:id/resume → AI /runs/{id}/resume 포워드.
3) CORS 허용(프론트 origin), run_id는 응답 그대로 전달(상태 보관은 프론트 sessionStorage).
비즈니스 로직 넣지 말 것. AI 미가동 시 목업 JSON 반환 플래그. 커밋.
```

### W-S3 — S2 BRD + S3 Question(review①)
```text
프론트에 S2/S3 추가. interrupt payload의 stage로 라우팅. 계획 후 구현.
1) api/client: postRun, postResume(axios+react-query). USE_MOCK 플래그(하드코딩 payload + canned pack).
2) S2 BRD Review(brd-review.tsx): intake 출력(problem_profile=BRD) 섹션 카드 표시 + 인라인 편집(InlineEditField) + EvidenceBadge(sources). '다음'.
3) S3 Question+Seed(question-seed.tsx): payload.items 질문 카드(question/category/rationale/linked_sources/data_status 뱃지) + coverage 바. review① 3버튼: 승인 / 인라인 수정 / 피드백 재생성 → postResume(action). 응답 stage 따라 화면 전환.
커밋.
```

### W-S4 — Ontology Graph (Cytoscape)
```text
프론트에 S4 Ontology Graph 추가. 계획 후 구현.
1) lib/cyto-mapper: ontology{nodes,relations} → Cytoscape elements (node type별 색: class/kpi/workflow/source). dev-plan §11 규칙.
2) components/graph/GraphCanvas(mode='ontology'): cytoscape dynamic import + cose-bilkent 레이아웃 + skeleton 로딩. GraphToolbar(필터/줌리셋), NodeDetailPanel(sheet, sources 인용).
3) S4(graph-ontology.tsx): store.ontology를 GraphCanvas에 주입.
커밋.
```

### W-S5 — S5~S7 목업 + Export
```text
프론트에 S5~S7 목업 추가 + 전체 흐름. 계획 후 구현.
1) S5 db-input/S6 alignment: 정적 픽스처로 트리뷰·매칭 카드 UI만(비동작).
2) S7 graph-final: GraphCanvas(mode='final') 정적 + ExportPanel: pack.export.markdown 코드블록 + 복사 버튼. (Cypher/JSON은 Phase2)
3) S1→S2→S3→S4 E2E 점검, USE_MOCK on/off 둘 다. 캔드 폴백 확인. 커밋.
```
