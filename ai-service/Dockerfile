# Domain Pack Builder — ai-service (FastAPI + LangGraph)
# 서버 시스템 파이썬은 3.10 이라 컨테이너로 격리. python:3.13 은 Docker Hub 익명 pull
# rate limit(429) 에 걸려, 서버에 이미 캐시된 python:3.11 을 베이스로 쓴다.
# 코드에 3.12+ 전용 문법(PEP695 등) 없음 → 3.11 에서 동작 확인됨.
FROM python:3.11

WORKDIR /app

# psycopg[binary] 는 libpq 를 번들하므로 별도 apt 불필요. pip 캐시는 끈다.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt ./
RUN pip install -r requirements.txt

# 앱 소스 (.dockerignore 로 .venv/out/tests 등 제외).
COPY . .

EXPOSE 8000

# 단일 워커 — LangGraph MemorySaver 는 인메모리 thread state 라 멀티워커 시 thread 라우팅이 깨진다.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
