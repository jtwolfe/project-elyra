---
name: git_diff
description: Show git diff (optional staged/paths/ref) for a path-jailed repo.
kind: read
---

# git_diff

Show git diff (optional staged/paths/ref) for a path-jailed repo.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
