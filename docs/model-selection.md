# Model selection

One entry per platform; update when a benchmark picks a winner. Keep
`catalog.yaml` in sync.

## DGX Spark (128GB unified, ~273GB/s)

| Tier | Candidates | Why |
|------|-----------|-----|
| smart | Qwen3.8-27B (current), Qwen3-32B-AWQ, gpt-oss-120b (MXFP4) | fits in 128GB; quality ceiling |
| fast | Qwen3-30B-A3B (MoE, ~3B active), Qwen3-14B, gpt-oss-20b | MoE active params beat BW wall |
| balanced | Qwen3-32B-AWQ, gpt-oss-20b | middle of both gates |

gpt-oss-120b is the headline Spark play: MXFP4 fits ~61GB and NVIDIA tuned
it for GB10. If it clears the 10 tok/s smart gate, it *is* the smart model.

## Strix Halo (128GB unified, ~256GB/s)

Same bandwidth class as Spark: Qwen3-30B-A3B (fast), Qwen3.8-27B 4-bit
(balanced - already installed, keep if competitive).

## OpenShift g6e (48GB L40S, up to 4x)

| Tier | Candidates | Why |
|------|-----------|-----|
| long-context | Qwen3.8-27B @ 262k (deployments/openshift/qwen38-27b.yaml) | already specced; multi-user via replicas |
| smart (4x L40S) | Llama-3.3-70B-AWQ, Qwen3-32B BF16 | 192GB VRAM across the node |
| fast | Qwen3-30B-A3B FP8 | cheap per-token on datacenter cards |

## RTX 5090 (32GB)

Qwen3-14B / gpt-oss-20b as a second fast endpoint or bench rig.

## Quality eval protocol (the "smart" half)

Fixed 30-prompt set (10 code, 10 summarize/reason, 10 agentic/tool-call),
scored by two team members blind, plus tool-call format pass/fail. Run on
every candidate before it enters `catalog.yaml` as `running`.
