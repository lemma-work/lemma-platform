# Fast E2E: mocked LLM + fake AgentBox (CI-runnable) — design & plan

## Goal

Two levels of e2e, both kept:

1. **Mocked level (default — runs in CI + locally, fast):** no real LLM, no Docker.
   - LLM responses come from a deterministic **mock model** (scripted text/tool-call/final-answer sequences).
   - Workspace/CLI tools run against an **in-process fake AgentBox** (local subprocess sandbox) exposing the same client API.
   - Full pipeline exercised end-to-end: API → events → streaq worker → harness → tools → persistence → SSE.
2. **Real level (flag-gated — local / nightly):** real model from env + real AgentBox over Docker, parallelized under a time budget.

CI runs level 1 only (no model keys, no Docker workspace). Level 2 runs on demand with a flag.

## Design

### A. Mock LLM — swap the *model*, keep the harness

Reuse `PydanticAIHarness` unchanged so tool execution, streaming, event emission, and persistence are all real. Only the underlying pydantic-ai model is swapped.

- **Injection point:** `app/modules/agent/services/runtime_model_factory.py` (`pydantic_ai_model_from_runtime_profile` / `require_...`). When mock mode is on, return a pydantic-ai `FunctionModel` instead of the real `OpenAIChatModel`/`AnthropicModel`.
- **The mock model** is a `FunctionModel` whose callback, on each model request, returns the next scripted `ModelResponse` (plain text, or tool calls). The agent loop then *really* executes those tool calls (against the fake AgentBox) and feeds results back — so multi-step tool flows are exercised.
- **Per-test scripting (worker is a separate process):** a small Redis-backed `MockLLMScriptStore` keyed by `conversation_id`. A test registers a script (list of steps: `text`, `tool_call(name,args)`, `final(text)`); the worker's `FunctionModel` reads it and advances by step (using the assistant/tool-return turn count in history to pick the step).
- **Default when no script:** return a single final answer (e.g. `"[mock] <ack of last user message>"`) so "a run that completes" tests need zero setup.
- **Selection:** `build_harness_registry()` stays as-is; the model factory checks `settings.e2e_llm_mode`. Daemon harnesses (codex/claude/opencode) keep their real-CLI path and are real-level only.

### B. Fake AgentBox — local subprocess manager

A minimal ASGI app implementing the AgentBox manager HTTP contract the `AgentBoxClient` calls (`agentbox-client/agentbox_client/client.py`): `PUT /sandboxes/{id}`, `PUT .../sessions/{sid}`, `POST .../exec-command`, `POST .../python`, `POST .../stdin`, `DELETE .../processes/{pid}`, `GET .../processes`, heartbeats, `DELETE`.

- Each sandbox = a temp directory; each session = a cwd. `exec_command` runs the command with `asyncio.create_subprocess_shell` in that dir (with the run's env injected, incl. `lemma` CLI vars). `python` runs via a subprocess interpreter. Long-running/`tty` processes tracked in a dict for `stdin`/`list`/`terminate`.
- Runs **in-process** for tests (mounted on the test backend or a tiny uvicorn on a random port). `AGENTBOX_API_URL`/`AGENTBOX_API_KEY` point at it.
- Lives in `app/modules/workspace/testing/fake_agentbox.py` (importable by tests + a `make` target). Reuses the real `AgentBoxClient` + `WorkspaceSandboxService` unchanged → the whole tool→client→manager path is exercised.

### C. Env / flag scheme (CI + local)

Single source of truth in `config.py`:

| Setting (env) | Default | `real` |
|---|---|---|
| `E2E_LLM_MODE` (`mock`\|`real`) | `mock` | `real` |
| `E2E_SANDBOX_MODE` (`fake`\|`docker`) | `fake` | `docker` |

Convenience: `E2E_REAL=1` flips both to real. Existing `LEMMA_RUN_PROVIDER_E2E` becomes an alias for `E2E_LLM_MODE=real`.

- **CI (GitHub Actions):** nothing set → mock + fake. No model keys, no Docker workspace image. Runs `make test` + `make test-e2e` (mock).
- **Local fast:** same as CI (default).
- **Local real:** `E2E_REAL=1 make test-e2e-real` → real model (keys from `.env`) + Docker AgentBox, parallelized.

### D. Test markers / organization

- Default e2e run = everything except `real_llm` / `real_sandbox`-marked tests. Mock fixtures (mock model + fake AgentBox) are **autouse in e2e** unless `E2E_*_MODE=real`.
- `@pytest.mark.real_llm` / `@pytest.mark.real_sandbox`: tests that ONLY make sense with the real thing (a handful of model/sandbox smoke tests) — skipped unless the matching real mode is on.
- The bulk of agent/surface/workflow pipeline tests run in BOTH modes (mock in CI, real on demand) — same test body, fixtures decide the backend.

### E. Parallelization (both modes)

Per-`PYTEST_XDIST_WORKER` isolation already exists for containers (`e2e_base.py`). Extend it to the **agentbox + worker ports** (currently fixed → why runtime is serial): suffix the agentbox port and worker queue/namespace by worker id so the runtime suite runs `-n N`. Fake AgentBox is in-process (no port contention) → mock runtime parallelizes trivially.

## Implementation phases

1. **Mock model**: `FunctionModel` factory + `MockLLMScriptStore` (Redis) + model-factory hook on `E2E_LLM_MODE`. Unit-test the script→events mapping (no infra).
2. **Fake AgentBox**: `fake_agentbox.py` (ASGI manager + subprocess sandbox) + fixture wiring `AGENTBOX_API_URL`. Unit/integration-test exec_command/python against it.
3. **Config + fixtures**: `E2E_LLM_MODE`/`E2E_SANDBOX_MODE`/`E2E_REAL`, autouse e2e fixtures selecting mock vs real, markers `real_llm`/`real_sandbox`.
4. **Convert worker-driven agent e2e** (`test_agent_e2e.py` etc.) to run under mock by default; script the few that need specific tool sequences; mark real-only ones.
5. **Parallelize** runtime suite (per-worker agentbox/worker ports).
6. **Makefile + CI**: `test-e2e` (mock, default), `test-e2e-real` (flagged), GitHub Actions workflow running the mock gate.

## Verification

- Mock gate runs with NO model keys and NO Docker, green, in a few minutes, parallelized.
- `E2E_REAL=1` path still green locally (real model + Docker AgentBox).
- A deliberately-broken pipeline step (e.g. drop an SSE event) fails the mock gate — proving it catches real bugs.
