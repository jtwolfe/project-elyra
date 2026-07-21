# Inference — llama.cpp (Vulkan) + Gemma 4

Port from **project-elyra2**. Do not invent a new stack.

## Model files (leave in elyra2)

| File | Role |
|------|------|
| `Gemma-4-12B-OBLITERATED-Q4_K_M.gguf` | Weights |
| `mmproj-BF16.gguf` | Multimodal projector |
| `llama.cpp/llama-server` | Server binary (+ `libggml-vulkan.so`) |

Setup when implementing (weights stay put):

```bash
# from project-elyra root
ln -s ../aurimago/project-elyra2/model model
```

## What elyra2 `elyra start` does today

| Setting | Value | Meaning |
|---------|-------|---------|
| `-c` | **86000** | Server **KV ceiling** (allocated window) |
| Input assembly max | **~69616** | `86000 − 16384` — code cap, not always filled |
| Linear memory default | **~16000 SAFE** | Product default; large linear prefills crashed 16 GiB AMD |
| Many step `max_tokens` | **4096** | Not 16k every step |
| `-np` | **1** | One parallel slot |
| `-ngl` | **99** | Offload (Vulkan backend) |
| `--jinja` / `--reasoning on` | yes | Chat template + reasoning |

**Important:** starting with `-c 86000` does **not** mean every prompt is 86k tokens. Crashes were mostly large prefills / VRAM, not “full context successfully used.”

## Stretch 1 defaults

```text
llama-server
  -m …/Gemma-4-12B-OBLITERATED-Q4_K_M.gguf
  --mmproj …/mmproj-BF16.gguf
  --embedding --jinja --pooling mean
  -c <ceiling>          # 86000 max; lower if unstable (e.g. 32k–48k)
  -b 2048 -ub 2048
  -ngl 99 -np 1
  --cache-ram 0
  --host 127.0.0.1 --port 8080
  --no-webui --threads 4
  --reasoning on --reasoning-format auto
  # no global --reasoning-budget unless needed
```

| Policy | Preference |
|--------|------------|
| Input | Sliding window — **well under** `-c`; never pack full KV by default |
| Generation | **Generous** when stable (do not starve tool loops); can use high max_tokens |
| Stability | One HTTP call at a time (chat or embed); drop `-c` before inventing new stacks |

Client: `http://127.0.0.1:8080/v1/chat/completions` (tools + reasoning). Long read timeout (~600s). Temperature ~0.2 until tuned.

## Non-goals

New quants, multi-slot server, cloud default, elyra2 step-profile zoo.
