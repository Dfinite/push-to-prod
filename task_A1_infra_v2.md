# A1 — ai-service (인프라 + 후반 노드) 작업 카드 v2

> 변경 결정 반영: LangGraph 유지 · 프론트 7화면 · Go BFF(얇은 프록시) · **S2 BRD = 로컬 편집(인터럽트 1개)**.

## v2 변경점 (v1 대비)
1. **interrupt payload에 `stage` + `brd` 추가** — `{stage:'questions', brd: problem_profile, items: business_questions, coverage}`. 프론트가 `stage`로 화면 라우팅, `brd`로 S2 표시.
2. **최종 pack에 `ontology`(S4 Cytoscape용)와 `export.markdown`(S7용) 반드시 포함.**
3. AI 엔드포인트는 그대로(`/runs`·`/resume`) — **Go BFF가 1:1 프록시**할 뿐, AI 쪽 변경 없음.
4. 인터럽트는 **질문 1개 유지**(S2는 프론트 로컬 편집). *인터럽트 추가를 원하면 intake 뒤 `review_brd` 노드 1개만 더하면 됨.*

스택: **Python only · FastAPI · LangGraph** · 워크트리 `feat/graph-infra`.

## 셋업 (T0, A2와 함께)
```bash
# main: schemas.py / app.py / CLAUDE.md 커밋·푸시 후
git worktree add ../ai-infra -b feat/graph-infra && cd ../ai-infra && claude   # /init
```

## 타임박스
| 시간 | 작업 | 핸드오프 |
|---|---|---|
| 0–10 | `schemas.py` 공동→main, FastAPI 스켈레톤, 워크트리/`/init` | schemas → 공유 |
| 10–30 | state·StateGraph 10노드·라우터·MemorySaver — F-H1~H5 | — |
| 30 | **`/runs`·`/resume`를 `{stage,brd,items,coverage}` 하드코딩 + canned pack(ontology·export 포함)으로 노출 → 머지** | **→ W(S2·S3·S4·S7) 통합 시작** |
| 30–60 | review_questions 실 interrupt(payload에 stage·brd) | gen_questions ← A2(~65) |
| 60–80 | gen_ontology(스켈레톤→LLM→검증) — F-E* | seed/질문 ← A2 |
| 80–95 | gen_workflows·gen_demo·assemble_export(ontology+markdown 포함) | → W 최종 pack |
| 95–105 | 통합 디버그(`/runs`→interrupt(stage)→`/resume`→done) | 합류 |
| 105–120 | 서버 안정·녹화 지원 | — |

## Claude Code 세션 프롬프트

### A1-S1 — 그래프 골격 + 서버(하드코딩 payload)
```text
Domain Pack Builder의 ai-service(Python only, FastAPI + LangGraph). schemas.py(첨부) 사용. 계획 후 구현.
1) DomainPackState(TypedDict): industry, documents, problem, problem_profile, seed, business_questions, ontology, workflows, demo_scenario, required_sources, review_questions/ontology/final({status,feedback,attempts}), max_attempts, export.
2) StateGraph 10노드(intake,retrieve,gen_questions,review_questions,gen_ontology,review_ontology,gen_workflows,gen_demo,review_final,assemble_export) — 전부 스텁. review_ontology/review_final 자동승인.
3) 라우터 route_questions/route_ontology/route_final + 조건부 엣지.
4) MemorySaver, interrupt 가능하게 compile.
5) FastAPI: POST /runs(invoke 시작, run_id=thread_id), POST /runs/{id}/resume(Command(resume=...)).
   지금은 review_questions가 다음 형태의 interrupt를 던진다:
   { "stage":"questions",
     "brd": <problem_profile 더미>,
     "items": [BusinessQuestion 더미 2개],
     "coverage": {"covered":[...], "missing":[...]} }
   끝까지 가면 canned pack 반환(반드시 ontology{nodes,relations}와 export.markdown 포함).
curl로 /runs→interrupt(stage 확인), /resume(approve)→done(pack에 ontology·export 확인). 커밋·머지.
```

### A1-S2 — review① 실결선 + gen_ontology
```text
ai-service feat/graph-infra. 계획 후 구현.
1) review_questions 실 interrupt: payload = {stage:'questions', brd: state['problem_profile'], items: state['business_questions'], coverage}. resume decision(action/edited_items/feedback)으로 review_questions.{status,feedback,attempts} 기록, edit면 business_questions 갱신. route_questions: rejected & attempts<max → gen_questions, 아니면 gen_ontology.
2) gen_ontology: fk_skeleton(seed.reference FK→관계, 테이블→노드) → Claude tool-calling(ONTOLOGY_TOOL: name,type,maps_from,answers, relations) → validate(관계 양끝·질문 커버·dedup). 출력 state['ontology'].
A2 머지본(retrieve/gen_questions)과 정합 확인. 커밋·머지.
```

### A1-S3 — workflows / demo / export (pack 완성)
```text
ai-service feat/graph-infra. 계획 후, gen_workflows·gen_demo는 가능하면 isolation: worktree 서브에이전트로 병렬.
1) gen_workflows: tool-calling 2~3개, answers_question·uses_nodes 강제, 가벼운 검증.
2) gen_demo: pick_top → Claude로 narrative+steps+based_on.
3) assemble_export: required_sources={available,needed}(질문 linked_sources ∪ maps_from ∪ seed.gap.missing). render_markdown(PoC 체크리스트).
   **최종 pack(=resume 종료 응답)에 industry, business_questions, ontology{nodes,relations}, workflows, demo_scenario, required_sources, export.markdown 전부 포함** (프론트 S4 그래프·S7 Export가 사용).
전체 /runs→interrupt→resume→done E2E 확인. 커밋·머지.
```
