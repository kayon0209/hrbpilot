# HRBPilot portable migration snapshot

Created: 2026-09-10
Source Git state at capture: branch main, commit 250d002, with uncommitted work intentionally represented below.

## Included

- 797 tracked project files, excluding 6 tracked evaluation-result artifacts and the two original environment examples.
- 14 modified tracked product files, included at their working-tree version.
- 8 explicit untracked product files: material-batch API/service/migration, HR Case read executors/context and test, and the shared interview/voice batch UI panel.
- Repository and web workspace instructions: AGENTS.md and web/AGENTS.md.
- Sanitized .env.example and nv.docker.example: keys retained, all original values removed.

## Deliberately excluded

- Git history and .git.
- Every .env file and the original example-template values.
- Dependency/virtual-environment/cache/build/test-report directories; agent/editor directories; logs; local runtime files.
- 6 tracked files under valuation/results/.
- Untracked diagnostic/rollback/seed/simulation scripts, verification JSON/screenshots, browser flow/audit scripts, and start-local.bat.

## New-computer setup

1. Extract this directory. Copy .env.example to .env (or nv.docker.example to nv.docker for Docker) and set real values locally; do not commit either file.
2. Backend local development: python -m venv .venv, activate it, then pip install -e ".[dev]".
3. Frontend: install Node.js 22 with Corepack, then run corepack pnpm --dir web install and corepack pnpm --dir web dev.
4. Full local stack: after configuring nv.docker, run docker compose up --build. It applies Alembic migrations; review the migration set before using any non-disposable database.
5. Run project checks from the README after dependencies and required local services are configured. This snapshot itself was structurally validated, not claimed release-ready.