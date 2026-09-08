# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the dependency set into a self-contained virtualenv.
# Compilers and build backends stay in this stage and never reach the runtime
# image, which is the whole point of splitting it in two.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the manifest alone first: as long as it does not change, Docker reuses
# the cached dependency layer even when application code has changed.
COPY pyproject.toml ./
COPY app/__init__.py ./app/__init__.py
RUN pip install .

# ---------------------------------------------------------------------------
# Stage 2 — runtime. No build tools, no package index, non-root.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATAPILOT_DATA_DIR=/app/data

# TODO(R12): the runtime image is ~250 MB, dominated by pandas and its NumPy
# dependency. Measure before/after when trimming (slim wheels, --no-compile,
# dropping test-only extras).
RUN useradd --create-home --uid 10001 datapilot

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=datapilot:datapilot app ./app
COPY --chown=datapilot:datapilot data ./data

USER datapilot

EXPOSE 8000

# The healthcheck uses the stdlib rather than curl: the slim image has no curl,
# and adding one just to poll a URL would grow the image for nothing.
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
