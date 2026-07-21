---
name: search_replace
description: Replace text in a sandbox file. old must be non-empty; count 0 means all.
kind: mutate
---

# search_replace

Literal substring replace in one sandbox file (atomic write). Path is jailed;
escapes return a tool error.
