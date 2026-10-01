# syntax=docker/dockerfile:1

# ─── 阶段 1：构建前端 ────────────────────────────────────────────
# 前端产物是纯静态文件，只在 BuildKit 的构建机架构上构建一次。
# 否则多架构构建会在 QEMU 下重复运行 Node/pnpm，ARM64 容易触发非法指令。
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend

RUN corepack enable && corepack install --global pnpm@10

WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build


# ─── 阶段 2：准备 Python 包 ─────────────────────────────────────
FROM python:3.14-slim AS package

ARG RELEASE=false

WORKDIR /build
COPY . ./
COPY --from=frontend /build/frontend/dist ./frontend/dist

# 本地模式使用源码；发布模式安装 workflow 传入的 wheel。
# 发布 workflow 会将 uv build 生成的 wheel 下载到构建上下文的 dist/。
# 发布模式只取「最新的那个」wheel：dist/ 里若留着旧版本的 wheel，阶段 3 那句
# farewell_rss-*.whl 会一次装上两个版本，pip 直接 ResolutionImpossible
# （实测 0.1.10.dev14 + 8 月的 0.1.6 就是这个错）。
RUN if [ "$RELEASE" = "true" ]; then \
    mkdir -p /wheels && cp "$(ls -t dist/*.whl | head -1)" /wheels/; \
    else \
    apt-get update && apt-get install -y --no-install-recommends git; \
    pip wheel . --no-deps --wheel-dir /wheels; \
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
