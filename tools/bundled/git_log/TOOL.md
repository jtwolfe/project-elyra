---
name: git_log
description: Show git log (optional max_count/oneline) for a path-jailed repo.
kind: read
---

# git_log

Show git log (optional max_count/oneline) for a path-jailed repo.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
