FROM runpod/base:1.2.0-rc.162-cuda1281-ubuntu2204

# Keep the RunPod base image's SSH/bootstrap behavior so SkyPilot can manage
# it. Build llama.cpp once into the published image, never on a task wake.
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 ca-certificates build-essential cmake git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp.git /opt/llama.cpp \
    && cmake -S /opt/llama.cpp -B /opt/llama.cpp/build -DGGML_CUDA=ON \
       -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF \
    && cmake --build /opt/llama.cpp/build --target llama-server -j"$(nproc)" \
    && install -m 0755 /opt/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server \
    && rm -rf /opt/llama.cpp
COPY start-model-server.sh /start-model-server.sh
