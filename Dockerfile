# syntax=docker/dockerfile:1

# ─── 阶段 1：构建前端 ────────────────────────────────────────────
FROM node:22-alpine AS frontend

RUN corepack enable

WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


# ─── 阶段 2：准备 Python 包 ─────────────────────────────────────
FROM python:3.14-slim AS package

ARG RELEASE=false
ARG APP_VERSION

WORKDIR /build
COPY . ./
COPY --from=frontend /build/frontend/dist ./frontend/dist

# 本地模式使用源码；发布模式只从 PyPI 获取包。
# 本地版本顺序：APP_VERSION -> Git 自动判断 -> hatch-vcs fallback-version。
RUN if [ "$RELEASE" = "true" ]; then \
    if [ -n "$APP_VERSION" ]; then \
    pip download --no-deps --dest /wheels "farewell-rss==$APP_VERSION"; \
    else \
    pip download --no-deps --dest /wheels farewell-rss; \
    fi; \
    else \
    apt-get update && apt-get install -y --no-install-recommends git; \
    if [ -n "$APP_VERSION" ]; then \
    SETUPTOOLS_SCM_PRETEND_VERSION="$APP_VERSION" pip wheel . --no-deps --wheel-dir /wheels; \
    else \
    pip wheel . --no-deps --wheel-dir /wheels; \
    fi; \
    fi


# ─── 阶段 3：运行时 ──────────────────────────────────────────────
FROM python:3.14-slim

ENV FAREWELL_RSS_DATA_DIR=/data

COPY --from=package /wheels/ /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/farewell_rss-*.whl \
    && rm -rf /tmp/wheels

# 数据目录（可挂卷持久化）
RUN mkdir -p /data
VOLUME ["/data"]

# 构建期端口：同时作为镜像内默认监听端口（FAREWELL_RSS_PORT）和 EXPOSE 声明。
# 这样 docker run -p X:X 时容器内默认就监听 X，无需额外设环境变量；
# 运行时仍可用 -e FAREWELL_RSS_PORT=... 覆盖（ENV 优先级低于 -e）。
# 注意 EXPOSE 只是元数据/文档，实际对外映射靠 docker run -p 宿主:容器。
ARG APP_PORT=3000
ENV FAREWELL_RSS_PORT=${APP_PORT}
EXPOSE ${APP_PORT}

CMD ["farewell-rss"]
