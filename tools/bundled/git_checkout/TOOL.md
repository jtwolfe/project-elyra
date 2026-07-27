---
name: git_checkout
description: Checkout a branch/ref (optional create -b) in a path-jailed repo.
kind: mutate
---

# git_checkout

Checkout a branch/ref (optional create -b) in a path-jailed repo.

Host builtin — path jail for git tools; GH_TOKEN from secrets inject for gh tools
(soft-fail ``auth_unavailable`` when missing). No shell; argv only.
