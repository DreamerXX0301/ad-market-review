ARG NODE_IMAGE=public.ecr.aws/docker/library/node:22.22.1-bookworm-slim@sha256:4f77a690f2f8946ab16fe1e791a3ac0667ae1c3575c3e4d0d4589e9ed5bfaf3d
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.6.13@sha256:0b6dc79013b689f3bc0cbf12807cb1c901beaafe80f2ee10a1d76aa3842afb92
FROM ${NODE_IMAGE} AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.* ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM ${PYTHON_IMAGE} AS sqlite-builder
ARG SQLITE_ARCHIVE=sqlite-autoconf-3540100
ARG SQLITE_SHA3=b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl --fail --location --retry 3 "https://www.sqlite.org/2026/${SQLITE_ARCHIVE}.tar.gz" -o sqlite.tar.gz \
 && python -c "import hashlib; assert hashlib.sha3_256(open('sqlite.tar.gz','rb').read()).hexdigest() == '${SQLITE_SHA3}', 'SQLite source checksum mismatch'" \
 && tar -xzf sqlite.tar.gz \
 && cd "${SQLITE_ARCHIVE}" && ./configure --prefix=/opt/sqlite --disable-readline \
 && make -j2 && make install

FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS runtime
ARG APP_VERSION=0.1.23
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="冒险的梦想家" \
    org.opencontainers.image.description="A股盘面与策略研究工作台" \
    org.opencontainers.image.source="https://github.com/DreamerXX0301/ad-market-review" \
    org.opencontainers.image.url="https://github.com/DreamerXX0301/ad-market-review" \
    org.opencontainers.image.version="${APP_VERSION}" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.created="${BUILD_DATE}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LD_LIBRARY_PATH=/opt/sqlite/lib \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    SHIXU_DATA_DIR=/var/lib/shixu SHIXU_SQLITE_PATH=/var/lib/shixu/business/app.sqlite3 \
    SHIXU_PUBLISHED_DIR=/var/lib/shixu/published SHIXU_WORK_DIR=/var/lib/shixu/work \
    SHIXU_RAW_DIR=/var/lib/shixu/raw SHIXU_CREDENTIAL_DIR=/var/lib/shixu/credentials \
    SHIXU_CREDENTIAL_MASTER_KEY_FILE=/run/secrets/shixu_master \
    PATH=/opt/venv/bin:$PATH PYTHONPATH=/app/backend
COPY --from=sqlite-builder /opt/sqlite/lib/libsqlite3.so* /opt/sqlite/lib/
COPY --from=uv /uv /usr/local/bin/uv
RUN groupadd --gid 10001 shixu && useradd --uid 10001 --gid shixu --create-home shixu \
 && mkdir -p /var/lib/shixu/business /var/lib/shixu/published /var/lib/shixu/raw /var/lib/shixu/work /var/lib/shixu/credentials \
 && chown -R shixu:shixu /var/lib/shixu
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev \
 && /opt/venv/bin/python -c "import sqlite3; assert sqlite3.sqlite_version == '3.54.1', sqlite3.sqlite_version"
COPY backend/app ./app
COPY scripts/ /app/scripts/
COPY deploy/init_volumes.py /app/deploy/init_volumes.py
COPY deploy/create_secret.py /app/deploy/create_secret.py
COPY --from=frontend /frontend/dist /app/frontend/dist
USER 10001:10001
EXPOSE 8788
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8788","--workers","1"]
