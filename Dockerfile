# Web Watcher — production Docker image
# Builds a minimal, non-root runtime with only production dependencies.

FROM python:3.11-slim AS base

# ---------- build arguments ----------
ARG PYTHONUNBUFFERED=1
ARG PIP_NO_CACHE_DIR=1
ARG APP_USER=appuser
ARG APP_UID=1000
ARG APP_GID=1000

# ---------- system prep ----------
RUN groupadd -g ${APP_GID} ${APP_USER} \
    && useradd -m -u ${APP_UID} -g ${APP_GID} -s /bin/sh ${APP_USER}

# ---------- install production dependencies ----------
# Use pyproject.toml so install setuptools/wheel metadata tools,
# then install the project in editable mode with production deps only.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --no-deps -e . \
    && pip install --no-cache-dir requests pyyaml

# ---------- application code ----------
COPY src/ ./src/
COPY config/ ./config/

# ---------- runtime directories ----------
# Container filesystem is ephemeral; /data and /logs must be provided
# by the orchestrator as named volumes or binds.
RUN mkdir -p /data /logs \
    && chown -R ${APP_UID}:${APP_GID} /data /logs /src /config

WORKDIR /app

# ---------- entrypoint ----------
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && chown ${APP_UID}:${APP_GID} /entrypoint.sh

# ---------- metadata ----------
ENV PYTHONUNBUFFERED=${PYTHONUNBUFFERED}
ENV PIP_NO_CACHE_DIR=${PIP_NO_CACHE_DIR}

USER ${APP_UID}

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "web_watcher.docker_run"]
