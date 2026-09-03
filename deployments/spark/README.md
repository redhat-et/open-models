# DGX Spark (GB10, 128GB unified memory, ~273GB/s)

Serve with vLLM (DGX OS has the CUDA/ARM stack ready). 128GB unified means
big models *fit* but memory bandwidth sets the speed ceiling - prefer MoE or
quantized dense for the "fast" tier.

## Run a model

```bash
docker run -d --name vllm --restart unless-stopped \
  --gpus all --network host --ipc host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -e HF_TOKEN \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-30B-A3B \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.85 \
  --enable-prefix-caching \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder
```

Notes:
- Spark is arm64 - use multi-arch images; vLLM publishes arm64 builds (verify
  tag support before pinning).
- 128GB shared with OS: keep `--gpu-memory-utilization` <= 0.85.
- One model per Spark. The web front-end expects `/health` and `/v1/models`.

## systemd unit (survive reboots without docker restart policy)

```ini
[Unit]
Description=vLLM (OCTO Open Models)
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker rm -f vllm
ExecStart=/usr/bin/docker start -a vllm
ExecStop=/usr/bin/docker stop vllm

[Install]
WantedBy=multi-user.target
```

## Startup instrumentation (RFE)

Wrap `docker logs -f vllm | ts` into `results/startup.log`, record wall-clock
from container start to first `200` on `/health`; compare flags
(`--enforce-eager`, `--load-format`, prefix caching on/off) to find the
fastest safe startup. Target: model reachable in < 2 min on Spark.
