# Domain Pack Builder — 실행 순서 (당일 마스터)

> 2시간 · 3명(W=프론트+Go BFF, A1=인프라+후반, A2=콘텐츠) · LangGraph 유지 + 7화면 + Go BFF.
> 각 단계의 `[프롬프트]` 전문은 v2 카드(task_W_webapp_v2 / task_A1_infra_v2 / task_A2_content_v2) 참조.

## 🔑 동기화 게이트 (이것만 시간 맞추면 됨)
- **T10** 공통 끝: schemas.py + 계약 확정
- **T30** A1 하드코딩 `/runs`·`/resume`(stage·brd·items + ontology·export 든 canned pack) 머지 → **W 통합 시작**
- **T45** A2 seed 머지 / **T65** A2 질문 머지 → **A1 붙음**
- **T105** 코드 프리즈 → 녹화

---

## 0. 공통 (T0–10, 3명 같이)
1. 레포 2개 push: `web`(W), `ai-service`(A1·A2). 각 `CLAUDE.md` + `.gitignore`(`.claude/worktrees/`).
2. 계약 1분 합의: `/runs`·`/resume` + payload `{stage, brd, items, coverage}` + 최종 pack 필드(ontology·export 포함).
3. **A1**: `ai-service` main에서 schemas.py 생성(아래 프롬프트) → 커밋·push.
4. **A2** `git pull` / **W** schemas → TS 타입 복사(아래 프롬프트).
5. `canned_pack.json`(폴백 결과 한 벌) 합의 → W 보관.
6. 각자 워크트리/레포에서 `claude` + `/init`.

**schemas.py 프롬프트 (A1):**
```text
ai-service main에서 schemas.py를 만든다. typing만 사용(런타임 의존성 X). TypedDict 정의:
- InputDoc{kind,title,content} / PackInput{industry, documents:list[InputDoc], problem:Optional[str]}
- ProfileItem{text, sources:list[str]} / ProblemProfile{goals,pain_points,kpis,constraints:list[ProfileItem], systems:list[str], stakeholders:list[str]}
- Column{name,type} / Table{table, columns:list[Column], pk:list[str], fk:list[dict]}
- Reference{name, schema:list[Table], samples:dict, profile:dict}
- Gap{matched:list[dict], missing:list[dict], extra:list[str]} / Seed{reference:Reference, expected:dict, gap:Gap, gold_questions:list[str]}
- BusinessQuestion{id,question,category,rationale, linked_sources:list[str], data_status}
- OntologyNode{id,name,type, maps_from:list[str], answers:list[str]} / OntologyRelation{source,target,label} / Ontology{nodes,relations}
- Workflow{id,name, steps:list[str], answers_question, uses_nodes:list[str]} / DemoScenario{narrative, steps:list[str], based_on}
- ReviewState{status,feedback:list[str],attempts:int} / RequiredSources{available:list[str], needed:list[str]}
- DomainPackState(TypedDict,total=False): 위 전부 + review_questions/ontology/final:ReviewState + max_attempts:int + export:dict
- DomainPackOutput: industry, business_questions, ontology, workflows, demo_scenario, required_sources, export
- COVERAGE_AREAS 상수 + PROFILE_TOOL/QUESTIONS_TOOL/ONTOLOGY_TOOL input_schema 골격(주석 TODO).
커밋.
```

**TS 타입 프롬프트 (W):**
```text
src/types/index.ts: ai-service/schemas.py(첨부)와 1:1 대응 TS 타입 + RunResponse{run_id, status:'interrupted'|'done', stage?, payload?:{brd:ProblemProfile, items:BusinessQuestion[], coverage:{covered:string[],missing:string[]}}, pack?:DomainPackOutput}. 커밋.
```

---

## W — 순서 (web)
1. (10–15) 부트스트랩: pnpm+vite+`shadcn init`+패키지+providers — **[W-S1]**
2. (15–25) 공용 컴포넌트 + S1 Input + Zustand persist
3. (25–30) Go BFF 얇은 프록시(`/runs`·`/resume`+CORS) — **[W-S2]**
4. (30–50) S2 BRD(로컬 편집) + S3 질문(review① 3버튼) — 목업→실연동(A1 ~30) — **[W-S3]**
5. (50–72) S4 Ontology 그래프(Cytoscape + cyto-mapper) — **[W-S4]**
6. (72–88) S5~S7 목업 + S7 `export.markdown` — **[W-S5]**
7. (88–105) 전체 E2E(실 AI) + 캔드 폴백
8. (105–120) 코드 프리즈 → **데모 녹화 주도**

## A1 — 순서 (ai-service / feat/graph-infra)
1. (0–10) schemas.py 작성·커밋(공통3) + 워크트리
2. (10–30) state+StateGraph 10노드(스텁)+라우터+MemorySaver — **[A1-S1]**
3. (~30) **하드코딩 `/runs`·`/resume`(stage·brd·items)+canned pack 머지** → W 통합 시작
4. (30–60) review_questions 실 interrupt 결선 — **[A1-S2 전반]**
5. (60–80) gen_ontology(스켈레톤→LLM→검증) — **[A1-S2 후반]**
6. (80–95) gen_workflows·gen_demo·assemble_export → **최종 pack 완성** — **[A1-S3]**
7. (95–105) E2E 디버그
8. (105–120) 서버 안정·녹화 지원

## A2 — 순서 (ai-service / feat/content-nodes)
1. (0–10) schemas 확정 + 워크트리 + DuckDB/Claude 키 셋업
2. (10–30) intake(parse·extract·provenance·가드) — `profile.sources` 꼼꼼히 — **[A2-S1]**
3. (30–45) retrieve(TPC-H+갭) → **머지(→A1 seed/profile)** — **[A2-S2]**
4. (45–65) gen_questions(`data_status`·안정 `id`) → **머지(→A1)** — **[A2-S3]**
5. (65–85) 온톨로지 결합·질문 폴리시·정합 통합 지원
6. (85–105) E2E 디버그(갭→data_status→required_sources, sources→S2)
7. (105–120) 녹화 지원

---

## 생존 규칙
1. **계약 우선** — T10 이후 계약·schemas 변경 금지(불가피 시 3명 동시).
2. **공격적 목업** — W는 USE_MOCK으로 화면 완성, A1은 하드코딩 payload 먼저.
3. **5~10분 단위 커밋·머지** — 워크트리로 충돌 0.
4. **T105 코드 프리즈** — 이후 녹화만.
5. **캔드 폴백** — 통합 깨지면 W를 `canned_pack.json` 모드로 전환해 데모 성립.
6. **스코프 고정** — S5~S7·review②③·RAG·Notion·PII는 목업. W 과부하 시 S4 강등 또는 AI 인력 합류.

## 데모 녹화 (T105–120, ~90초)
1. "PoC 세팅을 반복 가능하게" 인트로
2. ABC상사 BRD·영업 메모 입력(S1)
3. 추출된 BRD + 데이터 갭(확보/확보필요) 확인(S2)
4. **질문셋 검토 ①에서 수정/재생성 라이브**(S3) ← 하이라이트
5. 온톨로지 그래프 자동 생성(S4, Cytoscape)
6. PoC 체크리스트 Export(S7)
> 화면 녹화 미리 세팅, 입력 클립보드 준비, 1~2테이크 + 캔드 모드로 보험 1테이크.
