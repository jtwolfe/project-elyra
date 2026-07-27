---
name: gh_issue_create
description: Create an issue via gh.
kind: mutate
---

# gh_issue_create

Create an issue via gh.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
