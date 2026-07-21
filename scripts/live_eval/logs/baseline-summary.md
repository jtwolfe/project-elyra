# Stage 0 baseline summary (committed)

Full narrative: [stage-0.md](stage-0.md). Scorecards: `scorecard-stage-0_*.md`.

**Knobs:** temp=0.2, top_p/top_k omitted, no thinking budget.  
**Path:** product (isolated ELYRA_HOME, POST /api/messages, real Gemma).  
**Date:** 2026-07-21.

| Attempt | flood | tools | speak | free_text | latency_s | feel | stop |
|---------|-------|-------|-------|-----------|-----------|------|------|
| S-social/1 | Y | speak | Y | N | 279 | 1 | no_tools |
| S-social/2 | Y | speak | Y | N | 280 | 1 | no_tools |
| S-social/3 | Y | speak | Y | N | 278 | 1 | no_tools |
| S-tools/1 | N | list_dir,speak | Y | N | 15 | 5 | no_tools |
| S-tools/2 | N | list_dir,speak | Y | N | 12 | 5 | no_tools |
| S-tools/3 | N | list_dir,speak | Y | N | 9 | 5 | no_tools |
| S-mono/1 | N | list_dir,speak | Y | N | 586 | 4 | no_tools |
| S-mono/2 | N | list_dir,speak | Y | N | 320 | 4 | no_tools |
| S-mono/3 | N | list_dir,run,run,speak | Y | N | 600 | 4 | no_tools |

| Dimension | S-social | S-tools | S-mono |
|-----------|----------|---------|--------|
| (A) no flood | 0/3 | 3/3 | 3/3 |
| (B) tool_calls | 3/3 | 3/3 | 3/3 |
| (B) glass speak | 3/3 | 3/3 | 3/3 |

**Headline:** Failure (A) is **deterministic on S-social hop-2** (pure `<|channel>thought` content flood, ~4k markers). (B) is healthy without tool_choice pins on this baseline. Ship harness; advance sampling / hygiene tracks for (A).
