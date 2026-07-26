---
name: promote_identity
description: Promote identity draft to current under host gates (self needs grant; user needs social context).
kind: mutate
---

# promote_identity

Promote draft → current after host gates pass. Archives previous current into versions/.

- Required: `actor`, `reason`
- When `actor=user`: `user_id` (target profile) must match session user on the model path
- When `actor=self`: `grant_token` required (operator one-time grant; prefer Glass Promote button)
- Optional: `expected_draft_sha256` optimistic lock

Self: hard gate (grant + reason ≥ 8). User: medium gate (social wake + reason ≥ 4 +
session user match). Draft never injects until this succeeds.
