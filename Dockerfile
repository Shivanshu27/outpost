# Multi-stage: build the SPA, install Python, ship neither toolchain.
#
# The frontend build is a separate stage so that Node never reaches the final
# image — it is needed to produce `assets/`, not to serve it.

# ---------------------------------------------------------------- web
#
# Debian-based rather than Alpine. `node:22-alpine` reproducibly failed here
# with "npm error Exit handler never called!" after ~8 minutes — npm being
# OOM-killed against musl. The slim image is larger, but this stage is
# discarded after the build, so the cost is build time, not image size.
FROM node:22-slim AS web

WORKDIR /web
# Copy manifests first so `npm ci` is cached independently of source changes.
COPY web/package.json web/package-lock.json ./
# --no-audit/--no-fund cut a surprising amount of work and network from a
# non-interactive build; --prefer-offline reuses the layer cache where it can.
#
# If this step hangs for ~8 minutes and dies with "npm error Exit handler never
# called!", the lockfile is pointing at a registry the container cannot reach.
# npm blames itself in that message, which is misleading: check
# `grep resolved web/package-lock.json` for a host that is not
# registry.npmjs.org. A lockfile generated behind a corporate npm mirror
# carries that mirror's URLs, and they resolve for nobody else.
# `web/.npmrc` pins the public registry to prevent exactly that.
RUN npm ci --no-audit --no-fund --prefer-offline

COPY web/ ./
# Vite writes to ../src/outpost/resources/web, so give it somewhere to land.
RUN mkdir -p /src/outpost/resources && npm run build


# ---------------------------------------------------------------- python
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies before source: this layer survives every code change.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN uv pip install --system --no-cache .

COPY --from=web /src/outpost/resources/web/ ./src/outpost/resources/web/
RUN uv pip install --system --no-cache --no-deps .

# Non-root. The tool reads a resume and writes a database of the user's job
# search; there is no reason for it to run as root.
RUN useradd --create-home --uid 10001 outpost \
    && mkdir -p /data /config \
    && chown -R outpost:outpost /data /config /app
USER outpost

# Config and data are volumes: everything Outpost knows about its user lives
# here, and none of it belongs in an image layer.
ENV OUTPOST_CONFIG_DIR=/config \
    OUTPOST_DATA_DIR=/data \
    OUTPOST_UI_HOST=0.0.0.0
VOLUME ["/config", "/data"]

# 0.0.0.0 inside the container is bound to loopback on the host by compose.
# `outpost doctor` warns about this bind precisely because it would be wrong
# outside a container.
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/health', timeout=2).status==200 else 1)"

ENTRYPOINT ["outpost"]
CMD ["ui", "--no-browser"]
