# CQR Sandbox Container — base image for project execution environments
# Pre-baked with Node 20, Python 3.11, Go 1.21, and CQR runtime utilities

FROM ubuntu:22.04

LABEL maintainer="CQR Team"
LABEL description="CQR sandboxed execution environment"

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    jq \
    htop \
    ca-certificates \
    gnupg \
    lsb-release \
    build-essential \
    patch \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python 3.11
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.11 /usr/bin/python

# ---------------------------------------------------------------------------
# Node.js 20
# ---------------------------------------------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g yarn \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Go 1.21
# ---------------------------------------------------------------------------
RUN wget -q https://go.dev/dl/go1.21.13.linux-amd64.tar.gz \
    && tar -C /usr/local -xzf go1.21.13.linux-amd64.tar.gz \
    && rm go1.21.13.linux-amd64.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"

# ---------------------------------------------------------------------------
# Playwright (for agent browser testing)
# ---------------------------------------------------------------------------
RUN pip3 install playwright && playwright install --with-deps chromium

# ---------------------------------------------------------------------------
# CQR runtime (read-only bind mount in production)
# ---------------------------------------------------------------------------
RUN mkdir -p /cqr /workspace /tmp/agent-scratch

COPY inject-env.sh /cqr/inject-env.sh
COPY agent-entrypoint.py /cqr/agent-entrypoint.py
RUN chmod +x /cqr/inject-env.sh

# ---------------------------------------------------------------------------
# Git identity placeholder (overridden per project at runtime)
# ---------------------------------------------------------------------------
RUN git config --global user.email "agent@cqr.dev" \
    && git config --global user.name "CQR Agent" \
    && git config --global init.defaultBranch main

WORKDIR /workspace

CMD ["/bin/bash"]
