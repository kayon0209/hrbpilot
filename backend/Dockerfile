FROM python:3.12-slim

WORKDIR /app

# Install dependencies directly
COPY pyproject.toml ./
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic pydantic-settings structlog pyyaml openai python-jose python-multipart passlib

# Copy all source
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
