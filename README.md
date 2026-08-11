# AesthFlow

A modular multi-agent orchestration backend. A **supervisor agent** receives a task, decides which **specialist agents** are needed, delegates to them, and merges their results — with every step fully logged and replayable.

Built with LangGraph, FastAPI, PostgreSQL, and Redis. Backend-only for now; UI comes later.

For the architectural rationale behind each layer, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Table of contents

1. [Tech stack](#tech-stack)
2. [Developer setup](#developer-setup)
3. [Branching strategy](#branching-strategy)
4. [Phases](#phases)
5. [Task distribution](#task-distribution)
6. [Working conventions](#working-conventions)
7. [Project layout](#project-layout)
8. [Design principles](#design-principles)
9. [Team](#team)
10. [License](#license)

---

## Tech stack

- **Python 3.12+** (managed with [uv](https://docs.astral.sh/uv/))
- **FastAPI** + **Uvicorn** (API layer, Phase 4)
- **LangGraph** + **LangChain** (agent graphs)
- **PostgreSQL 16** (persistence — Docker)
- **Redis 7** (event bus — Docker)
- **Docker + Docker Compose**
- **Google Gemini API key** (AI Studio, free tier) + **Tavily API key** (web search)

> **Windows note:** the local Postgres container is published on port `5434` instead of the default `5432` to avoid conflicts with a native Windows PostgreSQL service. If you still see password-auth failures, run `netstat -ano | findstr 543` and stop any local `postgres.exe` that owns the port.

---

## Developer setup

This project uses **uv** for dependency and environment management. You can use pip if you prefer, but the lockfile and CI will be uv-based.

### 1. Clone and create the virtual environment

```bash
git clone <your-repo-url>
cd aesthflow

uv venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install the project

```bash
uv pip install -e ".[dev]"
```

This installs the package in editable mode. The importable package is `orchestrator` (not `src.orchestrator`), matching `pyproject.toml`.

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```dotenv
GOOGLE_API_KEY=AIza-...
TAVILY_API_KEY=tvly-...
```

The database URLs in `.env.example` already point to the Docker Postgres on port `5434`:

```dotenv
DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator@localhost:5434/orchestrator
DATABASE_URL_SYNC=postgresql+psycopg2://orchestrator:orchestrator@localhost:5434/orchestrator
```

### 4. Start infrastructure

```bash
docker-compose up -d
```

This starts:

- `orchestrator-postgres` on `localhost:5434`
- `orchestrator-redis` on `localhost:6379`

### 5. Run migrations

Migrations live in `alembic/versions/` and are committed to the repo. Apply the latest:

```bash
alembic upgrade head
```

If you change `persistence/models.py`, generate a new migration with:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

---

## Branching strategy

The repo uses a lightweight trunk-based flow:

| Branch | Purpose | Lifespan |
|--------|---------|----------|
| `main` | Always deployable; whole-phase checkpoints pass here | permanent |
| `dev` | Integration branch for the current phase | permanent |
| `phase{N}-{short-task}` | One branch per concrete task | temporary |

### Workflow

1. **Start from `dev`** (or `main` if `dev` does not yet exist):

   ```bash
   git checkout dev
   git pull
   git checkout -b phase1-research-analyst-agent
   ```

2. **Work on the task** and make sure its checkpoint passes locally.

3. **Rebase if `dev` has moved forward**:

   ```bash
   git checkout dev
   git pull
   git checkout phase1-research-analyst-agent
   git rebase dev
   ```

4. **Open a PR back to `dev`** when the task is ready.

5. **Merge to `main` only after the whole phase checkpoint passes in `dev`.** Do not merge individual task branches straight to `main`.

### Naming examples

- `phase0-verify-event-persistence`
- `phase1-research-analyst-agent`
- `phase1-llm-reasoner-node`
- `phase2-sandbox-executor`
- `phase2-code-reviewer-agent`
- `phase3-supervisor-routing`
- `phase3-graph-builder`
- `phase4-runs-api`
- `phase4-agents-api`

---

## Phases

The build is sequenced deliberately: infrastructure before agents, agents before orchestration, orchestration before the API. Each phase has a concrete checkpoint that must pass before the next phase starts.

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5
Foundations  First node    Sandbox +     Supervisor    API layer   Hardening
             + first agent  remaining    + graph
                            agents
```

| Phase | What it builds | Why it exists | Concrete checkpoint | Depends on |
|-------|----------------|---------------|---------------------|------------|
| **0 — Foundations** | Config, `AgentState` schema, `Event` schema, Postgres models (`Run`, `EventRow`, `WorkflowDefinition`), dual-write event publisher | You cannot debug agents without observable, persisted execution | `python scripts/verify_phase0.py` passes — an event round-trips through Postgres and Redis | — |
| **1 — First node + agent** | `BaseNode`, `LLMReasonerNode`, `WebSearchNode`, Research Analyst agent | Prove one agent works end-to-end before adding complexity | Running Research Analyst standalone produces a full, correctly ordered event trail in Postgres | Phase 0 |
| **2 — Sandbox + remaining agents** | Docker sandbox executor, `PythonSandboxNode`, Code Reviewer, Data Analyst, Fact Checker | Isolated code execution is a security boundary; specialist agents share a reusable node library | Each specialist runs standalone with correct event trails; sandbox survives adversarial tests | Phase 1 |
| **3 — Orchestrator** | `supervisor.py` routing logic, `graph_builder.py` full graph assembly | Multi-agent routing is the core product behavior | A multi-part task triggers more than one specialist in sequence, with merged final answer | Phase 2 |
| **4 — API layer** | FastAPI routes: submit task, poll status, retrieve events, workflow CRUD, agent registry | Make the backend usable over HTTP | A full multi-agent run can be triggered, polled, and inspected entirely over HTTP | Phase 3 |
| **5 — Hardening** | Retries, structured logging, concurrent sandbox load tests, full integration suite | Move from demo to production-capable | System survives concurrent runs; full integration test suite passes | Phase 4 |

### Current status

| Phase | What it delivers | Status |
|---|---|---|
| 0 | Config, shared state schema, event schema, DB models, event publisher | ✅ Scaffolded |
| 1 | First node types + Research Analyst agent, proven via CLI | ⬜ Not started |
| 2 | Sandbox executor + remaining specialist agents | ⬜ Not started |
| 3 | Supervisor + orchestrator graph | ⬜ Not started |
| 4 | FastAPI layer | ⬜ Not started |
| 5 | Hardening (retries, load testing, adversarial sandbox tests) | ⬜ Not started |

---

## Task distribution

Work is split along the architecture's existing seam: **infrastructure-facing layers** (sandbox, persistence, graph plumbing) and **agent-facing layers** (agent composition, routing logic, API surface). This minimizes merge conflicts because each owner mostly touches different directories.

### Phase ownership

| Phase | Harry | Jay |
|---|---|---|
| 0 — Foundations | Config, state/event schemas, DB models, event publisher | Review + get local env running |
| 1 — First node + agent | `BaseNode`, `LLMReasonerNode`, `WebSearchNode` | Research Analyst agent + CLI checkpoint script |
| 2 — Sandbox + agents | Sandbox executor, resource policies, Data Analyst, Fact Checker | Code Reviewer agent |
| 3 — Orchestrator | `graph_builder.py` — assemble and wire the `StateGraph` | `supervisor.py` — routing logic and prompt design |
| 4 — API layer | `routes/runs.py`, `routes/workflows.py` | `routes/agents.py`, request validation, budget-cap enforcement |
| 5 — Hardening | Sandbox adversarial + concurrent load testing | Retries, structured logging, integration test suite |

### Module-level ownership

| Layer / module | Primary owner | Directories / files | Notes |
|----------------|---------------|---------------------|-------|
| Config | Harry | `src/orchestrator/config.py` | Pydantic settings; changes affect everyone |
| Core contracts | Harry | `src/orchestrator/core/` | `state.py`, `events.py`, `run_context.py`, `exceptions.py` — coordinate before changing |
| Reusable nodes | Harry | `src/orchestrator/nodes/` | `BaseNode`, `LLMReasonerNode`, `WebSearchNode`, `PythonSandboxNode`, `VerifierNode` |
| Specialist agents | split | `src/orchestrator/agents/` | Jay: Research Analyst, Code Reviewer; Harry: Data Analyst, Fact Checker |
| Orchestrator graph | shared | `src/orchestrator/orchestrator/` | Harry: `graph_builder.py`; Jay: `supervisor.py` |
| Sandbox executor | Harry | `src/orchestrator/sandbox/` | Docker executor + policy enforcement |
| Persistence | Harry | `src/orchestrator/persistence/` | SQLAlchemy models + session management |
| Events bus | Harry | `src/orchestrator/events_bus/` | Dual-write publisher (Postgres + Redis) |
| API routes | shared | `src/orchestrator/api/routes/` | Harry: runs/workflows; Jay: agents |
| Tests | shared | `tests/` | Unit tests with each task; integration tests at phase checkpoints |
| Scripts | shared | `scripts/` | CLI utilities, phase checkpoints, seeding |
| Migrations | shared | `alembic/` | Generated, reviewed, and committed together |

### Agent-by-agent ownership

| Agent | Nodes it depends on | Owner | Built in |
|---|---|---|---|
| Research Analyst | `WebSearchNode` + `LLMReasonerNode` | Jay | Phase 1 |
| Code Reviewer | `PythonSandboxNode` + `LLMReasonerNode` | Jay | Phase 2 |
| Data Analyst | `PythonSandboxNode` + `LLMReasonerNode` | Harry | Phase 2 |
| Fact Checker | `WebSearchNode` + `VerifierNode` | Harry | Phase 2 |

> This is not a rigid contract. If one person finishes early or gets blocked, the natural move is to help unblock the other rather than start the next phase solo, because each phase's checkpoint depends on both halves being done.

---

## Working conventions

### Shared files that need a heads-up

The following files are depended on by almost every layer. Coordinate in the branch PR before changing them:

- `src/orchestrator/core/state.py`
- `src/orchestrator/core/events.py`
- `src/orchestrator/core/run_context.py`
- `src/orchestrator/orchestrator/workflow_schema.py`

### Event emission is non-negotiable

Every node and agent must emit events via `publish_event`:

- `NODE_STARTED` / `NODE_COMPLETED` / `NODE_FAILED` around node work
- `TOOL_CALL` / `TOOL_RESULT` when calling external tools
- `SUPERVISOR_DECISION` from the supervisor
- `RUN_COMPLETED` at the end of a run

The entire debugging, replay, and future dashboard story depends on this being consistent.

### Agent return contract

Every specialist agent's callable (the function registered in `AGENT_REGISTRY`) must return a dictionary containing exactly:

- `agent_results` — required, with at least one entry describing what the agent produced.
- `token_count` — required, containing the agent's own `state["token_count"]` after its nodes have run. This ensures cumulative token usage remains accurate across a multi-agent run.

The agent **must not return** `scratch`.

`scratch` is private working space for an agent's own nodes. Propagating it into the shared top-level state can cause key collisions between agents that reuse the same node types. For example, both the Research Analyst and Fact Checker use `WebSearchNode`, which defaults to writing under `scratch["search_results"]`.

See `agents/research_analyst.py` for the reference implementation.

### Code review rules

- Open PRs against `dev`.
- A PR must include passing local checkpoint output in its description.
- Rebase onto latest `dev` before requesting review.
- Do not merge your own PR unless it is a pure docs/tooling change.

### Environment and secrets

- `.env` is never committed (it is already in `.gitignore`).
- `.env.example` is the template; keep it in sync with `.env` keys.
- Generated Alembic files are committed; hand-edit them only when autogenerate is wrong, and note the edit in the PR.

---

## Project layout

```
aesthflow/
├── src/orchestrator/          # Importable package: `import orchestrator`
│   ├── core/                  # Shared contracts: state, events, run_context, exceptions
│   ├── nodes/                 # Reusable LangGraph nodes (reasoner, search, sandbox, verifier)
│   ├── agents/                # Specialist agents built from nodes (LangGraph subgraphs)
│   ├── orchestrator/          # Supervisor routing + graph assembly
│   ├── sandbox/               # Docker-based isolated Python execution
│   ├── persistence/           # SQLAlchemy models + DB session management
│   ├── events_bus/            # Redis publish/subscribe wrapper
│   ├── api/                   # FastAPI routes (Phase 4)
│   └── config.py              # Centralized Pydantic settings
├── tests/                     # Unit + integration tests
├── scripts/                   # CLI utilities and phase checkpoint scripts
├── alembic/                   # Database migrations
├── docker-compose.yml         # Postgres (5434) + Redis (6379)
├── pyproject.toml             # uv/pip project config
├── uv.lock                    # Dependency lockfile (committed)
├── .env.example               # Environment variable template
└── ARCHITECTURE.md            # Full design rationale
```

---

## Design principles

1. **Execution and observation are decoupled.** Nodes emit events; they do not know who consumes them. This makes logging, replay, and dashboards possible without touching agent code.
2. **Infrastructure is swappable.** Redis today could be Kafka later; Docker exec today could be E2B later. Downstream code talks only through the `sandbox/` and `events_bus/` interfaces.
3. **Prove the pattern once before scaling it.** One working agent (Phase 1) validates the architecture before adding the supervisor (Phase 3).
4. **Isolation is non-negotiable for code execution.** Dynamically generated Python never runs in-process. This is a security requirement, not a convenience.

---

## Team

Built by **Harry** (backend/infra, sandbox, persistence, orchestration plumbing) and **Jay** (agent logic, routing/prompt design, API surface).

### Onboarding notes for Jay

- Read `ARCHITECTURE.md` first — it explains why the layers are split the way they are.
- You will spend most of your time in `src/orchestrator/agents/` and `src/orchestrator/orchestrator/supervisor.py`.
- You generally will not need to touch `core/`, `persistence/`, or `events_bus/` unless we are changing a shared contract.
- Every node/agent must emit the standard events listed in [Working conventions](#working-conventions).
- Setup steps are in [Developer setup](#developer-setup) above.

---

## License

TBD.
