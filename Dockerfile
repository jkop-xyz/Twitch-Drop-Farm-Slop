FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/jkop-xyz/Twitch-Drop-Farm-Slop" \
      org.opencontainers.image.description="Headless Twitch Drops and Channel Points farmer with a web UI (AI-generated)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --uid 1000 --create-home bot && mkdir -p /data && chown bot /data

USER bot
VOLUME /data
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=4)"

CMD ["dropbot"]
