---
name: git_commit
description: Create a git commit with a message in a path-jailed repo.
kind: mutate
---

# git_commit

Create a git commit with a message in a path-jailed repo.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
