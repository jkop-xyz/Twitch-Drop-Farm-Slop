# Build stage: turn the source into a wheel, so no source tree or build leftovers ship.
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY src ./src
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/jkop-xyz/Twitch-Drop-Farm-Slop" \
      org.opencontainers.image.description="Headless Twitch Drops and Channel Points farmer with a web UI (AI-generated)"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY --from=build /wheels /tmp/wheels
RUN pip install --no-deps /tmp/wheels/*.whl && rm -r /tmp/wheels

# Rootless by default: a fixed, numeric, unprivileged uid (so `runAsNonRoot` can verify it),
# and a /data owned by group 0 so any uid run with `--user <uid>:0` can still write to it.
RUN useradd --system --uid 10001 --gid 0 --home-dir /data --no-create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /data && chown 10001:0 /data && chmod 0770 /data

USER 10001:0
ENV HOME=/data
WORKDIR /data
VOLUME /data
ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=4)"

CMD ["dropbot"]
