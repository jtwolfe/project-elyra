---
name: git_worktree_add
description: Add a git worktree at a path inside the VCS jail.
kind: mutate
---

# git_worktree_add

Add a git worktree at a path inside the VCS jail.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
