---
name: gh_auth_status
description: Show gh auth status. Soft-fails auth_unavailable without gh_token grant.
kind: read
---

# gh_auth_status

Show gh auth status. Soft-fails auth_unavailable without gh_token grant.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
