---
name: gh_pr_create
description: Create a pull request via gh. Soft-fails without GH_TOKEN.
kind: mutate
---

# gh_pr_create

Create a pull request via gh. Soft-fails without GH_TOKEN.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
