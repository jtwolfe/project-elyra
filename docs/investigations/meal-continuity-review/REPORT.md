# Meal continuity review report — BUG-meal-03 findings

| Field | Value |
|-------|--------|
| **Document** | Fault isolation report (inspection only; no product meal behavior changes) |
| **Issue** | [#93](https://github.com/jtwolfe/project-elyra/issues/93) — `BUG-meal-03` |
| **Review plan** | [`../../design/memory/design-meal-formation-continuity-review-plan.md`](../../design/memory/design-meal-formation-continuity-review-plan.md) |
| **Product draft** | [`../../design/memory/design-instance-continuity-glass-tail-directed-keep.md`](../../design/memory/design-instance-continuity-glass-tail-directed-keep.md) |
| **Date** | 2026-07-30 |
| **Worktree code** | SHA `7ebf50b` (symbols/lines as read) |
| **Dogfood data** | `/home/jim/Workspace/project-elyra/data` (read-only) |
| **Live E-P2** | **Skipped** — `http://127.0.0.1:8787` not available |
| **WP coverage** | WP1 static SA-1…SA-9 ✅ · WP2 SA-9b offline recompose ✅ · WP3 live ⏭ |

Supporting maps: [`CODE-PATH-MAP.md`](CODE-PATH-MAP.md) · [`EDGE-MATRIX.md`](EDGE-MATRIX.md) · [`evidence/sa9b-e6d460f2/`](evidence/sa9b-e6d460f2/)

---

## 1. Executive summary

### Dogfood (Prince Rupert tip smash — rockets class)

On 2026-07-30 the glass log showed a normal Q→A:

1. Assistant: time speak (*It’s Thursday 30 July 2026…*) then armed `wait_user` with prompt *Anything else?* (prompt lives in `waits.json` / prior tape `de87355d-…`, **not** as a glass row).
2. User: *what is the coolest thing you remember about rockets?* (`04f85fc6-…`).
3. Assistant: *Not much else hanging — last open threads were the philosophy pack and fabric report, both closed…* (`37ec1721-…`, moment `e6d460f2-…`).

Model reasoning on hop 1 framed a **wait ceremony** (*replying to my wait after I told them the time… wait prompt was "Anything else?"*) and never addressed rockets. Glass looked correct; the **outer Completions package** did not present a dialogue-shaped tip that could outrank wait-ceremony framing and closed-work episodic vibe.

### Primary fault chain (not B1 alone)

Two rankings are used deliberately — do not merge them:

| Label | Order | Meaning |
|-------|-------|---------|
| **Empirical recompose ranking** (SA-9b hop-0 carriers) | **B12 → B11 → B3 → B7 → B1** | What the offline meal frame + tape suggest outranked the tip *in the failure instance* (mass/framing first; structural missing band last as “absence”) |
| **Normative fix / structural priority** (implement S-order) | **B12 + B1 co-primary → B3 → B7 → B11** (under tip floor) | What to ship first: framing dual-write + glass-tail channel; then role fidelity; hybrid stays scoped; epi mass handled by tip floor once a tip channel exists |

#### Normative fix priority (for S1–S3)

| Rank | Bucket | Mechanism | Role in rockets failure |
|------|--------|-----------|-------------------------|
| 1 | **B12** framing_bias | `_why_now("wait_reply")` = wait_id only + `format_skill_bias` → `BIAS_TALK` | Matches tape reasoning; wait ceremony attractor |
| 1 (co) | **B1** missing_glass_tail | Memory meal has **no** sliding glass / glass-tail band | Prior assistant social turns never enter outer — primary **missing channel** fix |
| 3 | **B3** role_collapse | Meal items default `role: user` host blocks; `format_atom_line` kind tags | Rockets text, if present, is not Q→A dialogue |
| 4 | **B7** hybrid_wake_only | Hybrid injects **one** glass row only when id missing; skipped when temporal already has `wake_message_id` | No assistant prior; often no extra glass row at all |
| 5 | **B11** epi_outranks_tip | Channel order + token mass; no tip floor / precedence | Closed-work speak can dominate thin tip once tip band exists, protect with floor |
| — | **B4** why_now_without_content | Content-carrier gap for orient | Amplifies B12 |
| — | **B2** path_asymmetry | wait_reply → new moment outer rebuild vs interject → chain | Explains why interject “never” fails this way |
| — | **B5 + B5b** | Moment-end wipe + `get_last_confirmed_keep(open_id)` moment filter | Sticky keep cannot survive moment boundary even if wipe removed alone |

Empirical order matches `evidence/sa9b-e6d460f2/recompose_meal.json` `carrier_ranking`, `notes.md`, and EDGE-MATRIX P2 (B12→B11→B3→B7→B1). Elevating **B1** in the normative table is fix-priority (missing channel / S1), **not** a re-statement of the empirical recompose rank.

**B12 framing (tail-only vs tail+orient):** Offline recompose + tape show that **glass-tail alone is the correct primary fix (S1)** for must-have tip (user Q + prior assistant glass), but **why_now / orient snippet remains a recommended dual-write (S2 option)** because the model’s reasoning tracked wait ceremony language that is strongest in orient. Do **not** ship prompt-only soft recall (A5) as S1.

### What was proven offline

- Structural B1/B4: static SA-1/SA-2 (confirmed).
- Effective tip missing / role collapse / hybrid / framing: SA-9b recompose + tape (confirmed for rockets class).
- End-to-end speak under current live host: **not re-verified** (API down). Historical tape remains the behavioral ground truth for e6d460f2.

### Recommendation package (for implement plan)

1. **S1 glass-tail** with role fidelity + tip floor under pressure (before sticky keep).
2. **S2 path parity**: P2/P5 tests; optional **why_now user snippet** dual-write; hybrid dedupe vs tail (B10 risk).
3. **S3 sticky keep**: stop wipe **and** instance tray without `snap.moment_id == open_moment_id` filter (B5+B5b).
4. Semantic seed from glass-tail last user (OQ8) after tip exists.

---

## 2. CODE-PATH-MAP

See [`CODE-PATH-MAP.md`](CODE-PATH-MAP.md) for full graphs. Condensed:

| Path | Key symbols |
|------|-------------|
| Memory outer | `PresenceWorker.rebuild_outer` `worker.py:1976` → `_memory_meal_active:1382` → `compose_meal` / `compose_outer_messages` `meal.py:1499/1709` → `expand_memory_meal_for_provider` |
| wait_reply | `_apply_wait_reply_unlocked:2824` → `_why_now:178` → `_promote_social_wake_unlocked:1825` → `promote_wake_observation` `promote.py:972` |
| Interject | `_drain_interjections` `doloop.py:615` (chain only) |
| Keep dual kill | `get_last_confirmed_keep` `traverse.py:534` **B5b**; `on_moment_close:1106` **B5**; wired via `_last_confirmed_keep_for_meal:1500` + `_close_traversal_for_moment:1491` |
| Framing | `format_skill_bias` `orient_slice.py:97` → `BIAS_TALK` L17/L122–123 |
| Budget | `split_memory_budget_v3` `tokens.py:127`; `effective_meal_budget_tokens` `meal_budget.py:113` |
| Legacy control | `assemble_outer_meal` `context.py:251` (sliding glass roles) |

---

## 3. EDGE-MATRIX results

See [`EDGE-MATRIX.md`](EDGE-MATRIX.md). Minimum exit set:

| Path | Static tip verdict |
|------|--------------------|
| **P1** user_message | Partial — text may land as temporal obs; no glass dialogue tip |
| **P2** wait_reply | **Fail** — rockets class (primary) |
| **P4** interject | **Pass** for this failure class (chain path) |
| **P5** wait bridge | **Fail** — P2 + B5/B5b |

Live E-P* matrix: **skipped**.

---

## 4. Faults

### F-01 — Missing glass-tail band on memory outer

| Field | Value |
|-------|-------|
| **Bucket** | **B1** missing_glass_tail |
| **Severity** | **S0 / S1** — high dogfood blocker for instance continuity |
| **Path(s)** | P1, P2, P3, P5, P7, P8 |
| **Location** | `compose_outer_messages` `elyra/memory/meal.py:1709–1756` (order system→epi→sem→dk→temp→orient); `rebuild_outer` memory branch `worker.py:2032–2116` never feeds glass into compose; `list_messages` only for hybrid/media (`worker.py:1991`) |
| **Call graph** | claim → rebuild_outer → compose_meal → Completions (**no** `list_messages` rows as chat band) |
| **Nature** | Memory meal **by design** excludes sliding glass. UI glass remains correct; model outer lacks dialogue tip. Meal budget #91 enlarges residual **R** but does not create a tip channel. |
| **Evidence** | SA-1 static; SA-9b recompose messages show 0 assistant roles; prior time speak `436f4ca1-…` absent; tape fail speak. `evidence/sa9b-e6d460f2/`. |
| **Confidence** | **confirmed** (structural + offline recompose) |
| **Impact** | Model answers wait ceremony / closed work; user sees Q→A mismatch on glass. |
| **Related draft §** | §5 glass-tail; §4 invariant; S1 |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Glass-tail band (recommended)** | Select last K glass user/assistant rows; pack as band with true roles; order …→ glass_tail → temporal → orient | Restores dialogue tip; restart-safe from disk; matches draft | Dedup vs temporal/hybrid (B10 risk); budget floor needed | M |
| **B — Promote every glass row to atoms** | Spine-only continuity | Single store | Role collapse remains if still host user blocks; encode lag; cost | L |
| **C — Enlarge meal budget further** | Higher fraction | Cheap | Already tried (#91); no tip channel | S — **reject as primary** |
| **D — Prompt-only soft recall** | Orient copy nudge | Cheap | Papers over missing channels; B12 already has talk bias | S — **reject as S1** |

**Recommended:** **A**.  
**Maps to draft extension:** §5 placement + cut order + acceptance; lock OQ1/OQ2 defaults provisionally.

---

### F-02 — Framing bias: why_now wait-id + BIAS_TALK (B12)

| Field | Value |
|-------|-------|
| **Bucket** | **B12** framing_bias (+ **B4** carrier gap) |
| **Severity** | **S1** — co-primary with B1 for rockets reasoning shape |
| **Path(s)** | P2, P5 (wait_reply); related P3 wait_timeout |
| **Location** | `_why_now` `worker.py:185–186`; `format_skill_bias` `orient_slice.py:122–123` (`BIAS_TALK` L17); `fill_orient` in `rebuild_outer` `worker.py:2045–2054` |
| **Call graph** | wait_reply payload has `content` → open_moment why_now **drops** content → orient user block ends meal with wait_id + talk bias |
| **Nature** | User text exists on wake payload and (if promote) temporal, but **orient** presents ceremony-only why_now plus hard social skill bias. Tape reasoning tracks this framing more closely than the weak observation line. |
| **Evidence** | Index `why_now` exact match; orient recompose includes `Prefer skill: talk…` and wait_id line, **not** rockets; tape: *wait prompt was "Anything else?"*. Wait prompt surface map: waits.json only (nice-to-have). |
| **Confidence** | **confirmed** for framing presence; **likely** that framing is necessary co-factor with B1 (cannot A/B live without host) |
| **Impact** | Model treats hop as wait-status check rather than factual recall question. |
| **Related draft §** | §5.3 path rules; OQ7; S2 |

#### Fix evaluation (tail-only vs tail+orient)

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **T — Tail only** | Glass-tail supplies user Q + prior assistant; leave why_now as wait_id | Minimal orient churn; tail is SoT | Residual ceremony bias may still attract some models | M (with S1) |
| **T+O — Tail + why_now user snippet (recommended dual)** | e.g. `wait reply (wait_id=…): {snippet}` capped | Aligns orient with tip; matches tape failure mode | Dual-write; snippet policy | S on top of S1 |
| **T+W — Tail + wait-setup band** | Inject wait prompt from waits.json | Surfaces *Anything else?* | Prompt often not needed if Q answered; more surfaces | M |
| **O-only** | why_now snippet without glass-tail | Cheap | Still no prior assistant roles; **insufficient** | S — not enough |

**Recommended:** **S1 glass-tail (T) as primary**; ship **T+O why_now snippet as S2 optional dual-write** (OQ7 → “optional but recommended for wait_reply”). Do not treat B1 alone as complete rockets fix evaluation.

**Maps to draft extension:** OQ7 evidence note; S2 path parity bullet; acceptance may assert either tail alone *or* tail+snippet for hop-0 reasoning.

---

### F-03 — Role collapse on memory meal items

| Field | Value |
|-------|-------|
| **Bucket** | **B3** role_collapse |
| **Severity** | **S1** |
| **Path(s)** | All memory-meal social paths |
| **Location** | `_item_from_parts(..., role="user")` `meal.py:162–170`; `format_atom_line` `meal.py:138–142` → `[hhmm] (kind) body`; `meal_item_to_message` `meal.py:1695` |
| **Call graph** | select_* → MealItem role user → compose_outer_messages |
| **Nature** | Even when wake content is in temporal, model sees a **labeled host block**, not alternating user/assistant turns. Legacy `_glass_to_history` preserves roles. |
| **Evidence** | SA-9b: all meal item roles `user`; assistant_role_count=0; temporal preview `[08:47] (observation) what is the coolest thing you remember about rockets?` |
| **Confidence** | **confirmed** |
| **Impact** | Weak dialogue shape; model underweights user question vs orient/epi. |
| **Related draft §** | §5.1 roles; OQ6 glass-tail wins for social rows |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Glass-tail with true roles** | Prefer glass_tail role fidelity over temporal label for social rows | Fixes tip shape without rewriting all channels | Dedup rules | M |
| **B — Promote assistant speaks as role=assistant meal items** | Spine dialogue | Schema/channel inventiveness; media | L |

**Recommended:** **A** (OQ6 lock: glass-tail roles win).

---

### F-04 — Hybrid wake inject is single-row and often skipped

| Field | Value |
|-------|-------|
| **Bucket** | **B7** hybrid_wake_only |
| **Severity** | **S2** (secondary; does not create tip by itself) |
| **Path(s)** | P1, P2, P5 |
| **Location** | `_inject_hybrid_wake_row` `meal.py:1816–1839`; `expand_memory_meal_for_provider`; `_meal_has_wake_id` `meal.py:1802` |
| **Nature** | Hybrid never reintroduces full glass — only protected wake message when id missing. When promote stamps `wake_message_id` on temporal, inject is **skipped**. Never injects prior assistant. |
| **Evidence** | SA-9b: `wake_id_in_meal=true` → hybrid skipped; glass_by_id had prior assistant available but unused. |
| **Confidence** | **confirmed** |
| **Impact** | Hybrid is media/id correlation, not continuity tip. |
| **Related draft §** | §5.3 hybrid remains; dedupe with glass-tail |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Keep hybrid as media/id only; add glass-tail** | Clear separation | Avoids hybrid scope creep | Needs S1 | M |
| **B — Expand hybrid to last N glass rows** | Fast hack | Overlaps glass-tail; thrash risk | M — prefer true band |

**Recommended:** **A**.

---

### F-05 — Path asymmetry wait_reply vs interject

| Field | Value |
|-------|-------|
| **Bucket** | **B2** path_asymmetry_wait_vs_interject |
| **Severity** | **S1** (design-critical explanation) |
| **Path(s)** | P2 vs P4 |
| **Location** | wait: new moment + `rebuild_outer`; interject: `doloop._drain_interjections:615` chain append only |
| **Nature** | Interject rides an already-built outer world for the open moment. Wait_reply **must reconstitute** the world from meal channels. Failure mode differs: delay vs wrong world. |
| **Evidence** | Static SA-6; design plan sequence diagrams; no rockets-class on interject path by construction. |
| **Confidence** | **confirmed** |
| **Impact** | Operators mis-generalize “interject works so meal is fine.” |
| **Related draft §** | §2.3; §7 P2/P4 |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Path parity via glass-tail on all outer rebuilds** | Same tip invariant | Correct | Doesn't change interject | M |
| **B — Force interject-style chain across wait** | Continuity without outer tip | Breaks moment model / restart | L — reject |

**Recommended:** **A**.

---

### F-06 — Directed keep moment-end wipe (B5)

| Field | Value |
|-------|-------|
| **Bucket** | **B5** directed_keep_moment_end_wipe |
| **Severity** | **S1** for sticky keep goal; **S2** for rockets tip (tip failure not caused by missing keep) |
| **Path(s)** | P5, P7, any multi-moment work |
| **Location** | `TraversalRegistry.on_moment_close` `traverse.py:1106–1117`; `PresenceWorker._close_traversal_for_moment` `worker.py:1491` |
| **Nature** | Kill switch #1: confirmed keep cleared at moment end. |
| **Evidence** | Static SA-4; hermetic: after `on_moment_close("moment_A")` keep is None. Tests cover close hygiene in `test_memory_traverse.py` but not sticky tray. |
| **Confidence** | **confirmed** |
| **Impact** | Pins do not survive wait boundaries. |
| **Related draft §** | §6; OQ4; S3 |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Stop meal-relevant wipe; keep last_session clear (KD-A19)** | Sticky tray possible | Policy split | Need TTL/LRU | M |
| **B — Persist tray under data/runtime** | Restart survival | I/O | Schema | M |

**Recommended:** **A+B** in S3; **not** before S1 for rockets.

---

### F-07 — Directed keep meal-wire moment scope (B5b)

| Field | Value |
|-------|-------|
| **Bucket** | **B5b** directed_keep_meal_wire_moment_scope |
| **Severity** | **S1** for sticky keep — **removing wipe alone is insufficient** |
| **Path(s)** | P5 and any compose with open moment ≠ confirm moment |
| **Location** | `get_last_confirmed_keep` `traverse.py:540–541`; always called with **open** `moment_id` from `_last_confirmed_keep_for_meal` `worker.py:2074–2076` |
| **Nature** | Kill switch #2: `snap.moment_id not in (None, moment_id)` → `None`. Keep confirmed in moment A never packs into moment B mid-lifetime either. |
| **Evidence** | Hermetic: snap A → get(B) is None; SA-4 static. |
| **Confidence** | **confirmed** |
| **Impact** | Sticky keep design that only stops wipe still fails meal wire. |
| **Related draft §** | §6 on_compose instance tray; DRAFT-EXTENSIONS |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Instance tray read without moment_id equality** | Required for sticky | Must pair with TTL | Cross-moment pin policy | M |
| **B — Re-stamp snap.moment_id on each open** | Hack | Confusing semantics | S — reject |

**Recommended:** **A** (compose from instance tray; filter by age/tokens not open moment id).

---

### F-08 — Semantic empty seed / encode lag

| Field | Value |
|-------|-------|
| **Bucket** | **B6** semantic_empty_seed_or_lag |
| **Severity** | **S2** (not primary rockets; seed would include rockets obs text if semantic on + encoder warm) |
| **Path(s)** | P1–P2 when semantic_enabled |
| **Location** | `build_semantic_query_seed` `meal.py:898` — open-moment obs/speak/model only; omit reasons `SEMANTIC_OMIT_*` L72–78; KD12 warm-only embedder in rebuild_outer |
| **Nature** | Seed cannot see prior glass alone. Encode lag leaves new atoms unindexed. Default `MemorySettings.semantic_enabled` may be false in some dogfood configs. |
| **Evidence** | Static SA-5; SA-9b ran with semantic off (defaults / flag). No live queue status. |
| **Confidence** | **confirmed** structural; lag **hypothesis** without live encode metrics |
| **Impact** | “What do you remember about X?” underserved without tip/keep. |
| **Related draft §** | OQ8 seed from glass-tail last user |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Seed from glass-tail last user when social** | Better ANN after S1 | Depends on tail | S after S1 |
| **B — Wait for encode on social hops** | Fresher vectors | Latency | existing wait toggle |

**Recommended:** **A** after glass-tail lands.

---

### F-09 — Episodic mass outranks tip

| Field | Value |
|-------|-------|
| **Bucket** | **B11** epi_outranks_tip |
| **Severity** | **S1** under realistic bulb; **S2** when store thin |
| **Path(s)** | P2 rockets; any social with rich epi |
| **Location** | `compose_meal` order KD-A8 `meal.py:1643–1648`; `split_memory_budget_v3` epi share; **no** glass-tail floor in cut order |
| **Nature** | Supports listed before temporal; under pressure supports cut before temporal floor — but tip channel does not exist to protect. Closed-work narrative fills bulb. |
| **Evidence** | **Mass ranking driven by hermetic compose B (+ thin jsonl compose A), not full lance dogfood.** Hermetic pressure fixture B: epi ~981 tok vs temporal tip 27 tok; compose A dogfood jsonl was thin (atom_count 87; **0 atoms for moment e6d460f2**; lance open segfaulted in review env — possible richer lance data at runtime was not measured). Fail speak still references philosophy/fabric (behavioral), consistent with B11 under a filled bulb. |
| **Confidence** | **confirmed** mechanism (order + no tip floor); mass magnitude on full production lance **not measured** here (likely higher) |
| **Impact** | Model prefers “closed threads” narrative. |
| **Related draft §** | §4.1 precedence; §5.4 cut order |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Tip floor + never cut glass-tail below floor** | Hard law | Needs band | M |
| **B — Soft precedence prompt** | Cheap | Weak vs mass | S after bands |

**Recommended:** **A**.

---

### F-10 — In-turn re-outer / restart hydration / post-S1 triple risk

| Field | Value |
|-------|-------|
| **Bucket** | **B8** in_turn_vs_outer; **B9** restart_hydration; **B10** dual_copy **prospective** |
| **Severity** | B8/B9 **S2**; B10 **post-S1 risk only** |
| **Path(s)** | P9; P7/P8; after S1 |
| **Location** | `enforce_in_turn_budget` `doloop.py:456` may call `rebuild_outer`; keep RAM-only; `_last_meal_snapshot` process RAM (`worker.py:1519+`); glass disk unused as tip |
| **Nature** | Re-outer still lacks tip today. Restart loses tray + meal snapshot; glass remains on disk but memory meal ignores it for chat. After glass-tail, hybrid + temporal + tail may triple user row (B10). |
| **Evidence** | Static SA-7/SA-9; snapshot note in plan KD-R4. B10 not present today. |
| **Confidence** | B8/B9 **confirmed** structural; B10 **hypothesis** post-S1 |
| **Impact** | Restart chat amnesia; future dedupe bugs. |
| **Related draft §** | S2 dedupe; S3 persist tray; §5.3 hybrid |

#### Fix evaluation

| Option | Description | Pros | Cons | Effort |
|--------|-------------|------|------|--------|
| **A — Glass-tail from disk on every rebuild_outer** | Fixes B9 tip half | Tray still needs persist | M |
| **B — Dedupe policy glass_tail wins on message_id** | Prevents B10 | Careful media path | S with S1 |

**Recommended:** **A+B** with S1/S2.

---

### F-11 — Test / contract gaps

| Field | Value |
|-------|-------|
| **Bucket** | (process) |
| **Severity** | **S2** |
| **Location** | `tests/test_memory_meal*.py` lock order/omit; **no** wait_reply glass-tail / rockets-class integration test; directed_keep tests pack flags but not sticky tray across moments; `get_last_confirmed_keep` moment filter lightly covered via traverse tests |
| **Nature** | Contracts freeze **current** meal shape (no glass-tail). Implement plan must add golden outer fixtures for P2. |
| **Evidence** | SA-8 grep of tests |
| **Confidence** | **confirmed** |
| **Related draft §** | §5.5 acceptance → test names |

---

## 5. Fix portfolio (recommended ordering)

| Order | Ship | Faults addressed | Notes |
|-------|------|------------------|-------|
| **S1** | Glass-tail select/pack + tip floor + role fidelity | F-01, F-03, F-09 (partial), F-05 | Tip before keep (KD-R9) |
| **S2** | Path parity tests P2/P5/P7; why_now snippet dual-write; hybrid/tail dedupe | F-02, F-04, F-10 B10 | B12 not fixed by B1 alone evaluation |
| **S3** | Persist tray; stop wipe; **instance compose without moment_id filter**; TTL/LRU | F-06, F-07, F-10 B9 keep half | B5+B5b both required |
| **S4+** | Semantic seed from tail (OQ8); soft recall copy **after** bands | F-08; draft §8 | Not primary |

Do **not** ship S3 before S1 while wait_reply social still tip-smashes.

---

## 6. DRAFT-EXTENSIONS

Proposed refinements to `docs/design/memory/design-instance-continuity-glass-tail-directed-keep.md` (apply in PR-R4; **not** applied in this PR).

### 6.1 §5 Glass-tail placement

- **Confirm order:** `system → episodic → semantic → directed_keep → glass_tail → temporal → orient`.
- Offline recompose: temporal already holds weak wake obs; **glass_tail must still exist** for prior assistant roles and true user role. Prefer glass_tail for social `message_id` when deduping against temporal (OQ6).
- Hybrid remains media/id only; with tail, skip hybrid when same id present on tail **or** temporal.

### 6.2 §5.3 Path rules — normative min tip set

| Path | Must in outer | Nice |
|------|---------------|------|
| wait_reply | Last glass user answer + recent assistant glass social turns (e.g. prior speak) | Wait prompt if only in waits.json / prior tape |
| user_message | Same must with triggering user row | — |

Rockets failure = **ignored user question + missing prior assistant glass**, not missing wait-prompt text alone.

### 6.3 §5.4 Cut order (numbers from SA-9b)

- Hermetic pressure: tip temporal ~27 tokens vs epi hundreds–thousands.
- Law: **never cut glass-tail below floor** for social wakes (absolute min turns, e.g. ≥4 messages or ≥ last 2 full turns).
- Cut supports first: semantic → age-soft directed_keep → episodic; temporal protect tail unchanged.
- Meal fraction #91: document explicitly “larger R ≠ tip.”

### 6.4 §5.5 Acceptance → tests

Add named acceptance / future tests:

- `test_meal_glass_tail_wait_reply_includes_user_and_prior_assistant` (P2 rockets-class fixture)
- `test_meal_glass_tail_roles_preserved`
- `test_meal_tip_floor_under_epi_pressure`
- Offline golden from `evidence/sa9b-e6d460f2/` shape

### 6.5 §6 Sticky keep — **B5 + B5b**

Extend Part B target table:

1. **Stop** `on_moment_close` wipe of meal-relevant `last_confirmed_keep` (optionally still clear glass `last_session` per KD-A19).
2. **Persist** tray under `data/runtime/` (or memory meta) with `confirmed_at` / `last_reinforced_at`.
3. **Host TTL/LRU:** soft ~3h, hard ≤24h (OQ3 default).
4. **Meal wire (B5b):** `select_directed_keep` / compose reads **instance tray** and must **not** require `snap.moment_id == open_moment_id`. Remove or bypass equality filter in `get_last_confirmed_keep` for meal path (glass session view may keep scope separately).
5. **Merge vs replace** on finish (OQ5 → merge default).
6. Inspect: tray age, token use, entry moment_ids on `/api/memory/context` + graph session.
7. Keep must not substitute for tip under pressure (age-soft keep before tip floor).

### 6.6 §10 OQ evidence notes

| OQ | Draft default | Review evidence | Lock? |
|----|---------------|-----------------|-------|
| **OQ4** | Keep tray (not clear at moment end) | B5 wipe confirmed `traverse.py:1106–1117`; wipe is kill switch #1 | **Lock: keep tray** |
| **OQ6** | Glass-tail roles win vs temporal | SA-9b: temporal role collapse; assistant glass only recoverable via true roles | **Lock: glass-tail roles** |
| **OQ7** | Optional why_now user snippet; tail SoT | B12 tape tracks wait ceremony; tail-only may not fully kill framing | **Lock: optional but recommended dual-write for wait_reply** |
| OQ1 | Floor turns + soft % | No live token calibration; keep provisional | provisional |
| OQ2 | Newest toward orient | Matches hybrid insert-before-orient pattern | provisional lock OK |
| OQ3 | 24h hard / 3h soft | No dogfood TTL data | provisional |
| OQ5 | Merge | No multi-confirm dogfood in this review | provisional |
| **OQ8** | Seed from glass-tail last user | Structural: seed open-moment only today; no live ANN | **prefer lock after S1** |

### 6.7 §9 S0–S6 ordering

- Keep **S1 before S3**.
- Add explicit **S2: framing dual-write (why_now snippet) + path tests + dedupe** informed by B12.
- Soft recall §8 only after bands (A5 reject as primary reaffirmed).

### 6.8 New: rockets fix-evaluation summary for draft

Add a short note under problem statement or §5 (keep empirical vs normative labels distinct):

> **Empirical SA-9b recompose ranking** (hop-0 carriers in outer + tape): **B12 → B11 → B3 → B7 → B1**.  
> **Normative fix / structural priority** (implement order): **B12 + B1 co-primary → B3 → B7 → B11** (epi mass under tip floor once a glass-tail channel exists).  
> Elevating B1 for S1 is fix-priority (missing channel), not a re-rank of the empirical list. Implementation must evaluate **tail-only vs tail+orient snippet**, not B1 alone.

---

## 7. Open questions resolved / remaining

| ID | Status | Note |
|----|--------|------|
| RQ1 package shape | Multi-file under `meal-continuity-review/` | done |
| RQ2 live before OQ lock | Live skipped; structural + offline recompose sufficient for OQ4/6/7 notes | end-to-end speak not re-locked |
| Product OQ4/6/7 | Evidence-backed defaults above | apply in PR-R4 |
| OQ1/2/3/5/8 | Partial | implement plan measures |

---

## 8. Adjacencies

| Item | Relation |
|------|----------|
| **#91 meal budget** | Shipped; thickens bulb only — non-fix for tip |
| **#68 wake-02** | Post-restart wrong work thread — B9 / glass-tail adjacency |
| **#92 summaries** | Episodic quality (bulb), not tip |
| **lance-debug1** | Thin load can starve bulb after restart; do not attribute tip smash solely to thin Lance. Review env: lance open **segfaulted**; jsonl atom_count=87, 0 atoms for e6d460f2 (runtime may have used richer backend) |
| **BUG-mem-gpu-01** | Encode/semantic lag only when semantic on |

---

## 9. Appendix

### 9.1 Environment

| Item | Value |
|------|-------|
| Code SHA | `7ebf50b` |
| Dogfood data | read-only `…/project-elyra/data` |
| Live API | `http://127.0.0.1:8787` — **down / skipped** |
| Memory jsonl health (dogfood) | ok, atom_count=87, line_count=144 |
| SA-9b budget | 50_000 tokens; split fixed≈1920, epi≈9616, temp≈38464 (sem/dk off) |

### 9.2 Rockets ids (canonical)

| Kind | Id |
|------|-----|
| Moment | `e6d460f2-4087-42cd-870f-d34a89b6feaf` |
| Glass user | `04f85fc6-195a-4b3c-b0bf-8b307c7baa2f` |
| Glass fail asst | `37ec1721-930d-4045-9d0c-819c3c1c1baf` |
| Prior time asst | `436f4ca1-3860-4e3a-bd14-ec2bcb12373d` |
| Wait | `c13ae60a-40ed-45c6-a75a-035c1a78f05c` |
| Prior moment | `de87355d-ec85-4d49-a3a4-ece452678ea4` |
| Wake enqueue | `98af2ae5-9831-4f41-b328-e778bb758b7a` |

### 9.3 SA checklist completion

| SA | Status |
|----|--------|
| SA-1 Memory vs legacy branch | ✅ |
| SA-2 wait_reply carriers + B12 | ✅ |
| SA-3 Role / dialogue shape | ✅ |
| SA-4 Keep B5 + B5b | ✅ |
| SA-5 Semantic seed | ✅ (static; no live lag metrics) |
| SA-6 Interject | ✅ |
| SA-7 Budget / cut order | ✅ |
| SA-8 Tests vs gap | ✅ |
| SA-9 Tape/glass forensics | ✅ |
| SA-9b Offline recompose | ✅ `evidence/sa9b-e6d460f2/` |

### 9.4 Evidence files

```text
docs/investigations/meal-continuity-review/evidence/sa9b-e6d460f2/
  meta.json
  recompose_meal.json   # compose A dogfood jsonl + compose B hermetic pressure + carrier_ranking
  tape_excerpt.jsonl
  glass_window.jsonl    # content truncated
  wait_surface.json
  notes.md
```

PII: user_id `jim` retained as dogfood operator id; no secrets included.

### 9.5 Carrier rankings (empirical vs normative)

**Empirical recompose ranking** (SA-9b; matches `recompose_meal.json` / EDGE-MATRIX P2):

1. **B12** orient why_now + BIAS_TALK  
2. **B11** episodic closed-work mass  
3. **B3** role-collapsed temporal observation  
4. **B7** hybrid skip / single row  
5. **B1** missing glass-tail band (structural absence)

**Normative fix / structural priority** (implement S-order; not a re-quote of SA-9b):

1. **B12 + B1** co-primary (framing dual-write + glass-tail channel)  
2. **B3** role fidelity via glass-tail  
3. **B7** keep hybrid scoped; dedupe with tail  
4. **B11** epi mass under tip floor once tip exists  

**Primary package:** glass-tail (S1) + path/framing dual-write (S2) + sticky keep B5+B5b (S3).
