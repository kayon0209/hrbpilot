FROM python:3.12-slim

WORKDIR /app

# Install full runtime dependencies from pyproject.toml
# (SQLAlchemy, asyncpg, alembic, minio, pymilvus, jieba, python-docx, pypdf, ...)
COPY . .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 5 .

# The API and Celery worker do not need root privileges at runtime.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
