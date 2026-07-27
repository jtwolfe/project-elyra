---
name: git_worktree_prune
description: Prune stale git worktree registrations.
kind: mutate
---

# git_worktree_prune

Prune stale git worktree registrations.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
