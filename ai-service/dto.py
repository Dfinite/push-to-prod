"""HTTP boundary DTOs — Pydantic v2 models for request/response validation.

`schemas.py`의 TypedDict는 그래프 내부 state 계약(typing only)이라 런타임 검증을 못 한다.
DTO는 그 TypedDict와 **필드 1:1** 로 대응하되, FastAPI 경계에서 422를 던질 수 있도록 한다.

- 잘못된 action / 누락된 edited_items / 누락된 feedback → 422.
- 변환은 `model_dump()` 로 plain dict 만들어 LangGraph state 에 그대로 흘려보낸다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# 입력 — POST /runs
# ---------------------------------------------------------------------------


class InputDocDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    title: str
    content: str


class PackInputDTO(BaseModel):
    """schemas.PackInput 과 1:1. industry는 MVP 고정값 외에도 허용해 운영 확장 여지."""

    model_config = ConfigDict(extra="forbid")
    industry: str = Field(default="distribution", min_length=1)
    documents: List[InputDocDTO] = Field(default_factory=list)
    problem: Optional[str] = None
    max_attempts: int = Field(default=3, ge=1, le=5)


# ---------------------------------------------------------------------------
# 입력 — POST /runs/{id}/resume
# ---------------------------------------------------------------------------


class BusinessQuestionDTO(BaseModel):
    """schemas.BusinessQuestion 과 1:1. id 는 edit 시 비어올 수 있어 Optional."""

    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = None
    question: str
    category: str
    rationale: str = ""
    linked_sources: List[str] = Field(default_factory=list)
    data_status: str = "available"


class ResumeDecisionDTO(BaseModel):
    """schemas.ResumeDecision 과 1:1. action별 필수 필드를 422 로 강제한다."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "edit", "regenerate"]
    edited_items: Optional[List[BusinessQuestionDTO]] = None
    feedback: Optional[str] = None

    @model_validator(mode="after")
    def _enforce_per_action(self) -> "ResumeDecisionDTO":
        if self.action == "edit" and not self.edited_items:
            raise ValueError("edited_items is required when action='edit'")
        if self.action == "regenerate" and not (self.feedback and self.feedback.strip()):
            raise ValueError("non-empty feedback is required when action='regenerate'")
        return self


# ---------------------------------------------------------------------------
# 응답 — RunResponse (interrupted / done)
# ---------------------------------------------------------------------------


class RunResponse(BaseModel):
    """W(BFF/프론트) 와 합의된 응답 구조.

    - 일시정지:  {run_id, status:"interrupted", stage, payload}
    - 완료:      {run_id, status:"done", pack}
    """

    model_config = ConfigDict(extra="allow")
    run_id: str
    status: Literal["interrupted", "done"]
    stage: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    pack: Optional[Dict[str, Any]] = None
