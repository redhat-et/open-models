# Hardware inventory

| Host | Hardware | Memory | Serve notes | Status |
|------|----------|--------|-------------|--------|
| spark1 | DGX Spark (GB10) | 128GB unified | one model/box; BW-limited | free candidate |
| spark2 | DGX Spark (GB10) | 128GB unified | re-benchmark current qwen3.8 | running something |
| spark3 | DGX Spark (GB10) | 128GB unified | re-benchmark current qwen3.8 | running something |
| spark4 | DGX Spark (GB10) | 128GB unified | | free candidate |
| strix-halo | Ryzen AI Max+ (gfx1151) | 128GB unified | llama.cpp today (verify) | go grab it |
| rtx5090 | RTX 5090 | 32GB VRAM | needs CUDA 12.8+ images (sm_120) | available |
| openshift | g6e.12xlarge (1+) | 4x L40S 48GB | vLLM TP=4; Ryan Cook's cluster is long-term | Models Arch cluster prepped |

## Candidates to scavenge / borrow / beg

- [ ] spark4 + the Strix Halo (physically)
- [ ] Old workstation GPU pool (any Pascal+ box can host a 7-8B "CPU-adjacent" tier)
- [ ] Ryan Cook: dedicated time slice or namespace on the g6e cluster
- [ ] Retired servers with >64GB RAM for llama.cpp CPU serving (see below)
- [ ] Cloud free-tier / dev sandbox GPU hours as burst capacity

## CPU serving - is it viable?

Yes for a low-quality bar: llama.cpp with Qwen3-8B/14B GGUF on a 16+ core,
64GB box gets 5-15 tok/s single stream (bandwidth-bound). Useful as an
overflow/"last resort" endpoint, not for interactive coding. Decision: try
once we have everything else live, on whatever 64GB+ box we scavenge.
