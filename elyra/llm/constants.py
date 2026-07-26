"""Token budgets for meal assembly and generation headroom.

Scope: context window ceiling documentation, generation reserve, input budget
math used by sliding-window policy.
Out of scope: per-skill caps, provider process knobs.
"""

from __future__ import annotations

# Product KV/context ceiling (documentation + meal-budget math). Not a process
# launch flag — sliding meals use DEFAULT_SLIDING_INPUT_TOKENS in practice.
CONTEXT_WINDOW_TOKENS = 86_000

# Output reserve / default max_tokens headroom for tool do-loops.
GENERATION_RESERVED_TOKENS = 16_384
GENERATION_MAX_TOKENS = GENERATION_RESERVED_TOKENS

# Max input assembly if we enforce a hard cap (window minus reserve).
CONTEXT_BUDGET_TOKENS = CONTEXT_WINDOW_TOKENS - GENERATION_RESERVED_TOKENS

# Prefer sliding meals well under this in practice (VRAM / stability).
DEFAULT_SLIDING_INPUT_TOKENS = 24_000

# Chat temperature product default for local OpenAI-compat factory defaults.
DEFAULT_CHAT_TEMPERATURE = 1.0
