---
name: git_add
description: Stage paths with git add in a path-jailed repo.
kind: mutate
---

# git_add

Stage paths with git add in a path-jailed repo.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
