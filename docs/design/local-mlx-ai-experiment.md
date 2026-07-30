# Apple Silicon local MLX AI experiment

Status: implemented experiment, gated to macOS arm64

## Goal

Give an Apple Silicon user an explicit local-model manager in Desktop's Local
Control Center. After a one-time model download, Lemma can run its default
language model through a loopback-only OpenAI-compatible API without an
internet inference provider.

This does not make every Lemma capability air-gapped. Initial Desktop/runtime
installation, the model download, OCI images, hosted integrations, web
research, and external connectors still need their respective networks.
Local inference, local workspace data, and tools that do not call external
services can work after those assets are present.

## Upstream choice

The experiment pins two agent-ready models:

- `prism-ml/Ternary-Bonsai-8B-mlx-2bit`, revision
  `9260b24298e4211e804663e9f519962cf59f34be`, 2,315,166,534 bytes;
- `mlx-community/Qwen3-4B-4bit`, revision
  `4dcb3d101c2a062e5c1d4bb173588c54ea6c4d25`, 2,278,969,756 bytes;
- server: `mlx-lm==0.31.3`;
- MLX: `0.32.0` from the resolved lock;
- API: `http://127.0.0.1:<dynamic-port>/v1`.

The model publishers document stock MLX loading, 65,536-token Bonsai and
40,960-token Qwen3 contexts, and Apache-2.0 licensing:

- <https://huggingface.co/prism-ml/Ternary-Bonsai-8B-mlx-2bit>
- <https://github.com/PrismML-Eng/Bonsai-demo>
- <https://huggingface.co/Qwen/Qwen3-4B>
- <https://huggingface.co/mlx-community/Qwen3-4B-4bit>

Apple's MLX-LM server supplies `/v1/models` and
`/v1/chat/completions`, including streaming and OpenAI-style tool calls:

- <https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md>
- <https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py>

Third-party native servers were considered:

- <https://github.com/ddalcu/mlx-serve>
- <https://github.com/cubist38/mlx-openai-server>
- <https://github.com/KingboardMa/mlx-llm-server>

The first slice uses official MLX-LM because both the model publisher and Apple
document the same runtime path, and its current API emits native tool calls.
A native Swift server remains an optimization option if Python payload size or
server hardening becomes a release concern.

## Product behavior

The card is absent unless `locald` reports a macOS arm64 build. It is opt-in:
no model request is made until the user presses **Download** for that model.
Only one downloaded model is loaded in unified memory at a time. Every catalog
entry exposes download/use/stop/delete actions. A lifecycle lock serializes
model starts, a repeated start reuses the existing process, and an exact
process-identity marker lets a replacement daemon reclaim its predecessor
before it can launch another server.

A download and activation:

1. checks that the signed host runtime contains the isolated MLX package tree;
2. requires model bytes plus 4 GiB free headroom;
3. downloads only the pinned public model files into app-owned state;
4. resumes through the Hugging Face client after interruption and reports
   transferred bytes, throughput, and ETA from completed and partial files;
5. validates expected file sizes and SHA-256 for the weights and tokenizer;
6. starts MLX-LM on an OS-selected loopback port;
7. sends a real one-token completion with an OpenAI tool schema as the
   readiness gate;
8. discovers `/v1/models`;
9. transactionally selects the endpoint as Lemma's default OpenAI-compatible
   profile;
10. restarts only the backend when it is already running.

Any activation failure stops MLX and restores the previous provider profile
and backend environment. **Stop local AI** releases Metal/unified memory and
persists disabled autostart. An explicit application **Quit** sends the same
ownership-aware release request and waits for locald to confirm that the MLX
process is gone before the desktop exits. The downloaded files and selected
model remain, but serving must be opted into again on the next launch. Closing
the window to the tray is not a quit and does not interrupt an active request.

Selecting another provider explicitly disables MLX autostart after the new
profile validates.

## Serving policy

Both curated models are served with thinking enabled and Qwen3's recommended
non-greedy sampling defaults: temperature `0.6`, top-p `0.95`, top-k `20`, and
min-p `0`. Lemma allows up to 4,096 output tokens per turn, one active decode,
one concurrent prompt prefill, two prompt-cache entries, and a 1 GB reusable
prompt-cache budget. The model's own pinned 65,536/40,960-token context
configuration remains authoritative and no extra YaRN scaling is added.

There is no user-facing RAM allocation. MLX applies Metal's recommended working
set and macOS manages unified-memory pressure. Limiting active decode
concurrency prevents two large agent contexts from growing simultaneously on a
16 GB machine without reducing the context available to one agent turn.

The pinned Bonsai template contains the Qwen JSON tool schema but hardcodes an
empty thinking block at the generation boundary. At launch, `locald` verifies
that exact reviewed template and passes MLX-LM an in-memory override matching
the upstream Qwen3 `enable_thinking` behavior. Downloaded model files remain
unchanged. A template revision that no longer matches fails closed instead of
silently serving Bonsai without reasoning.

The catalog excludes models whose pinned MLX chat template cannot provide both
thinking and structured OpenAI tool calls. This is why Gemma 3 is not offered
as an agent-ready option in this slice.

## Ownership and offline boundary

`lemma-locald` owns configuration, download, verification, process lifecycle,
port allocation, logs, provider activation, and recovery. The Desktop shell is
only a privileged Control Center client.

State lives below:

```text
locald/
  local-ai/
    config.json
    process.json
    huggingface/
    models/ternary-bonsai-8b-mlx-2bit/
    models/qwen3-4b-mlx-4bit/
  logs/local-ai.log
```

The process marker records model/revision, PID, canonical executable, and OS
start identity. After a daemon crash, `locald` terminates an orphan only when
all recorded identity fields match the live process. A missing, damaged,
reused-PID, or changed-executable identity is never killed.

The serving process receives `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`; it cannot fetch model files during inference. It
binds only `127.0.0.1`. MLX-LM itself warns that its server has basic security
and is not a production network service, so this endpoint must never be
published to LAN or public interfaces.

## Packaging

`desktop/mlx-runtime/uv.lock` is a separate immutable dependency graph. On an
Apple Silicon host-pack build, `build_local_host_pack.py` installs it into:

```text
local-runtime/backend/mlx-runtime
```

The runtime is not installed into the backend environment or the user's
Python environment. Non-Apple host-pack builds omit it. The 2.3 GB model is
never included in an app or runtime archive.

The verified MLX package tree expands to about 310 MiB in the current lock.
Host-pack size reports expose that cost separately as `mlx_runtime_bytes`.

## Verified acceptance

The implementation has been exercised on Apple Silicon with the exact locked
runtime and full pinned model:

- immutable download and checksum verification succeeded;
- `/v1/models` returned the canonical local model ID;
- `/v1/chat/completions` returned `LOCAL_OK`;
- a function request returned `finish_reason: "tool_calls"` with
  `get_weather({"city":"Delhi"})`;
- operator readiness became `ready` with the managed loopback URL;
- explicit stop persisted `enabled: false`;
- a deliberately orphaned MLX server was reclaimed after exact PID,
  executable, and OS-start-identity matching;
- all `locald` and Desktop Rust tests passed.

Before release promotion, the remaining merge gates are the normal packaged
PR-DMG run, full backend agent/tool smoke tests, memory-pressure testing on
8/16/24 GiB Macs, and host/guest archive size gates.
