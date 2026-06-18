"""S1 intake 노드 — 문서들에서 ProblemProfile 을 추출·정규화·근거매핑한다.

흐름: documents → 청크 분할 → (LLM extract) 필드별 추출 → 정규화 dedup merge →
      후보 문서 선정(containment) → (LLM verify) 근거 검증 → 교집합 가드 →
      미지지 항목 제거 → 필드 cap → problem 합성.

핵심 설계:
- extract / verify 는 주입 가능한 seam (기본은 실 LLM 호출, 테스트는 fake 주입).
- dedup KEY 는 구두점 제거(`_key`), 표시 surface 는 first-seen 원문 유지.
- containment 는 비대칭 |item∩doc|/|item| — recall 지향.
- verify 결과는 반드시 후보 allow set 과 교집합 (할루시 title 제거).
- intake 는 입력 state 를 절대 변형하지 않는다 (fresh dict 만 반환).

키 없이 import 가능: anthropic 은 llm.get_client() 안에서 지연 import 된다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Dict, List, Optional, Tuple

from llm import call_tool, PROFILE_TOOL

# ---------------------------------------------------------------------------
# 0. 상수
# ---------------------------------------------------------------------------

CHUNK_SIZE = 6000
CHUNK_OVERLAP = 200
FIELD_CAP = 8
CONTAINMENT_THRESHOLD = 0.55  # 비대칭 |item∩doc|/|item|, recall 지향 (0.5–0.6 밴드)
MAX_CANDIDATES = 4  # item 당 후보 문서 상한 (verifier 토큰 비용 가드)

# ProfileItem({text, sources}) 로 머지되는 필드
ITEM_FIELDS = ("goals", "pain_points", "kpis", "constraints")
# 단순 문자열 집합 필드
SET_FIELDS = ("systems", "stakeholders")

# verifier(근거 판정) 전용 tool — 노드 로컬 dict. llm.py 에 넣지 않는다.
SUPPORT_TOOL: Dict[str, object] = {
    "name": "emit_support",
    "description": (
        "주어진 '항목 문장'을 '문서들' 중 어떤 문서가 실제로 지지(entailment)하는지 판정한다. "
        "문서 제목(title)만 반환. 문서에 근거가 없으면 빈 배열을 반환할 것 — 추측 금지."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "supporting_titles": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["supporting_titles"],
    },
}


# ---------------------------------------------------------------------------
# 1. 정규화 트리오 (_norm / _key / _tokens)
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """표시 surface 정규화 베이스: NFKC → casefold → 공백 collapse."""
    s = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"\s+", " ", s).strip()


def _key(s: str) -> str:
    r"""dedup 비교 KEY 전용: _norm 후 구두점 제거(`\w`/공백만 유지) + 공백 재collapse.

    표시에는 절대 쓰지 않는다 (예: golden 의 '·' 는 surface 에만 남는다).
    `\w` 는 Python re 에서 한글을 포함하므로 토큰이 보존된다.
    """
    n = _norm(s)
    stripped = re.sub(r"[^\w\s]", "", n)
    return re.sub(r"\s+", " ", stripped).strip()


def _tokens(s: str) -> set:
    """containment 스코어용 토큰 집합 — `set(_key(s).split())`."""
    return set(_key(s).split())


def _set_key(s: str) -> str:
    r"""SET_FIELDS 전용 dedup KEY: 후행 괄호 설명(descriptor)을 떼고 `_key`.

    entity 본체(앞부분)로만 dedup 한다. ASCII `()` 와 전각 `（）` 모두 처리하고
    공백을 collapse → `"김성태 (구매SCM본부장)"` 와 `"김성태 (본부장, 의사결정자)"`
    가 같은 키 `"김성태"` 로 묶인다. item 필드(괄호 디테일이 유의미)에는 쓰지 않는다.
    """
    stripped = re.sub(r"\s*[(（].*?[)）]\s*", " ", s)
    return _key(stripped)


def _is_junk(text: str) -> bool:
    r"""추출 문자열을 버려야 하면 True (보수적 — 정상 콘텐츠는 절대 버리지 않는다).

    버리는 경우:
    - `_key(text)` 가 빈 문자열 (영숫자/한글 알맹이가 없음), 또는
    - `_norm` 후 꺾쇠 placeholder 형태 `^<.*>$` (예: `<UNKNOWN>`, `<none>`).
    """
    if not _key(text):
        return True
    return bool(re.match(r"^<.*>$", _norm(text)))


# ---------------------------------------------------------------------------
# 2. 청크 분할 (_chunk_text)
# ---------------------------------------------------------------------------


def _chunk_text(
    content: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> List[str]:
    """문서 content 를 슬라이스로 분할. 빈 문자열 → []; size 이하 → [content].

    overlap 만큼 겹치며 step=max(1, size-overlap) 으로 전체 커버 보장
    (overlap>=size 인 비정상 입력에서도 무한루프 방지).
    """
    if not content.strip():
        return []
    if len(content) <= size:
        return [content]
    step = max(1, size - overlap)
    return [content[i : i + size] for i in range(0, len(content), step)]


# ---------------------------------------------------------------------------
# 3. containment 스코어 (_containment)
# ---------------------------------------------------------------------------


def _containment(item_text: str, doc_text: str) -> float:
    """비대칭 토큰 포함도: |item∩doc| / |item|. item 토큰이 비면 0.0."""
    it = _tokens(item_text)
    if not it:
        return 0.0
    dt = _tokens(doc_text)
    return len(it & dt) / len(it)


# ---------------------------------------------------------------------------
# 4. 청크 수집 + 필드별 추출 (_collect_chunks / _extract_all)
# ---------------------------------------------------------------------------


def _collect_chunks(documents: List[dict]) -> List[Tuple[str, str]]:
    """문서들을 평탄화 → [(title, chunk), ...]. content 별로 _chunk_text 적용."""
    pairs: List[Tuple[str, str]] = []
    for d in documents:
        title = d["title"]
        for chunk in _chunk_text(d["content"]):
            pairs.append((title, chunk))
    return pairs


def _extract_all(
    chunks: List[Tuple[str, str]],
    extract: Callable[[str, str], dict],
) -> Dict[str, List[Tuple[str, str]]]:
    """청크별로 extract(title, chunk) 실행 후 필드별 (text, title) 쌍 수집.

    누락 키는 방어적으로 .get(k, []) 처리. SET_FIELDS 도 (text, title) 로 모으되
    title 은 set merge 에서 무시된다.
    """
    out: Dict[str, List[Tuple[str, str]]] = {f: [] for f in ITEM_FIELDS + SET_FIELDS}
    for title, chunk in chunks:
        emitted = extract(title, chunk)
        for f in ITEM_FIELDS + SET_FIELDS:
            for text in emitted.get(f, []):
                out[f].append((text, title))
    return out


# ---------------------------------------------------------------------------
# 5. merge (_merge_items / _merge_set)
# ---------------------------------------------------------------------------


def _merge_items(pairs: List[Tuple[str, str]]) -> List[dict]:
    """_key 기준 dedup. surface 는 first-seen 원문, sources 는 first-seen 순서 title union."""
    order: List[str] = []  # key 등장 순서
    surface: Dict[str, str] = {}  # key → first-seen text
    sources: Dict[str, List[str]] = {}  # key → first-seen 순서 title 목록
    for text, title in pairs:
        if _is_junk(text):
            continue
        k = _key(text)
        if k not in surface:
            order.append(k)
            surface[k] = text
            sources[k] = []
        if title not in sources[k]:
            sources[k].append(title)
    return [{"text": surface[k], "sources": sources[k]} for k in order]


def _merge_set(pairs: List[Tuple[str, str]]) -> List[str]:
    """_set_key 기준 dedup, first-seen surface 유지. title 은 버린다.

    entity 본체로만 묶으므로 후행 괄호 설명이 다른 동일 인물/시스템이 하나로 합쳐진다
    (예: 김성태 (구매SCM본부장) / 김성태 (본부장, 의사결정자) → 전자 surface 유지).
    junk(빈 키·placeholder) 는 keying 전에 건너뛴다.
    """
    order: List[str] = []
    surface: Dict[str, str] = {}
    for text, _title in pairs:
        if _is_junk(text):
            continue
        k = _set_key(text)
        if k not in surface:
            order.append(k)
            surface[k] = text
    return [surface[k] for k in order]


# ---------------------------------------------------------------------------
# 6. 후보 문서 선정 (_candidate_docs)
# ---------------------------------------------------------------------------


def _candidate_docs(item: dict, doc_texts: Dict[str, str]) -> List[dict]:
    """containment≥threshold 인 문서 + item 의 기존 source title 을 항상 seed.

    스코어 내림차순 정렬 후 MAX_CANDIDATES 로 cap. [{title, content}] 반환.
    """
    scored: Dict[str, float] = {}
    for title, content in doc_texts.items():
        score = _containment(item["text"], content)
        if score >= CONTAINMENT_THRESHOLD:
            scored[title] = score
    # item 의 기존 source title 은 임계 미달이어도 항상 후보에 포함 (seed)
    for title in item["sources"]:
        if title not in scored and title in doc_texts:
            scored[title] = _containment(item["text"], doc_texts[title])
    ranked = sorted(scored, key=lambda t: scored[t], reverse=True)[:MAX_CANDIDATES]
    return [{"title": t, "content": doc_texts[t]} for t in ranked]


# ---------------------------------------------------------------------------
# 7. 근거 매핑 (_map_provenance_item)
# ---------------------------------------------------------------------------


def _map_provenance_item(
    item: dict,
    doc_texts: Dict[str, str],
    verify: Callable[[str, List[dict]], List[str]],
) -> dict:
    """후보 → verify → allow set 교집합 가드 → 순서보존 dedup.

    verified ∩ allow 교집합이 할루시(후보에 없는 title) 를 제거하는 필수 가드.
    어떤 verify 구현이 주입되든 verify 직후 반드시 실행된다.
    """
    cands = _candidate_docs(item, doc_texts)
    allow = {c["title"] for c in cands}
    verified = verify(item["text"], cands)
    sources: List[str] = []
    for t in verified:
        if t in allow and t not in sources:
            sources.append(t)
    return {"text": item["text"], "sources": sources}


# ---------------------------------------------------------------------------
# 8. cap / synthesize
# ---------------------------------------------------------------------------


def _cap(items: list, n: int = FIELD_CAP) -> list:
    """순서 보존 truncate to n."""
    return items[:n]


def _synthesize(problem: Optional[str], pain_points: List[dict]) -> str:
    """problem 이 truthy 면 그대로; 아니면 상위 2개 pain_point text 를 ' / ' 로 join.

    pain_point 가 없으면 '' (비크래시 fallback).
    """
    if problem:
        return problem
    if not pain_points:
        return ""
    return " / ".join(p["text"] for p in pain_points[:2])


# ---------------------------------------------------------------------------
# 9. LLM seam 기본 구현 (_default_extract / _default_verify)
# ---------------------------------------------------------------------------


def _default_extract(title: str, text: str) -> dict:
    """실 extract: PROFILE_TOOL 로 6개 필드 추출 (temperature 0.2). 누락 키 방어."""
    system = (
        "너는 유통/물류 도메인 문서에서 문제 프로파일을 추출하는 분석가다. "
        "문서에 실제로 드러난 표현만 추출하고, 근거 없는 내용은 만들지 마라."
    )
    data = call_tool(system=system, user=text, tool=PROFILE_TOOL, temperature=0.2)
    return {k: data.get(k, []) for k in ITEM_FIELDS + SET_FIELDS}


def _default_verify(item_text: str, docs: List[dict]) -> List[str]:
    """실 verify: SUPPORT_TOOL 로 지지 문서 title 판정 (temperature 0.0)."""
    if not docs:
        return []
    system = (
        "너는 근거 검증자다. 주어진 항목 문장을 실제로 지지(entailment)하는 문서의 "
        "title 만 반환하고, 근거 없는 문서는 절대 포함하지 마라."
    )
    doc_block = "\n\n".join(f"[{d['title']}]\n{d['content']}" for d in docs)
    user = f"항목 문장: {item_text}\n\n문서들:\n{doc_block}"
    data = call_tool(system=system, user=user, tool=SUPPORT_TOOL, temperature=0.0)
    return data.get("supporting_titles", [])


# ---------------------------------------------------------------------------
# 10. 노드 진입점 (intake)
# ---------------------------------------------------------------------------


def intake(
    state: dict,
    *,
    extract: Callable[[str, str], dict] = _default_extract,
    verify: Callable[[str, List[dict]], List[str]] = _default_verify,
) -> dict:
    """intake 노드. {"problem_profile": ProblemProfile, "problem": str} 반환.

    입력 state 는 읽기 전용 — .update 나 in-place 편집 없이 fresh dict 만 만든다.
    """
    documents = state.get("documents", [])  # 변형 금지
    problem_in = state.get("problem")  # None / "" 일 수 있음
    # title 중복 시 last-wins (픽스처 title 은 유일하므로 허용)
    doc_texts = {d["title"]: d["content"] for d in documents}

    chunks = _collect_chunks(documents)
    extracted = _extract_all(chunks, extract)

    profile: Dict[str, list] = {}
    for f in ITEM_FIELDS:
        merged = _merge_items(extracted[f])
        mapped = [_map_provenance_item(it, doc_texts, verify) for it in merged]
        supported = [it for it in mapped if it["sources"]]  # drop_unsupported
        profile[f] = _cap(supported)
    for f in SET_FIELDS:
        profile[f] = _cap(_merge_set(extracted[f]))

    problem_out = _synthesize(problem_in, profile["pain_points"])
    return {"problem_profile": profile, "problem": problem_out}
