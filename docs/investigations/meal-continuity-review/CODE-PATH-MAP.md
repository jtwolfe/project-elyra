# CODE-PATH-MAP — confirmed call graphs (BUG-meal-03 review)

Symbols and approximate line numbers from worktree SHA `7ebf50b` (2026-07-30).

---

## 1. Memory vs legacy outer branch

```
PresenceWorker._run_moment
  └─ rebuild_outer()                          worker.py:1976
       ├─ glass = list_messages(limit=80)     worker.py:1991  (always loaded)
       ├─ skill_bias = format_skill_bias(…)   worker.py:2017 → orient_slice.py:97
       ├─ use_memory_meal = _memory_meal_active()  worker.py:2032 / 1382
       │     requires settings.memory.enabled + store.health().ok
       ├─ [memory path]
       │     fill_orient(… why_now=why, skill_bias=…)  worker.py:2045–2054
       │     dk_ids = _last_confirmed_keep_for_meal(moment_id)  worker.py:2074–2076
       │     compose_meal(… open_moment_id=moment_id, directed_keep_ids=dk_ids)  meal.py:1499
       │     compose_outer_messages(… package)  meal.py:1709
       │     expand_memory_meal_for_provider(… wake_message_id, glass_by_id)  meal.py:1915
       │         └─ _inject_hybrid_wake_row if id missing  meal.py:1816
       │     strip_meal_wire_fields → Completions
       └─ [legacy path]
             assemble_outer_meal(glass_history=glass, …)  context.py:251
             expand_meal_for_provider → strip
```

**Confirmed:** memory path never passes full glass into `compose_meal`. Glass is only for `index_glass` / hybrid inject / media. Message order on memory path:

```text
system → episodic → semantic → directed_keep → temporal → orient
(+ optional single hybrid glass wake row before orient)
```

Rendered in `compose_outer_messages` (`meal.py:1746–1756`). Channel items via `meal_item_to_message` (`meal.py:1695`) using `item.role` default **`user`** from `_item_from_parts` (`meal.py:162–170`).

---

## 2. wait_reply social path (rockets class)

```
POST /api/messages (phase=waiting)
  → _apply_wait_reply_unlocked                 worker.py:2824
       payload = {user_id, content, message_id, wait_id?}
       enqueue("wait_reply", payload)
  → claim → _claim_and_open                    worker.py:1867
       why = _why_now(wake)                    worker.py:178
         wait_reply → "wait reply (wait_id=…)"  **no user text**  L185–186
       open_moment(why_now=why)
       _promote_social_wake_unlocked           worker.py:1825
         → promote_wake_observation            promote.py:972
              kind=observation, meta.wake_message_id=message_id
  → rebuild_outer (memory)                     as §1
  → run_do_loop(outer_prefix=…)                doloop.py:652
```

**Content carriers for user text on hop 0:**

| Carrier | Holds rockets text? | Role shape |
|---------|---------------------|------------|
| `_why_now` | **No** | orient string wait_id only |
| Temporal open-moment obs | **Yes** (if promote + write_atoms) | `[hhmm] (observation) …` under one user host block |
| Hybrid inject | Only if wake id **missing** from meal | single glass row with true role |
| Glass-tail band | **Does not exist** | — |
| Prior glass assistant | **Not in outer** | — |
| Wait prompt *Anything else?* | waits.json / prior tape only | not glass |

---

## 3. Interject contrast (P4)

```
POST /api/messages (phase=in_moment)
  → InterjectBuffer.try_add                    presence/interject.py
  → do_loop safe point:
       _drain_interjections(chain, drain, …)   doloop.py:615
         chain.append(_obs_user_message(text))
         promote beat observation
  Outer meal: **not** rebuilt solely for interject
  After moment close leftover:
       _flush_interjects_as_wakes_unlocked → user_message (new moment = P1)
```

Continuity rides the **in-turn chain**. Rockets-class failure requires new-moment outer rebuild (wait_reply / idle user_message), not interject drain timing.

---

## 4. Directed keep dual kill switches

```
memory_traverse finish confirm
  → TraversalRegistry._last_confirmed_keep = ConfirmedKeepSnapshot(moment_id=A, …)

compose (next rebuild_outer):
  _last_confirmed_keep_for_meal(open_moment_id=B)   worker.py:1500–1517
    → get_last_confirmed_keep(B)                  traverse.py:534–541
         if snap.moment_id not in (None, B): return None   **B5b meal-wire**

moment finalize:
  _close_traversal_for_moment(moment_id)          worker.py:1491 / ~2249
    → on_moment_close(moment_id)                  traverse.py:1106–1120
         clears last_confirmed_keep               **B5 wipe**
         also clears last_session (glass KD-A19 — separate policy)
```

**Confirmed hermetic:** snap under `moment_A` → `get_last_confirmed_keep("moment_B") is None`; after `on_moment_close("moment_A")` keep is gone entirely.

Meal only packs **confirmed** keep (`directed_keep_ids` from last_confirmed, not active provisional walk). Flag gate: `is_directed_keep_enabled` / `directed_keep_enabled` + non-empty ids (KD-A7).

---

## 5. Semantic seed & budget

```
build_semantic_query_seed(open_moment_atoms)      meal.py:898
  kinds = observation | speak | model only        meal.py:68 _SEMANTIC_SEED_KINDS
select_semantic → omit empty_seed | encoder | no_index | timeout | …

split_memory_budget_v3                            tokens.py:127
  residual R → semantic / directed_keep / episodic / temporal floor
  cut under floor: semantic → dk → episodic (never invent glass-tail floor)

effective_meal_budget_tokens                      meal_budget.py:113
  fraction × model window (#91) — enlarges R only; no tip channel
```

---

## 6. Framing amplifiers (B12)

```
format_skill_bias("wait_reply", …)                orient_slice.py:122–123
  → BIAS_TALK = "Prefer skill: talk (social reply first; speak before wait)."
fill_orient(… why_now, skill_bias)                used in rebuild_outer memory path
```

Orient ends the outer meal as a **user** role block. Combined with wait_id-only why_now, this is a strong wait-ceremony attractor independent of whether temporal holds the user question string.

---

## 7. Legacy control

```
assemble_outer_meal                               context.py:251
  system → _glass_to_history (user/assistant roles preserved) → orient
  wake protect by id/content
```

When `_memory_meal_active()` is false, glass **is** the tip. Do not regress this path.
