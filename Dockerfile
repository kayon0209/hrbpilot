FROM python:3.12-slim AS runtime

WORKDIR /app

# Production images contain only runtime dependencies and application files.
COPY pyproject.toml README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 5 .

COPY alembic.ini ./
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test

USER root
COPY tests ./tests
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 5 ".[dev]"
USER appuser
CMD ["pytest"]
