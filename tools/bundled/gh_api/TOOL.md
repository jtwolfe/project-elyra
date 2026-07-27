---
name: gh_api
description: Call GitHub API via gh api (escape hatch). Soft-fails without GH_TOKEN.
kind: mutate
---

# gh_api

Call GitHub API via gh api (escape hatch). Soft-fails without GH_TOKEN.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
