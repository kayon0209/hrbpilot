@echo off
REM ============================================
REM  HRBPilot startup (Docker REQUIRED)
REM  Needs: Docker Desktop running + .venv ready
REM  First run on a fresh database, run once:
REM    .venv\Scripts\python.exe -m alembic upgrade head
REM  Demo accounts: e2e-hrbp-verify01@hrbpilot.test
REM  Password: Hrbpilot!Verify2026
REM ============================================
REM  Docker Desktop 默认不在 PATH。若下面 docker 命令调用不到，
REM  先设置环境变量 DOCKER_BIN=<Docker Desktop 的 resources\bin 目录>
if defined DOCKER_BIN set "PATH=%DOCKER_BIN%;%PATH%"
title HRBPilot Launcher
cd /d "%~dp0"

echo [1/4] Starting Docker infra (postgres/redis/milvus/minio/etcd) ...
docker compose up -d etcd minio postgres redis milvus

echo [2/4] Starting API on http://127.0.0.1:8001 ...
start "HRBP API" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001"

echo [3/4] Starting Celery worker (async tasks) ...
start "HRBP Celery" cmd /k ".venv\Scripts\python.exe -m celery -A app.shared.celery_app worker --loglevel=info --pool=solo --concurrency=1"

echo [4/4] Starting Web on http://127.0.0.1:5173 ...
cd web
start "HRBP Web" cmd /k "node --max-old-space-size=384 _serve.mjs"

echo.
echo ==========================================
echo  Web : http://127.0.0.1:5173
echo  API : http://127.0.0.1:8001
echo ==========================================
echo NOTE: web/dist must exist. If missing, build first:
echo   cd web ^&^& npx vite build
echo Close opened windows to stop. To free RAM when done:
echo   docker stop hrbpilot-milvus-1 hrbpilot-minio-1 hrbpilot-etcd-1
timeout /t 5 >nul
