# docs/api/

ai-service API 스펙 정본 디렉터리. 두 파일:

| 파일 | 역할 | 형식 | 누가 읽나 |
|---|---|---|---|
| [`spec.md`](./spec.md) | **사람용 자세한 스펙** — 엔드포인트 / 데이터 모델 / TS 타입 / 시퀀스 다이어그램 / curl 예제 / 422 케이스 / 모드 / 변경 정책 | Markdown (~700줄) | W BFF·view 담당자, A1·A2 협업자 |
| [`openapi.json`](./openapi.json) | **기계용 정본** (FastAPI 자동 생성) | OpenAPI 3.x JSON (392줄) | TS 타입 생성기, OpenAPI viewer, BFF 라우팅 코드 생성 |

## 빠른 시작 (W 측)

1. `spec.md §10` TypeScript 타입을 view 측 `src/types/index.ts`에 1:1 복사
2. `spec.md §11` curl 예제로 BFF 라우팅 검증
3. `spec.md §부록 A` 체크리스트로 통합 확인
4. R-11 협의(`required_sources.available` 표시 방식)는 `docs/integration/handoff-to-W-2026-06-18.md §5.1`

## 갱신 방법

스펙 변경 시 — `spec.md §12` 참조:

```bash
cd ai-service && source .venv/bin/activate
uvicorn app:app --port 8888 --log-level warning >/dev/null 2>&1 &
SP=$!
until curl -sf http://127.0.0.1:8888/healthz >/dev/null 2>&1; do sleep 0.2; done
curl -s http://127.0.0.1:8888/openapi.json | python -m json.tool > ../docs/api/openapi.json
kill $SP
```

⚠️ `openapi.json` 만 갱신하면 안 됨 — `spec.md` 도 동시 갱신 (drift 방지).
변경 정책: `spec.md §9` (T10 freeze — 3명 합의 필요).

## 디렉터리 역할 비교

| 디렉터리 | 책임 |
|---|---|
| `docs/api/` | **API 계약 정본**. 엔드포인트 변경 시 갱신. |
| `docs/integration/` | **W·BFF·view 와의 연동 가이드 + 협의 사항**. 결정·교환 메시지. |
| `docs/work-log/` | **A1 진행 기록 + 리스크 인벤토리**. 시점별 산출/검증/회고. |
| `docs/` (루트) | **PRD / dev-plan / 노드별설계 / 작업 카드 / decisions** (정본 8종). |

## 관련 문서

- [`../integration/handoff-to-W-2026-06-18.md`](../integration/handoff-to-W-2026-06-18.md) — W 컨택 핸드오프
- [`../decisions_a1_v1.md`](../decisions_a1_v1.md) — A1 결정 카탈로그 (D-1~D-13)
- [`../lld_a1_v1.md`](../lld_a1_v1.md) — A1 Low-Level Design
- [`../work-log/a1-merge-risks-2026-06-18.md`](../work-log/a1-merge-risks-2026-06-18.md) — S2/S3 머지 후 리스크 인벤토리 (R-1 ~ R-21)
