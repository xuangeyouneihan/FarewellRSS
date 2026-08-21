# syntax=docker/dockerfile:1

# ─── 阶段 1：构建前端 ────────────────────────────────────────────
FROM node:22-alpine AS frontend

# corepack 启用 pnpm
RUN corepack enable

WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build
# 产物在 /build/frontend/dist


# ─── 阶段 2：运行时 ──────────────────────────────────────────────
FROM python:3.14-slim

# 默认环境变量（只设数据目录，其余用代码里的默认值）
ENV FAREWELL_RSS_DATA_DIR=/data

# 版本由 git tag 决定：hatch-vcs 在容器内跑 git describe 读 tag，需要 git 二进制 + .git
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 代码上下文 = 仓库根（.dockerignore 控制进什么）
WORKDIR /app

# 后端依赖 + 源码 + git 元数据（hatch-vcs 读 tag 用）
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY .git ./.git
# 前端构建产物（从阶段 1）
COPY --from=frontend /build/frontend/dist ./frontend/dist

# 安装 farewell-rss（hatch force-include 会把 frontend/dist 打进包）
# git 仓库所有权与容器 root 不同，需标记 safe.directory
RUN git config --global --add safe.directory /app && pip install --no-cache-dir .

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
