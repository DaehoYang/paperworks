# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ARG CODEX_CLI_VERSION=0.141.0
ARG INSTALL_CODEX_CLI=true

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PAPERWORKS_BUILD_FRONTEND=auto \
    PATH=/opt/paperworks-frontend/node_modules/.bin:$PATH

WORKDIR /workspace

# Runtime tools:
# - poppler-utils: pdftotext, pdftoppm, pdfinfo, pdfseparate, pdfunite
# - nodejs/npm: React/Vite frontend build
# - fonts-noto-cjk: Korean text rendering in Chromium/PDF generation
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        poppler-utils \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/documents/requirements.txt /tmp/paperworks-documents-requirements.txt
COPY scripts/gui/requirements.txt /tmp/paperworks-gui-requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install \
        -r /tmp/paperworks-documents-requirements.txt \
        -r /tmp/paperworks-gui-requirements.txt \
        beautifulsoup4 \
        fastapi \
        openpyxl \
        pandas \
        pillow \
        pydantic \
        pypdf \
        python-multipart \
        pyyaml \
        "uvicorn[standard]" \
    && python -m playwright install --with-deps chromium \
    && CHROME_PATH="$(find /ms-playwright -path '*/chrome-linux*/chrome' -type f | sort | tail -n 1)" \
    && test -n "$CHROME_PATH" \
    && ln -sf "$CHROME_PATH" /usr/bin/google-chrome \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/paperworks-frontend
COPY scripts/gui/frontend/package.json scripts/gui/frontend/package-lock.json ./
RUN npm ci \
    && npm cache clean --force

# The OCR fallback path calls the `codex` CLI. Authentication still needs to be
# supplied at runtime, for example by mounting ~/.codex or setting an API key.
RUN if [ "$INSTALL_CODEX_CLI" = "true" ]; then \
        npm install -g "@openai/codex@${CODEX_CLI_VERSION}"; \
    fi \
    && npm cache clean --force

RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -e' \
    'FRONTEND=/workspace/scripts/gui/frontend' \
    'if [ -d "$FRONTEND" ] && [ ! -e "$FRONTEND/node_modules" ]; then' \
    '  ln -s /opt/paperworks-frontend/node_modules "$FRONTEND/node_modules" 2>/dev/null || true' \
    'fi' \
    'case " $* " in' \
    '  *"scripts/gui/run_react.py"*|*"scripts.gui.react_backend.app:app"*)' \
    '    if [ -d "$FRONTEND" ]; then' \
    '      if [ "${PAPERWORKS_BUILD_FRONTEND:-auto}" = "1" ] || { [ "${PAPERWORKS_BUILD_FRONTEND:-auto}" = "auto" ] && [ ! -f "$FRONTEND/dist/index.html" ]; }; then' \
    '        (cd "$FRONTEND" && npm run build)' \
    '      fi' \
    '    fi' \
    '    ;;' \
    'esac' \
    'cd /workspace' \
    'exec "$@"' \
    > /usr/local/bin/paperworks-entrypoint \
    && chmod +x /usr/local/bin/paperworks-entrypoint

WORKDIR /workspace
ENV PYTHONPATH=/workspace
EXPOSE 45001 8501

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/paperworks-entrypoint"]
CMD ["python", "scripts/gui/run_react.py", "--address", "0.0.0.0", "--port", "45001"]
