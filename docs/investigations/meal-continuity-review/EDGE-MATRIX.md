# EDGE-MATRIX — continuity path results (BUG-meal-03)

Scoring uses product draft / review-plan **P2 tip split**:

- **Must:** user answer text + recent assistant **glass** social turns in outer with dialogue shape
- **Nice:** wait prompt string when it exists only in wait state (not on glass)

**Live column:** host `http://127.0.0.1:8787` was **down** during this review → all live cells **skipped**.

| ID | Path | Entry symbols | Expected tip (must / nice) | Static mechanism (observed) | Tip OK? (static) | Offline / live |
|----|------|---------------|----------------------------|----------------------------|------------------|----------------|
| **P1** | Idle → `user_message` | `enqueue` → claim → `_promote_social_wake_unlocked` → `rebuild_outer` | **Must:** user text in outer with clear social shape | Same memory outer as P2: temporal obs + hybrid; **no glass-tail**; prior assistant glass absent; roles host-user | **Partial** — text may be present as obs; dialogue shape fail | Live skipped |
| **P2** | Waiting → `wait_reply` | `_apply_wait_reply_unlocked` → claim | **Must:** user answer + recent assistant glass turns. **Nice:** wait prompt | Rockets class: why_now wait_id only; temporal obs carries rockets under role=user; hybrid skipped when id present; 0 assistant roles; epi can dominate; BIAS_TALK framing | **Fail** (must: assistant glass missing; shape weak) | Offline SA-9b ✅; live skipped |
| **P3** | Waiting → `wait_timeout` | timers → `wait_timeout` | **Must:** recent social glass tip | `_why_now` = wait timeout id only; same no glass-tail meal; #68 adjacency | **Likely fail** same structural B1/B12 | Live skipped |
| **P4** | `in_moment` → interject | `interject` → `_drain_interjections` | Chain gets user text; outer unchanged | `doloop._drain_interjections` appends user obs to **chain**; outer not rebuilt for interject; remainder → `user_message` after close | **Pass** for rockets-class (different path) | Live skipped |
| **P5** | ends_moment + wait → later reply | same as P2 after wait arm | Same must as P2 across moment boundary | Prior moment speak only on glass/tape; new moment temporal starts empty except wake obs; **B5 wipe + B5b** kill keep across boundary | **Fail** (same as P2 + keep dead) | Offline structural ✅; live skipped |
| **P6** | `moment_continue` / `task_ready` | continuous enqueue | Work path must not erase pending social tip | Work why_now + skill bias do/plan; no glass-tail; social tip not reconstituted | **At risk** | optional / skipped |
| **P7** | Restart mid-wait | process restart → wait_reply | Glass from disk; tray load | Glass disk-backed but unused as tip by memory meal; keep RAM-only; `_last_meal_snapshot` RAM-only; thin Lance load adjacency | **Fail** structural B9 | Live skipped |
| **P8** | Restart idle | next social | Instance memory of last chat | Same as P7 without wait | **Fail** structural B9 | optional / skipped |
| **P9** | Long tool moment + chain pressure | `enforce_in_turn_budget` | Outer tip intact; chain may compress | Re-outer calls same `rebuild_outer` (still no glass-tail); chain compress can drop early social in-turn | **At risk** B8 | synthetic not run |

---

## P2 rockets detail (primary)

| Surface | Value |
|---------|-------|
| Moment | `e6d460f2-4087-42cd-870f-d34a89b6feaf` |
| why_now | `wait reply (wait_id=c13ae60a-40ed-45c6-a75a-035c1a78f05c)` |
| Glass user | `04f85fc6-195a-4b3c-b0bf-8b307c7baa2f` rockets question |
| Glass assistant | `37ec1721-…` *Not much else hanging — last open threads were the philosophy pack and fabric report…* |
| Prior glass assistant | `436f4ca1-…` time speak (must-have tip material — **missing from outer**) |
| Wait prompt surface | `waits.json` + prior moment `de87355d-…` `wait_user` tool — *Anything else?* (**nice-to-have**; not P2 primary fail alone) |
| Tape reasoning | *The user is replying to my wait after I told them the time. The wait prompt was "Anything else?"…* |
| Offline recompose | `evidence/sa9b-e6d460f2/` — carrier ranking B12 → B11 → B3 → B7 → B1 |

### Offline recompose summary (hop 0)

| Check | Result |
|-------|--------|
| Rockets in temporal | Yes — `[08:47] (observation) what is the coolest thing…` role=`user` |
| Prior assistant glass in outer | **No** |
| Assistant role count (channel items) | **0** |
| Hybrid inject | **Skipped** (wake_message_id already on temporal meta) |
| why_now has user text | **No** |
| skill_bias | `Prefer skill: talk (social reply first; speak before wait).` |
| Glass-tail band | **Absent** |
| Wait prompt in outer | **No** |
| Hermetic pressure epi:temp | ~981 : 27 tokens — closed-work mass ≫ tip |

---

## Fault bucket coverage by path

| Bucket | P1 | P2 | P4 | P5 | P7 |
|--------|----|----|----|----|-----|
| B1 missing_glass_tail | ● | ● | ○ (N/A outer) | ● | ● |
| B2 path_asymmetry | ● vs P4 | ● | contrast | ● | |
| B3 role_collapse | ● | ● | chain uses user obs | ● | ● |
| B4 why_now_without_content | short ok | ● | | ● | ● |
| B5 keep wipe | | | | ● | ● |
| B5b keep meal-wire | | | | ● | ● |
| B6 semantic seed/lag | optional | optional | | | |
| B7 hybrid_only | ● | ● | | ● | ● |
| B8 in_turn_vs_outer | | | drain | | | reouter |
| B9 restart_hydration | | | | | ● |
| B11 epi_outranks_tip | ● | ● | | ● | ● |
| B12 framing_bias | social bias | ● wait | | ● | ● |

B10 dual/triple copy: **prospective post-S1 only** (not present-day EDGE hunt).
