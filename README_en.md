<a id="top"></a>

<div align="center">

# 🧭 HRBPilot

**An enterprise HR agent · One backend covering 5 high-frequency HR scenarios**

Hybrid RAG · Compliance guardrails · Quality evaluation · Token budget governance<br/>
Making AI in HR **affordable and safe to ship**

<p>
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/简体中文-DFE0E5?style=for-the-badge"></a>
  <a href="./README_en.md"><img alt="English" src="https://img.shields.io/badge/English-DBEDFA?style=for-the-badge"></a>
</p>

<p>
  <a href="https://github.com/kayon0209/hrbpilot/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/kayon0209/hrbpilot/ci.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI"></a>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=flat-square"></a>
  <img alt="Last commit" src="https://img.shields.io/github/last-commit/kayon0209/hrbpilot?style=flat-square&color=blue">
  <img alt="Code size" src="https://img.shields.io/github/languages/code-size/kayon0209/hrbpilot?style=flat-square">
</p>

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="Pydantic" src="https://img.shields.io/badge/Pydantic-v2-E92063?style=flat-square&logo=pydantic&logoColor=white">
  <img alt="PostgreSQL" src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql&logoColor=white">
  <img alt="Milvus" src="https://img.shields.io/badge/Milvus-2.5-00A1EA?style=flat-square&logo=milvus&logoColor=white">
  <img alt="Redis" src="https://img.shields.io/badge/Redis-7-FF4438?style=flat-square&logo=redis&logoColor=white">
  <img alt="Celery" src="https://img.shields.io/badge/Celery-37814A?style=flat-square&logo=celery&logoColor=white">
  <img alt="MinIO" src="https://img.shields.io/badge/MinIO-C72E49?style=flat-square&logo=minio&logoColor=white">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white">
</p>

<p>
  <img src="./assets/hrbpilot-process-board.png" alt="HRBPilot workflow board: business material passes through sensitive-data protection, cited retrieval, and AI synthesis before human HR approval and controlled execution with an auditable, recoverable trail; exceptions are routed to human review." width="100%">
</p>

<sub>Evidence first, never deciding for people: the system handles high-volume organization while judgement, approval, and accountability remain human.</sub>

</div>

---

## 📖 Table of Contents

