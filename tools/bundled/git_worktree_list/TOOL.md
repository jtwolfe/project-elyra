---
name: git_worktree_list
description: List git worktrees for a path-jailed repo.
kind: read
---

# git_worktree_list

List git worktrees for a path-jailed repo.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
