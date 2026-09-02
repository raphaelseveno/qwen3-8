FROM ghcr.io/ggml-org/llama.cpp:server-cuda AS llama-server

FROM runpod/base:1.2.0-rc.162-cuda1281-ubuntu2404

# Keep the RunPod base image's SSH/bootstrap behavior so SkyPilot can manage
# it. Reuse llama.cpp's official CUDA build instead of compiling on GitHub.
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=llama-server /app /opt/llama
RUN ln -s /opt/llama/llama-server /usr/local/bin/llama-server
ENV LD_LIBRARY_PATH=/opt/llama:${LD_LIBRARY_PATH}
COPY start-model-server.sh /start-model-server.sh
