# AesthFlow — Architecture

## 1. Core idea

A **supervisor/worker** multi-agent system. One orchestrator agent looks at an incoming task, decides which specialist agent(s) it needs, invokes them (possibly more than one, possibly in sequence), and merges their outputs into a final answer. This is a deliberate choice against the alternative — peer-to-peer agents that negotiate directly with each other — because supervisor/worker is a well-understood pattern with native support in LangGraph, whereas peer-to-peer coordination is still an open research problem with no settled best practice.

```
                    ┌─────────────────┐
   task ──────────► │   Supervisor     │
                    │  (routing logic) │
                    └──────┬───────────┘
                           │ decides which agent(s) to call
              ┌────────────┼────────────┬──────────────┐
              ▼            ▼            ▼              ▼
       ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌──────────────┐
       │ Research  │ │   Code    │ │   Data    │ │    Fact      │
       │ Analyst   │ │ Reviewer  │ │ Analyst   │ │   Checker    │
       └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └──────┬───────┘
             │             │             │              │
             └─────────────┴──────┬──────┴──────────────┘
                                   ▼
                         results merged, returned
                         to Supervisor for a final
                         decision: done, or route again
```

Every box above is a LangGraph node or subgraph. Every arrow is a state transition. Every hop emits an event.

---

## 2. Layers, and why each one exists

### `core/` — shared contracts, no I/O

`state.py`, `events.py`, `run_context.py`, `exceptions.py`. This layer defines *what things look like* (the shape of state passed between nodes, the shape of an event) without deciding *how* they're delivered or stored. Nothing in `core/` talks to Postgres, Redis, or Docker. This matters because it means `core/` is trivially unit-testable, and because every other layer depends on it — if it changes, everything downstream needs to be re-evaluated, so it should change as rarely as possible. This is why it was built first, in Phase 0.

### `nodes/` — the atomic units of execution

A `BaseNode` interface that every concrete node (`LLMReasonerNode`, `PythonSandboxNode`, `WebSearchNode`, `VerifierNode`) implements. Each node:
- Takes the shared `AgentState` in
- Does one thing (call an LLM, run code, search the web, cross-check a claim)
- Emits `NODE_STARTED` / `NODE_COMPLETED` (or `NODE_FAILED`) events around its work
- Returns a partial state update

Nodes know nothing about *which agent* they belong to or *why* they're being called — that's the agent's job. This separation is what lets you reuse `WebSearchNode` in both Research Analyst and Fact Checker without duplicating code.

### `agents/` — specialist compositions of nodes

Each specialist agent is a small LangGraph subgraph: a fixed sequence (or small graph) of nodes wired for a specific purpose. Research Analyst = search node → reasoner node. Data Analyst = sandbox node → reasoner node. Agents are registered in `agents/registry.py` with metadata (name, description, required tools) — this registry is what the Supervisor consults when deciding who to route to, and later, what an Agent Library UI would list.

**Why agents are subgraphs, not just Python functions:** keeping them as LangGraph subgraphs means they get the same event emission, state handling, and (later) checkpointing/retry behavior as everything else, instead of being a special case the orchestrator has to handle differently.

### `orchestrator/` — the supervisor and graph assembly

`supervisor.py` holds the routing decision logic — given the task and results so far, which agent (if any) should run next, or is the task complete. `graph_builder.py` assembles the full LangGraph `StateGraph`: supervisor node with conditional edges to each specialist subgraph, each of which loops back to the supervisor. This is the only layer that "knows about everything" — nodes don't know about agents, agents don't know about other agents, but the orchestrator knows about all of them. That's intentional: it keeps the blast radius of a design change contained to one layer.

### `sandbox/` — isolated code execution

Dynamically generated Python never executes in the same process as the orchestrator or API. `docker_executor.py` spins up a locked-down container per execution with enforced timeout and memory limits (`policies.py`). This is treated as a hard security boundary, not a convenience wrapper — the risk isn't hypothetical: an LLM-generated script is untrusted input by definition, no different from user-submitted code in any other context.

