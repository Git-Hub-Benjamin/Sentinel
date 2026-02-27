# AI Understanding of Sentinel — Architecture & Vision

## What Sentinel Is (Current)

A GPU arbitration daemon for a shared research workstation. It ensures the owner's
research workloads always get GPU priority over Ollama inference by monitoring SSH
sessions and pausing/resuming Ollama (`systemctl stop/start ollama`) accordingly.

**Current behavior:**
- Ollama runs freely when only `benjamin` is connected (or no one)
- A guest SSH session → Ollama stops within `poll_interval` seconds
- Guest disconnects → Ollama resumes after `restart_delay` seconds
- `sentinel-request <cmd>` wraps research workloads with explicit GPU lock
- Locks are reference-counted — stacked sentinel-request calls work correctly
- If a sentinel-request finishes while guests are still connected, inference
  stays paused (fixed race condition, checked at release time)

**Daemon state machine:** IDLE → RESEARCH → IDLE (via RESUMING with delay)

---

## What Sentinel Is Becoming (Vision)

A **compute node agent** — one instance per physical machine — that exposes GPU
capacity to OpenClaw so inference requests can be scheduled across a private GPU
cluster. The goal is for OpenClaw to treat this entire setup as a generic
inference/compute layer, identical in interface to OpenAI or Anthropic APIs,
but routing to owned hardware over Tailscale.

```
OpenClaw (orchestrator)
  │
  ├─── Tailscale ──► vlsi-space Sentinel  → Ollama :11434  (RTX 5090, 32GB)
  ├─── Tailscale ──► machine-2  Sentinel  → Ollama :11434  (RTX XXXX, YGB)
  └─── Tailscale ──► machine-N  Sentinel  → Ollama :11434  (...)
```

Each Sentinel node:
- Reports GPU capacity (total VRAM, free VRAM, loaded models + their sizes)
- Acts as a gatekeeper (SSH watchdog, research priority locks)
- Proxies or redirects inference requests to its local Ollama

OpenClaw holds a live connection to each node, queries capacity, and routes
inference requests accordingly — exactly like a lightweight Kubernetes GPU
scheduler but purpose-built for LLM inference.

---

## Concurrent Agents on One GPU (The Math)

**Setup:** RTX 5090, 32GB VRAM, running `qwen3.5:35b` (24GB weights)

**Key insight:** When multiple agents use the *same model*, Ollama shares the
weights. The model loads once (24GB). Each additional concurrent session only
costs its **KV cache** — not another copy of the weights.

**Free VRAM after loading model:** 32GB − 24GB = **8GB** for KV caches

KV cache size scales with context length:

| Context length | KV cache / session | Concurrent sessions on 8GB |
|---------------|-------------------|---------------------------|
| 4K tokens     | ~300 MB           | ~26 (throughput-limited)  |
| 16K tokens    | ~750 MB           | ~10                       |
| 32K tokens    | ~1.5 GB           | ~5                        |
| 64K tokens    | ~3 GB             | ~2                        |

**Practical answer: 6–8 concurrent agents at typical (8K–16K) context.**

Throughput is the real constraint — attention computation is expensive and
each new concurrent session slows all others. 6–8 is where latency starts
degrading noticeably. For an orchestrator + sub-agents pattern, a good rule is:

- 1 large orchestrator model (24GB) sharing weights
- Up to 6 sub-agent sessions before latency degrades unacceptably

**Different models:** Each distinct model requires its own full VRAM allocation.
If you need `qwen3.5:35b` (24GB) + `mistral:7b` (5GB), that's 29GB — fits on
the 5090, but leaves only 3GB for KV caches. Only ~4 total concurrent sessions.

---

## Scheduling Logic (OpenClaw's Job)

OpenClaw asks each Sentinel node: *"can you fit this model?"*

```
Request: run model M, estimated VRAM V

For each node:
  if M already loaded on node:
    cost = kv_cache_size(context_length)   # cheap!
  else:
    cost = model_vram(M) + kv_cache_size(context_length)

  if node.free_vram >= cost and node.accepting_requests:
    route here
    break

If no node fits:
  → queue (wait for a session to finish on some node)
  → or reject with 503 (let caller decide)
```

