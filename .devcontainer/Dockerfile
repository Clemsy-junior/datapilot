# syntax=docker/dockerfile:1

# Development image: Python 3.12 *and* Node 20 in one place, so a contributor
# needs neither on their machine. This image is only ever used for development —
# production images are `backend/Dockerfile` and `frontend/Dockerfile`, which
# are deliberately much smaller and contain none of these tools.

FROM python:3.12-slim-bookworm

ARG NODE_MAJOR=20
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gnupg \
        less \
        make \
        procps \
        ssh \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Node from NodeSource: the Debian package is several major versions behind.
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Non-root user: the workspace is bind-mounted from the host, and files created
# in the container must stay editable outside it.
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID --create-home --shell /bin/bash $USERNAME \
    && echo "$USERNAME ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# Tooling that must exist before the first `postCreateCommand` runs.
RUN pip install --no-cache-dir "ruff>=0.6" "uvicorn[standard]>=0.30"

USER $USERNAME
WORKDIR /workspace

ENV PATH="/home/${USERNAME}/.local/bin:${PATH}"
