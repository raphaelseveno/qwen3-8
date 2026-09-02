#!/usr/bin/env sh
# Usage: MODEL_SERVER_IMAGE=docker.io/your-user/qwen3-8-llama:cuda \
#          ./scripts/publish_model_server_image.sh
set -eu

: "${MODEL_SERVER_IMAGE:?Set MODEL_SERVER_IMAGE to your Docker Hub or GHCR image name.}"

docker buildx build --platform linux/amd64 --push \
  --tag "$MODEL_SERVER_IMAGE" \
  -f skypilot/model-server/Dockerfile skypilot/model-server
