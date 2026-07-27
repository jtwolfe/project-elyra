---
name: gh_pr_list
description: List pull requests via gh. Soft-fails without GH_TOKEN.
kind: read
---

# gh_pr_list

List pull requests via gh. Soft-fails without GH_TOKEN.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
