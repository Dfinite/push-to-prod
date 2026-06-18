# CLAUDE.md

Domain Pack Builder — 해커톤 프로젝트. 이 워크트리는 **A2(콘텐츠 노드)** 작업용 (`feat/content-nodes`).

## 무엇을 만드는가

BRD·영업 메모 등 비정형 문서를 입력받아, LangGraph 10노드 파이프라인으로 **비즈니스 질문 → 온톨로지 → 워크플로우 → 데모 시나리오 → PoC 체크리스트(Export)** 를 자동 생성하는 ai-service.

- 정본 설계: `docs/노드별설계.md` (노드별 INPUT/OUTPUT JSON), `docs/execution_sequence.md` (당일 실행 순서)
- 작업 카드: `docs/task_A2_content_v2.md` (이 워크트리), `docs/task_A1_infra_v2.md` (인프라/후반 노드)

## 레포 구조

```
ai-service/            # Python 서비스 루트 (여기서 작업)
  schemas.py           # ★ 공유 계약 (typing-only, 런타임 의존성 0). T10 이후 변경 금지.
  canned_pack.json     # 데모 폴백 pack (distribution/TPC-H 예시)
  pyproject.toml       # uv 프로젝트 (deps)
  .python-version      # 3.13.9 (pyenv)
  .env.example         # ANTHROPIC_API_KEY 등 (cp → .env)
docs/                  # 기획/설계 문서
```

## 스택 (고정)

- **Python only · FastAPI · LangGraph**
- Python **3.13.9** (pyenv local), 패키지 관리 **uv**
- 레퍼런스 DB: **DuckDB + tpch** (로컬, `CALL dbgen(sf=0.01)`)
- LLM: **Anthropic Claude** (tool-calling)

## 개발 환경

```bash
cd ai-service
uv sync                       # .venv 생성 + 의존성 설치 (3.13.9)
cp .env.example .env          # ANTHROPIC_API_KEY 채우기
uv run python -c "import schemas"   # 계약 import 확인
```

DuckDB tpch 동작 확인:
```bash
uv run python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL tpch; LOAD tpch; CALL dbgen(sf=0.01)'); print(c.execute('SELECT count(*) FROM lineitem').fetchone())"
```

## A2 담당 노드 (작업 순서)

`docs/task_A2_content_v2.md` 의 세션 프롬프트를 순서대로 수행한다.

1. **intake** (A2-S1): parse·extract(LLM, `PROFILE_TOOL`)·merge·map_provenance·dedup·가드 → `state['problem_profile']`, `state['problem']`. **`profile.*.sources`(근거 문서 title) 꼼꼼히** (S2 EvidenceBadge가 사용).
2. **retrieve** (A2-S2): load_reference(DuckDB tpch)·infer_expected(LLM)·schema_gap → `state['seed']`. → A1 머지.
3. **gen_questions** (A2-S3): build_context·LLM(`QUESTIONS_TOOL`)·validate → `state['business_questions']`. **`data_status`(available|missing:..) 필수**, **id 결정론 부여(q1,q2,…)** (온톨로지 answers 링크 의존). → A1 머지.

## 계약/협업 규칙

- `schemas.py` 는 A1·A2·W(프론트) 공유 계약. **T10 이후 변경 금지** (불가피 시 3명 동시).
- 질문 `id` 는 안정적으로(q1,q2…) — gen_ontology(A1)의 `answers` 링크가 의존.
- `ontology.relations` 양끝 노드 id 는 nodes 에 존재해야 함 (S4 Cytoscape 렌더).
- 5~10분 단위 커밋, A1 으로 머지. 세션 끝마다 커밋.

## 검증 기준

- intake: **ABC상사 예시**(유통 BRD·영업 메모·물류팀 회신)로 `problem_profile` 근거 매핑 확인.
- E2E: 갭 → `data_status` → `required_sources`, `sources` → S2 근거 흐름 확인.
