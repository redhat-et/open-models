# Strix Halo (Ryzen AI Max+, 128GB unified, RDNA3.5 iGPU)

Bandwidth class similar to DGX Spark. Current qwen3.8 install (verify) is
probably llama.cpp or LM Studio - keep whatever works and put vLLM ROCm as a
stretch goal.

- llama.cpp `--server` exposes OpenAI-compatible `/v1/chat/completions` and
  (recent builds) `/v1/models`; front-end probe expects both plus `/health`
  - if `/health` is missing, add `health_path` to the model entry in
  `catalog.yaml` and teach `probe_model` to use `models_path` only.
- gfx1151 support is maturing in ROCm 6.4+ / Vulkan backend; test
  Qwen3-30B-A3B (fast) and a 4-bit 32B (balanced).
- Keep the existing model if it beats our candidates on the same prompts.
