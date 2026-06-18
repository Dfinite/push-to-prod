"""Domain Pack Builder — FastAPI thin server (A1-S1).

엔드포인트는 단 2개. Go BFF (W 담당) 가 1:1 프록시한다.

- POST /runs            : PackInput → invoke. interrupt 또는 done 응답.
- POST /runs/{id}/resume : ResumeDecision → Command(resume=...) 로 재개.

응답 구조 (W 와 합의된 RunResponse):
- 일시정지:  {run_id, status:"interrupted", stage, payload}
- 완료:      {run_id, status:"done", pack}

입력 검증은 `dto.py` 의 Pydantic 모델이 담당 → 422 자동 응답.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command

from dto import PackInputDTO, ResumeDecisionDTO, RunResponse
from graph import GRAPH

app = FastAPI(title="Domain Pack Builder — ai-service", version="0.1.0-a1s1")

# 프론트 dev 서버 (Vite default 5173) 와 Go BFF 모두 허용.
# 운영 단계에서는 BFF Origin 화이트리스트로 좁힌다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _config(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _normalize(thread_id: str, final_state: Dict[str, Any]) -> RunResponse:
    """invoke 결과를 RunResponse 로 정규화.

    LangGraph >=0.2.40 은 interrupt 발생 시 state 에 `__interrupt__` 키 (Interrupt 객체 리스트)를 채워서 돌려준다.
    이 키가 없으면 그래프가 END 까지 도달한 것 → pack 추출.
    """
    interrupts = final_state.get("__interrupt__")
    if interrupts:
        first = interrupts[0]
        payload = getattr(first, "value", first)
        stage = payload.get("stage") if isinstance(payload, dict) else None
        return RunResponse(
            run_id=thread_id,
            status="interrupted",
            stage=stage,
            payload=payload if isinstance(payload, dict) else {"value": payload},
        )

    pack = {
        "industry": final_state.get("industry"),
        "business_questions": final_state.get("business_questions", []),
        "ontology": final_state.get("ontology", {"nodes": [], "relations": []}),
        "workflows": final_state.get("workflows", []),
        "demo_scenario": final_state.get("demo_scenario", {}),
        "required_sources": final_state.get(
            "required_sources", {"available": [], "needed": []}
        ),
        "export": final_state.get("export", {"markdown": ""}),
    }
    return RunResponse(run_id=thread_id, status="done", pack=pack)


@app.post("/runs", response_model=RunResponse)
def start_run(body: PackInputDTO) -> RunResponse:
    """그래프 진입. PackInput 형태의 body 를 받아 새 thread 로 invoke."""
    thread_id = str(uuid.uuid4())
    initial: Dict[str, Any] = body.model_dump()
    # max_attempts 는 state 의 별도 키로 유지 (schemas.DomainPackState.max_attempts)
    final_state = GRAPH.invoke(initial, config=_config(thread_id))
    return _normalize(thread_id, final_state)


@app.post("/runs/{run_id}/resume", response_model=RunResponse)
def resume_run(run_id: str, body: ResumeDecisionDTO) -> RunResponse:
    """interrupt 에서 재개. body 는 ResumeDecision (action / edited_items / feedback)."""
    decision: Dict[str, Any] = body.model_dump(exclude_none=True)
    final_state = GRAPH.invoke(Command(resume=decision), config=_config(run_id))
    return _normalize(run_id, final_state)


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}
