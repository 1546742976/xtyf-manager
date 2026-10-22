# Reproducible Linux browser E2E image; binaries live in GHCR, definitions live in Git.
FROM mcr.microsoft.com/playwright:v1.56.1-noble
COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /uvx /usr/local/bin/
LABEL org.opencontainers.image.source="https://github.com/SHM-white/xtyf-manager" \
      org.opencontainers.image.description="Prebuilt Python 3.13 + Node 22 + Playwright browser CI environment"
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    VIRTUAL_ENV=/opt/xtyf-python \
    NODE_PATH=/opt/xtyf-node/node_modules \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PATH="/opt/xtyf-python/bin:${PATH}"
COPY requirements.txt requirements-dev.txt /opt/xtyf-requirements/
RUN uv python install 3.13 \
    && uv venv --python 3.13 "$VIRTUAL_ENV" \
    && uv pip install --python "$VIRTUAL_ENV/bin/python" -r /opt/xtyf-requirements/requirements-dev.txt
RUN mkdir -p /opt/xtyf-node \
    && cd /opt/xtyf-node \
    && npm init --yes \
    && npm install --no-save --package-lock=false \
       playwright@1.56.1 bootstrap@5.3.3 bootstrap-icons@1.11.3
RUN python --version \
    && node --version \
    && python -c "import flask, coverage, reportlab" \
    && test -d /ms-playwright \
    && node -e "require('/opt/xtyf-node/node_modules/playwright')"