- [Why HRBPilot](#-why-hrbpilot-and-not-just-another-hr-qa-bot)
- [Product highlights](#-product-highlights)
- [HR scenarios covered](#-hr-scenarios-covered)
- [HR Case Agent: governed execution and notifications](#-hr-case-agent-governed-execution-and-notifications)
- [Architecture](#-architecture)
- [Evaluation results](#-evaluation-results-real-llm-run)
- [Quick start](#-quick-start)
- [Knowledge base and indexing](#-knowledge-base-and-indexing)
- [Stack and layout](#-stack-and-project-layout)
- [Testing](#-testing)
- [Known limitations](#-known-limitations)

---

## 🎯 Why HRBPilot, and not just another HR QA bot

The real pain in HR scenarios is **risk and cost**, not whether a model can produce an answer. One non-compliant reply, or one over-budget call, costs far more than an answer that is merely less clever.

|  |  |  |
| :-- | :-- | :-- |
| 🛡️ **Guardrails as a first-class citizen** | 💰 **Cost you can account for** | 🔍 **RAG that never fakes it** |
| Raw input passes guardrails before any model call: injection detection precedes query rewriting, PII is masked, rewrites are re-checked, and observability logs retain only lengths and hashes. On the golden set: **5/5 injections blocked, 0.0 false-positive rate** | Every call counts against a per-tenant monthly token budget (10M by default), with **75% warning / 90% critical alerts** | Original and rewritten queries both run dense + sparse retrieval and are fused through RRF, with **no mock fallback**. If an external service is down it raises an explicit infrastructure error instead of pretending retrieval succeeded |

> [!NOTE]
> Every answer is required to carry citations. When no supporting evidence is retrieved, the pipeline takes the `no_evidence_fallback` path and explicitly declines rather than fabricating an answer.

![HRBPilot product highlights: policy answers include citations and page numbers; interview notes, employee voice, and weekly reports are organized first; sensitive input is protected before model use, and consequential actions need human approval with an auditable, recoverable record.](./assets/hrbpilot-feature-overview.png)

---

## ✨ Product highlights

HRBPilot is not a generic chat model wrapped in an HR interface. It is built around three boundaries that match real HR work:

- **Policy answers need evidence.** Policy Q&A retrieves from the current tenant's enabled knowledge bases, returns cited sources, and says so clearly when evidence is insufficient instead of fabricating an answer.
- **Routine work should need less manual consolidation.** Interview digests, employee voice, weekly reports, and culture content are structured or drafted first so HR can spend time on judgement and conversation.
- **People remain accountable for actions.** Write operations such as case creation, assignment, and status updates require human approval. Execution, notifications, and outcomes remain inspectable.

---

## 💼 HR scenarios covered

Each scenario is an independent `orchestrator` + `config` + `prompts` + `schemas`, so they evolve without interfering with each other.

| Scenario | Module | What it does |
| :--- | :--- | :--- |
| 📋 **Policy Q&A** | `policy_qa` | Knowledge-base-grounded policy consultation, with query rewriting as preprocessing and citation validation as postprocessing |
| 🗣️ **Interview digest** | `interview_digest` | Structured summaries of performance, exit and onboarding interview records |
| 🎧 **Voice insight** | `voice_insight` | Insight extraction from voice and meeting content |
| 📅 **Weekly report** | `weekly_report` | Automated HR weekly report generation |
| 🎨 **Culture content** | `culture_content` | Generation of corporate-culture content |

---

## 🤖 HR Case Agent: governed execution and notifications

Alongside the five analytical scenarios, HRBPilot provides a deliberately bounded HR Case Agent. An employee or HR user can raise a case; the system identifies risk, gathers policy evidence, and proposes a plan. Any write action then requires human approval and is executed by an independent worker with an audit trail.

| Capability | Behaviour and boundary |
| :--- | :--- |
| **Governed writes** | `create_hr_case`, `assign_case_owner`, `update_case_status`, and `create_work_task` require an approved, unexpired, parameter-hash-matched, unconsumed approval. `/execute` only accepts the request atomically and returns `202`; it never performs an external side effect inside the HTTP transaction. |
| **Durable dispatch** | ToolExecution, an exact ExecutionGrant, and a `tool.dispatch` Outbox message are persisted in one transaction. The worker uses lease/fencing and a stable `request_id`; deterministic failures can retry or enter the DLQ, while an uncertain outcome becomes `UNKNOWN` and must not be retried blindly. |
| **Traceability** | `GET /api/v1/hr-cases/{id}/runs/{run_id}` exposes the plan, tool execution, approval, and event trail. The case state machine only permits `NEW → TRIAGED → EVIDENCE_READY → PLAN_READY → AWAITING_APPROVAL → EXECUTING → RESOLVED/FAILED`. |
| **Approval authority** | Only `hr_manager` can decide an approval. Platform admins hold no HR business capability, so route, service, and navigation semantics stay aligned. |
| **Policy-Q&A protections** | Policy Q&A keeps the newest conversation turns under a token budget, carries retrieved evidence in an explicit untrusted user-role block rather than a system message, and creates an immutable request-level model plan. Each provider uses its own model name in the fallback chain; streaming output is displayable only after output guardrails and the no-evidence fallback complete. |
| **In-app notifications** | `GET /api/notifications` and the read endpoint expose only the current recipient's notification metadata. A notification ID belonging to another recipient returns 404; case content remains behind the case ACL. |

Governed writes require an independent Outbox Worker. Docker Compose starts it already; for local or other deployments, start:

```bash
python -m app.outbox.worker
```

Use `python -m app.outbox.worker --once --max-messages 100` for a bounded operational drain. The [runbook](./docs/upgrade/HR_CASE_AGENT_RUNBOOK.md) contains API examples, DLQ replay, and `UNKNOWN` reconciliation; the [ADR](./docs/upgrade/ADR-0001-single-bounded-agent.md) explains the design trade-offs.

---

## 🏗 Architecture

```mermaid
flowchart TB
    Client(["🧑‍💼 HR / employee / internal system"]) --> Gateway
    Gateway["🔐 API governance<br/>Rate limit · Auth · RBAC · Tenant context · Audit"] --> Scenarios
    Gateway --> CaseAgent

    subgraph Knowledge["📚 Safe ingestion and incremental indexing"]
        Upload["Upload"] --> UploadGuard["🛡️ Type / magic bytes / path / PDF structural checks"]
        UploadGuard --> Source["MinIO source + PostgreSQL metadata"]
        Source --> IngestQueue["kb_ingest queue"]
        IngestQueue --> IngestWorker["Celery Worker<br/>parse · cross-page stitch · chunk"]
        IngestWorker --> Index["Milvus vectors + PostgreSQL full text"]
    end

    subgraph Answer["🔎 Evidence answers and reliable fallback"]
        Scenarios["🎯 5 Scenario Orchestrators<br/>Policy QA · interview · voice · weekly · culture"] --> InGuard["🛡️ Input guardrails<br/>injection block · PII masking"]
        InGuard --> Variants["Original + rewritten query"]
        Variants --> Dense["Milvus dense"]
        Variants --> Sparse["PostgreSQL sparse"]
        Dense --> Fusion["RRF fusion + page evidence"]
        Sparse --> Fusion
        Fusion --> Router["🤖 Provider Router<br/>request-level models + fallback"]
        Router --> OutGuard["🛡️ Output guardrails + citation checks"]
        OutGuard --> Response(["✅ Displayable answer + citations"])
    end

    subgraph Execution["🧭 Governed execution and recovery"]
        CaseAgent["HR Case Agent<br/>risk · evidence · plan"] --> Approval["👤 hr_manager approval"]
        Approval --> Outbox["ToolExecution + Outbox<br/>one transaction"]
        Outbox --> OutboxWorker["Independent Worker<br/>lease / fencing / retry"]
        OutboxWorker --> Effect["Controlled side effect"]
        OutboxWorker -. UNKNOWN / failed .-> Ops["DLQ + reconciliation<br/>replay / discard / human decision"]
    end

    Index --> Fusion
    Router -. observed calls .-> Ready["📡 /api/ready<br/>LLM degradation visible"]
    OutGuard -. async .-> Governance["💰 Token budget · 📊 evaluation · 📝 audit"]
    Ops -. recovery drill .-> Backup["🗄️ PostgreSQL backup / restore drill"]

    classDef guard fill:#fef3c7,stroke:#f59e0b,stroke-width:2px,color:#78350f
    classDef core fill:#dbeafe,stroke:#3b82f6,stroke-width:2px,color:#1e3a8a
    classDef ops fill:#dcfce7,stroke:#22c55e,stroke-width:2px,color:#14532d
    class UploadGuard,InGuard,OutGuard guard
    class Scenarios,Router core
    class Approval,Outbox,OutboxWorker,Ops,Backup ops
```

**Infrastructure layer:** PostgreSQL (Alembic migrations + row-level security) · Redis broker / backpressure · Celery queues · MinIO · Milvus

<details>
<summary>📐 View the static architecture diagram (SVG)</summary>

![HRBPilot architecture](./assets/architecture.svg)

</details>

---

## 📊 Evaluation results (real LLM run)

On 2026-08-28 the full pipeline was run against a **250-sample golden set** (50 per scenario, including 5 injection-refusal cases) using a real LLM (Gitee AI `qwen3.8-flash`). Composition: `policy_qa` and `interview_digest` account for **100 hand-written samples**; `voice_insight`, `weekly_report`, and `culture_content` are **150 deterministically expanded template samples** — the two groups must not be presented as a single data-quality figure.

The externally claimable artifact is `evaluation/results/golden_eval_20260828T215711Z.json`: `for_external_claims: true`, all 250 samples completed, and zero errors. Its repair history discloses two transient-error repairs and three partial reruns after a guardrail fix.

<table>
<tr>
<td width="50%" valign="top">

**Guardrails and cost**

| Metric | Result |
| :--- | :--- |
| Guardrail overall | **1.0** |
| Injection recall | **1.0** (5/5 blocked) |
| False-positive rate | **0.0** |
| Total tokens | 554,387 (99.9% real billing) |
| Budget consumed | **5.54%** of the 10M monthly budget |
| Implied capacity | ~**18 calls / tenant / month** |

</td>
<td width="50%" valign="top">

**Per-scenario quality**

| Scenario | Keyword hit | Citation coverage |
| :--- | :---: | :---: |
| `policy_qa` | 0.908 | 0.9 |
| `interview_digest` | 0.79 | **1.0** |
| `voice_insight` | 0.91 | **1.0** |
| `weekly_report` | 0.536 | **1.0** |
| `culture_content` | 0.104 | **1.0** |

</td>
</tr>
</table>

> [!TIP]
> Full run artifacts live under `app/evaluation/` and `evaluation/results/`. These numbers come from an actual offline `run_golden_eval.py` run, not estimates.

---

## 🚀 Quick start

### Option 1: Docker Compose (recommended, full Hybrid RAG)

RAG depends on four external services: PostgreSQL, Milvus, MinIO and Redis. Bring everything up at once:

```bash
cp env.docker.example env.docker    # 1. copy the env template
#  2. fill in env.docker: JWT_SECRET (>=32 chars), EMBEDDING_API_KEY,
#     LLM_API_KEY, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
docker compose up --build           # 3. start
```

> [!IMPORTANT]
> In production mode (`APP_ENV=production`) the app **refuses to start** on the default JWT secret. Always set a `JWT_SECRET` of at least 32 characters.

<details>
<summary>🔧 Startup order and automatic behaviour</summary>

`docker compose` orchestrates dependencies with healthchecks:

1. PostgreSQL becomes ready
2. The app runs `alembic upgrade head`
3. Milvus / MinIO / Redis become ready
4. FastAPI (uvicorn) starts
5. The Celery Worker consumes kb_ingest and scenario queues; the Outbox Worker handles approved governed writes

On startup the app ensures the Milvus collection (its dimension must match `EMBEDDING_DIMENSION`) and the MinIO bucket exist. The PostgreSQL application account is a **non-superuser**, so row-level security cannot be bypassed through the connection account.

</details>

### Option 2: Local development (lightweight, no vector retrieval)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                # fill in as needed
uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive API documentation.

---

## 📚 Knowledge base and indexing

**Supported document formats**

| | Formats |
| :--- | :--- |
| ✅ Supported | `txt` · `pdf` · `docx` |
| ❌ Explicitly unsupported | `doc` · `xls` · `ppt` (uploads are rejected rather than silently skipped) |

<details>
<summary>⚙️ Indexing and query pipeline in detail</summary>

**Indexing (write path)**

```
Upload → safety gate (type / magic bytes / path / PDF structure)
       → MinIO → documents (PostgreSQL, status=uploaded)
       → atomically claimed and dispatched as a Redis/Celery task
       → parse (cross-page stitching, DOCX table order, OCR page diagnostics) → chunk → jieba tokenization → embedding
       → document_chunks (PostgreSQL) + Milvus upsert
       → marked indexed
```

A failed rebuild **keeps the previous usable vectors**; old and new versions are cleaned up precisely by chunk id.

**Querying (read path)**

The original query and a rewritten query (only when it actually changed) both run PostgreSQL keyword retrieval (`plainto_tsquery('simple', jieba_query)`) and Milvus dense retrieval (scalar filtering on `tenant_id` + `kb_id`), then fuse through RRF. A rewrite therefore adds recall candidates instead of replacing and losing the original intent; page evidence stays attached for LLM citations.

Policy QA only accepts real knowledge bases that are **enabled** for the current tenant and carry `scenario_id=policy_qa`.

</details>

---

## 🧰 Stack and project layout

FastAPI · Uvicorn · Pydantic v2 · SQLAlchemy (async) + AsyncPG · Alembic · Redis · Celery · MinIO · Milvus · jieba · structlog · PyYAML

<details>
<summary>📂 Expand project layout</summary>

```
hrbpilot/
├── app/
│   ├── access/        # middleware chain + routes (health / auth / scenarios / eval / kb / settings)
│   ├── config/        # configuration (pydantic-settings)
│   ├── data/          # database / models / repositories / alembic migrations
│   ├── evaluation/    # metric aggregation + golden dataset
│   ├── guardrails/    # compliance / input / output guardrails + rate limiting
│   ├── rag/           # ingestion / knowledge_base / llm / retrieval / pipeline
│   ├── scenarios/     # 5 HR scenarios (each with orchestrator + config + prompts + schemas)
│   └── shared/        # logging / errors / audit / cache / token_budget / graceful shutdown
├── evaluation/        # offline golden evaluation scripts and results
├── interview_samples/ # interview record samples (.txt)
├── kb_docs/           # raw HR knowledge base documents (PDF / DOCX / TXT)
├── tests/             # pytest (unit + integration + security regression)
└── alembic.ini · Dockerfile · docker-compose.yml · pyproject.toml · .env.example
```

</details>

---

## ✅ Testing

```bash
pytest                              # everything
pytest -m "not integration"         # skip tests that need live PostgreSQL / Milvus
```

CI runs `ruff check` · `ruff format --check` · `mypy` · `pytest` on every push and pull request.

### Reproducible production baseline

Each milestone can run `scripts/freeze_production_baseline.py` to freeze a **non-hand-editable** verification receipt: commit SHA, dependency-lock hashes, timestamp, and measured backend and web checks are bound together with a content hash. `--verify` detects manual edits. It is evidence for one reproducible run, not a permanent green badge; code, dependency, or environment changes require a new freeze.

---

## ⚠️ Known limitations

- The keyword-hit score for `weekly_report` (0.536) is lower than the other structured scenarios because free-form weekly-report text is hard to measure with keyword matching. The evaluation approach is being reworked.
- The keyword-hit score for `culture_content` (0.104) is not a useful quality proxy for authored prose; citation coverage remains 1.0, and metric applicability is now tracked per scenario.
- Policy-QA citation coverage is 0.9 in the REAL-LLM run. The structured offline citation gate reports source recall 0.9333 and source precision 1.0; the measures are intentionally not conflated.
- HR Case Agent quality gates are still offline and deterministic; no REAL-LLM end-to-end metric is claimed. `send_case_notification` has no verifiable external provider yet, so its dispatch enters the DLQ rather than falsely reporting delivery.

---

## 📄 License

Released under the [MIT License](./LICENSE).

<div align="right"><a href="#top">⬆ Back to top</a></div>