**When nothing fits:**
- Same model as something already loaded → queue it (low cost when it runs)
- Different model and all GPUs full → OpenClaw must queue, offload to cloud API,
  or evict lowest-priority loaded model (LRU eviction, Ollama does this
  automatically with `OLLAMA_MAX_LOADED_MODELS`)

---

## Sentinel HTTP API (Needs to Be Built)

To support OpenClaw scheduling, Sentinel needs a small HTTP server exposing:

```
GET  /capacity    → {total_vram, free_vram, loaded_models, accepting_requests}
GET  /status      → full daemon state (existing socket status + GPU info)
POST /v1/*        → proxy to Ollama's OpenAI-compatible API (:11434/v1/*)
```

The proxy endpoint lets OpenClaw talk to Sentinel exactly like it talks to
OpenAI — same API format (`api: "openai-responses"` in openclaw.json),
just pointed at `http://<tailscale-hostname>:<sentinel-port>/v1/`.

Sentinel adds value at the proxy layer by:
- Rejecting requests if SSH guests are present (or queuing them)
- Tracking which models are loaded and updating free VRAM estimates
- Enforcing research workload priority (sentinel-request locks block inference)

---

## OpenClaw Integration

OpenClaw uses `~/.openclaw/openclaw.json` for provider configuration.
Ollama is OpenAI-compatible, so the config looks like:

```json
{
  "providers": {
    "vlsi-sentinel": {
      "baseUrl": "http://vlsi-space:8766/v1",
      "apiKey": "sentinel-local",
      "api": "openai-responses",
      "models": [
        { "name": "qwen3.5:35b", "contextWindow": 32768 },
        { "name": "mistral:7b-instruct-q5_K_M", "contextWindow": 32768 }
      ]
    }
  }
}
```

**Known issue (OpenClaw #7211):** Sub-agents silently fall back to the default
Anthropic model when a local provider is configured. Fixed in PR #9822 — ensure
OpenClaw is on a version that includes that merge before testing sub-agent routing.

**Model reference format in OpenClaw:** `vlsi-sentinel/qwen3.5:35b`

---

## Tailscale Setup

Tailscale is the right choice here. No port forwarding, works through NAT,
machines find each other automatically, encrypted. Each machine just joins
the same Tailscale network and becomes reachable by hostname.

Install and connect (run on each machine):
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

After that, `vlsi-space` is reachable from any other Tailscale node at
`http://vlsi-space:11434` (Ollama) or `http://vlsi-space:8766` (Sentinel HTTP API).

To expose only on Tailscale (not the public internet), bind Sentinel's HTTP
server to the Tailscale interface IP instead of `0.0.0.0`.

---

## Multi-Model Ollama Config

Set in `/etc/systemd/system/ollama.service` (or override):

```ini
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=4"
Environment="OLLAMA_NUM_PARALLEL=8"
```

`OLLAMA_MAX_LOADED_MODELS=4` — keep up to 4 models in VRAM simultaneously
`OLLAMA_NUM_PARALLEL=8` — allow up to 8 concurrent inference requests

Reload with:
```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

---

## What Still Needs to Be Built

| Component | Status | Notes |
|-----------|--------|-------|
| SSH watchdog | ✅ Done | Polls `who`, pauses on guests |
| sentinel-request locks | ✅ Done | Reference-counted |
| sentinel-monitor | ✅ Done | Live terminal dashboard |
| Tailscale | ⬜ Install | One command per machine |
| Sentinel HTTP API | ⬜ Build | `/capacity`, `/status`, `/v1/*` proxy |
| VRAM reporter | ⬜ Build | `nvidia-smi` + Ollama `/api/ps` |
| OpenClaw config | ⬜ Config | Point at Tailscale hostnames |
| Multi-GPU routing | ⬜ OpenClaw | Scheduling logic lives in OpenClaw |
