# Crypto screener UI/API (no Telegram bot). Build context = repository root.
# 3.11 = the version CI tests (.github/workflows/ci.yml). Tag verified on Docker Hub.
FROM python:3.11.17-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime deps only, exactly as listed (all have manylinux wheels: no compiler needed).
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY . /app

# Unprivileged user; /data (SQLite) and /app/.data (liquidity archive, analyst log) are bind mounts.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /app/.data \
    && chown 10001:10001 /data /app/.data
USER 10001

EXPOSE 5087
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5087/health', timeout=4).status == 200 else 1)"

CMD ["python", "deploy/contabo/run.py"]
