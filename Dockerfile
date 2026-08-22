# syntax=docker/dockerfile:1

# Docker 镜像使用已发布到 PyPI 的包，避免在镜像构建时依赖源码或 Git 元数据。
FROM python:3.14-slim

# 默认环境变量（只设数据目录，其余用代码里的默认值）
ENV FAREWELL_RSS_DATA_DIR=/data

# 发布 workflow 传入 release tag 对应的版本；手动构建时默认安装 PyPI 最新版。
ARG APP_VERSION
RUN if [ -n "$APP_VERSION" ]; then \
    pip install --no-cache-dir "farewell-rss==$APP_VERSION"; \
    else \
    pip install --no-cache-dir farewell-rss; \
    fi

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
