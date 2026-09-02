#!/bin/sh
set -eu

: "${MODEL_DOWNLOAD_URL:?MODEL_DOWNLOAD_URL is required}"
: "${MODEL_SHA256:?MODEL_SHA256 is required}"

MODEL_DIR=/models
MODEL_PATH="${MODEL_DIR}/model.gguf"
MARKER_PATH="${MODEL_PATH}.sha256"

/bin/mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_PATH" ] || [ ! -f "$MARKER_PATH" ] || \
   [ "$(/bin/cat "$MARKER_PATH")" != "$MODEL_SHA256" ]; then
  echo "Downloading model from Hugging Face (parallel and resumable)..."
  /usr/bin/aria2c --continue=true --max-connection-per-server=16 --split=16 \
    --min-split-size=8M --file-allocation=none --max-tries=0 --retry-wait=3 \
    --timeout=30 --summary-interval=30 --dir="$MODEL_DIR" \
    --out="model.gguf.partial" "$MODEL_DOWNLOAD_URL"
  ACTUAL_SHA256="$(/usr/bin/sha256sum "${MODEL_PATH}.partial" | /usr/bin/awk '{print $1}')"
  if [ "$ACTUAL_SHA256" != "$MODEL_SHA256" ]; then
    /bin/rm -f "${MODEL_PATH}.partial" "${MODEL_PATH}.partial.aria2"
    echo "Downloaded model checksum verification failed." >&2
    exit 1
  fi
  /bin/mv "${MODEL_PATH}.partial" "$MODEL_PATH"
  printf '%s' "$MODEL_SHA256" > "$MARKER_PATH"
fi

exec /usr/local/bin/llama-server \
  -m "$MODEL_PATH" --alias "$MODEL_ID" \
  --host 0.0.0.0 --port 8080 \
  --n-gpu-layers 999 --ctx-size "$CONTEXT_SIZE" --parallel 1
