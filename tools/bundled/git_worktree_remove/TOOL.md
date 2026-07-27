---
name: git_worktree_remove
description: Remove a git worktree. Dirty trees require confirm=true.
kind: mutate
---

# git_worktree_remove

Remove a git worktree. Dirty trees require confirm=true.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
