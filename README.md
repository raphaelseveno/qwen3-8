# OpenHands + SkyPilot on-demand coding model

This is a self-hosted coding-agent stack, not merely a chat UI:

```text
OpenHands (browser UI) ──> wake gateway on your Mac/CPU VM ──> SkyPilot GPU
       │                         │                                  │
       └── project files, Git,   └── starts/stops the GPU             └── llama.cpp
           terminal, MCP              after real request inactivity       Qwen GGUF
```

**OpenHands** is the established web UI. It gives each task an isolated coding
container and is the place to work with repositories, files, terminal commands,
Git, MCP servers, and secrets. **SkyPilot** selects the least-cost available
matching GPU across the cloud accounts you authenticate, while the gateway
ensures you are not billed for GPU time between coding sessions.

## Cost and cache policy

The GPU is terminated **10 minutes after the last completed response** by
default. The first deployment downloads the public GGUF directly from Hugging
Face with 16 parallel, resumable HTTP connections and verifies its SHA-256.
RunPod cannot stop-and-resume a SkyPilot pod, so a terminated pod does not keep
its local model cache.

This is inexpensive, but a brand-new provider is still a cold start. GPU VRAM
cannot survive a stop, migration, or provider change.

## Why the gateway is required

SkyPilot's native autostop is based on whether a cluster has a running job. A
model server is deliberately long-running, so `gateway/app.py` instead starts
the cluster only on an actual OpenAI request and calls `sky down` after
`IDLE_MINUTES`. `/v1/models` deliberately does not wake a GPU when OpenHands
refreshes its model selector.

### Fastest cold starts: publish the prebuilt server image

The repository includes a RunPod-compatible image recipe that already contains
CUDA llama.cpp. Publish it once to a registry you control, then add its name to
`.env` as `MODEL_SERVER_IMAGE`. This removes compilation from new GPUs; only
provider allocation, image pull, and the parallel GGUF download remain.

```sh
docker login
MODEL_SERVER_IMAGE=docker.io/YOUR_USER/qwen3-8-llama:cuda \
  ./scripts/publish_model_server_image.sh
```

Set the same image name in `.env`, then restart the gateway:

```sh
docker compose up -d --build gateway
```

## First-time setup

### 1. Prepare SkyPilot on the control machine

The control machine can be your Mac while it is on, or a small always-on CPU VM.
It must remain available so it can wake the GPU. Install SkyPilot and authenticate
only the clouds you are prepared to use. Then run:

```sh
sky check
sky show-gpus --all
```

SkyPilot will only compare the cloud accounts configured by `sky check`. Set any
provider budget limits and quotas first.

### 2. Start the wake gateway

From this directory on that control machine:

```sh
python3 -m venv .venv
.venv/bin/pip install -r gateway/requirements.txt skypilot
IDLE_MINUTES=10 SKY_TASK_FILE=skypilot/model-server.yaml \
  .venv/bin/uvicorn gateway.app:app --host 0.0.0.0 --port 8787
```

Verify without launching a GPU:

```sh
curl http://127.0.0.1:8787/v1/models
```

The first chat completion on any provider can take several minutes because
SkyPilot must provision a GPU and llama.cpp must load the 29 GB Q8 model. A
previously used stopped cluster is faster. A brand-new provider downloads from
Hugging Face again, which is the tradeoff for removing R2 and its credentials.
The download uses `aria2c --continue` with 16 connections, so an interrupted
transfer continues from the provider disk instead of starting from zero.

### 3. Start OpenHands

On the same Mac or a Linux host with Docker:

```sh
mkdir -p workspace
docker compose up -d
```

Open `http://localhost:3001` for this installation. This non-default port is
also configured for agent-container callbacks. In OpenHands **Settings → LLM → Advanced** set:

| Setting | Value when OpenHands runs on the same Mac |
| --- | --- |
| Custom model | `openai/qwen3.8-27b-obliterated-q8` |
| Base URL | `http://host.docker.internal:8787/v1` |
| API key | `local-gateway` |

If OpenHands runs on another host, replace `host.docker.internal` with the
private Tailscale/VPN address of the gateway. Do not expose port 8787 publicly:
it can create cloud GPU resources.

Mount a repository below `workspace/`, then open it in OpenHands. The agent has
the ability to modify everything mounted there, so do not mount your entire home
directory or credentials.

## GPU policy

`skypilot/model-server.yaml` permits L40S 48 GB, RTX A6000 48 GB, or A100 80 GB.
They are sufficient choices for the requested Q8 file plus a 32k coding context.
SkyPilot optimizes price among only these compatible GPU offerings and only across
your configured providers. Edit this list to exclude providers or GPUs you do
not want.

The model is based on a general Qwen model and altered for refusal removal; it is
not a dedicated coding-model release. Validate it on one of your real projects
before relying on it for autonomous edits. OpenHands generally works better with
at least 22k context, hence the 32k default.

## MCP and project management

OpenHands manages MCP configuration and secrets in its Settings UI. Add MCP
servers selectively and give them the minimum credentials needed. Its Docker
sandbox is intentionally powerful enough to edit and test code; keep OpenHands
behind Tailscale or another authenticated private network.

## Operations

```sh
# See the selected provider, price, and cluster state.
sky status qwen-coding

# Terminate now (normally the gateway does this after inactivity).
sky down --yes qwen-coding

# Launch manually.
sky launch --yes --detach-run -c qwen-coding skypilot/model-server.yaml
```

For a fresh cheapest-provider search, use `sky down --yes qwen-coding` before the
next task. Expect a full model download and cold start afterwards.