### `persistence/` — Postgres as the system of record

Three tables: `Run` (one row per task execution), `EventRow` (every event, ever — this is what makes execution logs/debugging possible after the fact with zero additional work), `WorkflowDefinition` (saved graphs, for Phase 3+ and the future Workflow Builder). Postgres is the durable source of truth; it's slower than Redis but it's what you query for history, audits, and debugging days later.

### `events_bus/` — the live tap

Redis Streams. Every event gets dual-written: Postgres first (must succeed), Redis second (best-effort). Redis is not the source of truth — it's what a future dashboard or log-tailer would subscribe to for *live* updates. If Redis is down, the system keeps working; you just lose live visibility, not history.

### `api/` — the thin outer layer (Phase 4)

FastAPI routes that trigger runs, report status, and expose event history. Deliberately built last: the API is a thin wrapper over an already-working execution core, not a scaffold the core is built to fit. Building it first tends to bake in assumptions about request/response shapes before you know what the execution engine actually needs to expose.

---

## 3. Data flow for one task

1. A task string comes in (via API in Phase 4, via CLI script before that).
2. A `RunContext` is created (`run_id`, token budget) → converted into the initial `AgentState`.
3. A `Run` row is inserted into Postgres, status `running`.
4. The LangGraph graph is invoked with the initial state.
5. Supervisor node runs first: decides which specialist to call (or that the task is done). Emits a `SUPERVISOR_DECISION` event.
6. Control passes to the chosen specialist's subgraph. Each node inside it emits `NODE_STARTED`/`NODE_COMPLETED`/`TOOL_CALL`/`TOOL_RESULT` events as it works, and every event is dual-written (Postgres + Redis) via `publish_event`.
7. The specialist's output is appended to `agent_results` in shared state; control returns to the Supervisor.
8. Supervisor re-evaluates: call another agent, or mark `is_complete = True` with a `final_answer`.
9. On completion, the `Run` row is updated (`status=completed`, `final_answer`, `token_count`), and a `RUN_COMPLETED` event is emitted.
10. Anyone querying `GET /runs/{id}/events` afterward gets the *exact* sequence of steps 5–9, in order, with full payloads — this is the entire "Execution Logs" feature, and it required no extra code beyond consistent event emission.

---

## 4. Key design decisions and their trade-offs

| Decision | Alternative considered | Why this choice |
|---|---|---|
| Supervisor/worker orchestration | Peer-to-peer agent negotiation | Native LangGraph support; well-understood failure modes; peer-to-peer is still an open research pattern |
| Dual-write events (Postgres + Redis) | CDC (change-data-capture) from one to the other | Simpler to reason about and debug at this project's scale; CDC is over-engineering for a single-node system |
| Docker containers for sandboxing | In-process `exec()` | `exec()` has no real isolation — a genuine security hole once code execution touches production workloads |
| TypedDict for `AgentState` | Pydantic model | LangGraph's `StateGraph` is built around TypedDict + reducer functions; fighting the framework's idiom adds friction for no benefit |
| Postgres as source of truth, Redis as live tap | Redis Streams as the only store | Redis isn't designed as a durable long-term audit log; Postgres is |
| Fixed specialist agents (not dynamically generated) | Agents that spawn sub-agents at runtime | Out of scope for v1 — a research-frontier feature, not required to prove the core pattern |

---

## 5. What "production-capable" will require beyond this backend

This architecture is designed so that reaching production readiness is additive, not a rewrite:
- Horizontal scaling of the API layer (already stateless — no in-memory run state)
- Swapping Redis Streams for Kafka if throughput demands it (isolated behind `events_bus/`)
- Swapping the Docker executor for a managed sandbox service like E2B (isolated behind `sandbox/`)
- Authentication/authorization on the API (not yet built — Phase 4 is functional, not secured)
- Rate limiting and multi-tenancy if this ever serves more than one user's workloads concurrently

None of these require touching `core/`, `nodes/`, or `agents/` — which is the entire point of keeping those layers free of infrastructure concerns.