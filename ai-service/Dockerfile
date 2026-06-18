# Domain Pack Builder — ai-service (FastAPI + LangGraph)
# 서버 시스템 파이썬은 3.10 이라 requires-python>=3.13 을 못 맞춤 → 컨테이너로 3.13 고정.
FROM python:3.13-slim

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
