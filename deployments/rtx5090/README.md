# RTX 5090 (32GB GDDR7)

Fast small-model box: great for the "fast" tier or a speculative-decoding
mini instance. Blackwell (sm_120) needs a recent CUDA 12.8+ vLLM image.

```bash
docker run -d --name vllm --restart unless-stopped \
  --gpus all --network host --ipc host \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  vllm/vllm-openai:cu129-nightly \
  --model Qwen/Qwen3-14B-Instruct \
  --max-model-len 32768 --gpu-memory-utilization 0.90
```

Candidates that fit 32GB with room for KV: Qwen3-14B, gpt-oss-20b (MXFP4),
Qwen3-30B-A3B-AWQ (tight). Verify sm_120 images work before committing it as
a team-facing instance; otherwise use it as the bench rig.
