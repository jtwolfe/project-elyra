---
name: gh_project_item_add
description: Add an issue/PR URL to a GitHub Project.
kind: mutate
---

# gh_project_item_add

Add an issue/PR URL to a GitHub Project.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
