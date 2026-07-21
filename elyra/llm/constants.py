"""Token budgets for llama-server and clients.

Scope: context window and generation headroom.
In scope: -c ceiling, generation reserve, input budget math.
Out of scope: sliding-window policy, per-skill caps.
"""

from __future__ import annotations

# llama-server -c — KV ceiling (not every-prompt fill size).
CONTEXT_WINDOW_TOKENS = 86_000

# Output reserve / default max_tokens headroom for tool do-loops.
GENERATION_RESERVED_TOKENS = 16_384
GENERATION_MAX_TOKENS = GENERATION_RESERVED_TOKENS

# Max input assembly if we enforce a hard cap (window minus reserve).
CONTEXT_BUDGET_TOKENS = CONTEXT_WINDOW_TOKENS - GENERATION_RESERVED_TOKENS

# Prefer sliding meals well under this in practice (VRAM / stability).
DEFAULT_SLIDING_INPUT_TOKENS = 24_000

# Gemma card nucleus / top-k truncation (product defaults ship in a later PR).
GEMMA_TOP_P = 0.95
GEMMA_TOP_K = 64
