# Orchestration refactor plan — robust determinism for weak models

> **Branch:** `refactor/orchestration-determinism`
> **Analysis by:** the 2026-07-31 session (forensics over 96 MB of real logs + a comparison with Claude Code)
> **How to use this doc:** each front (F1–F5) is independent and can be done on its own. Read sections 1–3 first (context + principle), then take the fronts in the order of section 5. Each front carries: motivation with evidence, `file:line` anchors, the concrete change, an ADR with trade-offs, tests and risks.

---

## 0. TL;DR

The planner's deterministic layer (phases, evidence ledger, completion gate) was the **right** decision for running weak local models — but in practice it **stays switched off**, through three independent holes proven in real logs:

1. **The surface classifier drops the task to `CONVERSATION`** and that empties the planner's state block (present in only **17%** of steps; **0% in 3 of 5 sessions**).
2. **The meta-tools that drive quality are opt-in** and the weak model does not call them (`complete_task`=**0** in 241 steps) — so the one hard gate **never fires**.
3. **`execute_command` is the universal bypass**: 77% of the real mutations go through the shell, which **never** becomes `FILE_CHANGED` evidence, so post-mutation verification is never demanded.

**Correcting principle:** engage the discipline from **observed evidence** (what the model did), not from a keyword (what it said); and nail the quality checkpoint **at the loop's natural stopping point**, not in `complete_task`.

---

## 1. Map of the relevant architecture

Layers (top to bottom): `ui/` → `app/` → `core/` → `providers/`. A turn's orchestration lives in two places:

| Component | File | Role |
|---|---|---|
| **`AgentOrchestrator`** | `src/code_ai/core/orchestration.py` (~2150 lines) | The provider/tool loop of one turn. Deterministic, failure-tolerant. |
| **`PlannerService`** | `src/code_ai/core/planning/service.py` (~1650 lines) | Semantic progress, per-phase tool policy, evidence-based completion gate. |
| **`PlannerToolPolicy`** | `src/code_ai/core/planning/policy.py` | Derives allowed/recommended tools from the phase. In `advisory` mode (the default) it **exposes everything**. |
| **Classifier** | `src/code_ai/core/planning/models.py` — `TaskProfile.from_user_text` (:132) | A keyword regex decides `intent` / `requires_workspace_mutation`. **Single point of failure.** |
| **Evidence** | `src/code_ai/core/planning/evidence.py` — `_records_from_payload` (:308) | Turns a tool payload into an `EvidenceRecord`. `execute_command` at :415. |
| **Completion gate** *(removed)* | `src/code_ai/core/planning/completion.py` | Minimal/Standard/Strict policies over an immutable `CompletionContext`. |
| **Preconditions** | `src/code_ai/core/planning/preconditions.py` | Advisory nudges (once, fail-open) before action tools. |

**Flow of a turn** (`orchestration.py`):
- `run_turn` (:405) → `_begin_planner` (:542) classifies and builds the skeleton → `_run_model_loop` (:570).
- Loop: `_build_request` (:2014) attaches `_planner_context()` as an ephemeral *user* message per step → `_run_model_step` → with no tool call: `_handle_no_tool_response` (:635); with one: `_execute_tool_batch` (:1300) → `_note_tool_round` (:1066, the stall guard).
- Completion: only through `complete_task` → `_completion_rejection` (:1606) → `planner.evaluate_completion` (service.py:844).

**Runtime constraint worth knowing:** the app runs **one model per turn**; background reflection is cancelled when a new turn needs the model (`_cancel_pending_learning`). Do not add extra synchronous model calls to the critical path.

---

## 2. Forensic baseline (the evidence behind all of it)

Source: `~/.code-ai/logs/` — **241 real model steps**, 5 sessions, the local model `qwen3.6-35b-a3b`, a filesystem-heavy mutation workload done mostly through the shell. Method: parse the `REQUEST`/`PARSED RESPONSE` blocks of each `*.log`.

| Metric | Value | Implication |
|---|---|---|
| "Runtime task state" block present | **42/241 (17%)**, 0% in 3/5 sessions | The planner is mute in most steps (hole #1) |
| `submit_plan` / `complete_plan_step` | 2 / 2 | The model's checklist is decorative |
| `complete_task` | **0** | The completion gate **never fires** (hole #2) |
| `use_skill` | **0** | Skills are never used proactively |
| `execute_command` | **118/221 tool calls (53%)** | The shell is the dominant channel |
| — of those, mutating the filesystem | **77%** (`python -c`, `>`, `cp`) | Mutations invisible to the planner (hole #3) |
| Batches with >1 write | **0** (191/221 batches = 1 call) | "Limit writes per step" would be a no-op — **do not build it** |
| Tools exposed per step | **43** (or 0 in 19 steps) | No focus; a large surface for a weak model |
| Tool calls truncated mid-stream | **44** in a single session | Streaming robustness (F4) |
| Distinct stall nudges / preconditions | 8 / 7 | Healthy, modest fail-open guards |

**Direct confirmations in the code** (run during this analysis):
```
"continue de onde paramos"   -> CONVERSATION, mut=False   (a continuation becomes chat)
"olhe o arquivo ..."         -> LOCAL_INSPECTION, mut=False (no mutation scaffolding)
"implemente um endpoint"     -> IMPLEMENTATION, mut=True    (control: it works)
```
`execute_command` in `evidence.py:415` classifies the shell as `VERIFICATION_*` (when it is a test/build) or `COMMAND_SUCCEEDED/FAILED` — **never** `FILE_CHANGED`/`FILE_CREATED`. So `has_file_change` (service.py:927) stays `False` for work done through the shell, and `changes_require_verification([])` returns `False`.

**Reproducing the forensics:** the scripts were thrown away, but the method is: for each `~/.code-ai/logs/<session>/*.log`, split on `^===== (REQUEST|PARSED RESPONSE)`, `json.loads` each block, count `tool_calls[].name` in the response and `messages` in the request. Nudges are counted in the **last** request of each session (the one with the fullest history), otherwise they inflate by persisting in the history.

---

## 3. Design principle (the north star of every front)

> **Determinism driven by observed evidence, not by declared classification; the quality checkpoint at the loop's natural stopping point, not in an opt-in meta-tool.**

This imports the good lesson from Claude Code — **the loop ends when the model stops calling tools** (official docs: agent-loop; there is no phase machine and no completion gate there; TodoWrite is tracking, not a latch; plan mode is a permission mode) — **without** giving up the determinism a weak model needs. Claude Code can trust the model because it runs Opus/Sonnet; we cannot, so the **runtime** has to nail the latches down — but nail them to what the model **did**, not to what it **said** it would do.

Anti-over-engineering corollary: do **not** harden batches (the data: batches are already 1 call), do **not** turn fail-open guards into hard latches (they are healthy), do **not** copy Claude Code's flat loop (it depends on a strong model).

---

## 4. The fronts

### F1 — Decouple determinism from the classifier  *(root cause #1 — biggest impact)*

**Motivation.** Today the whole mutation discipline is conditioned on `TaskProfile.requires_workspace_mutation`, which comes from a keyword regex over the **first text of the turn**. Continuation turns ("continue", "go on") and low-level instructions fall into `CONVERSATION` → `task_context_block` (service.py:625) returns `""` → zero scaffolding. Proven: the block is present 17% of the time.

**Two changes (independent of each other):**

**F1a — A continuation inherits the previous profile.**
- Anchor: `PlannerService.begin_turn` (service.py:226) — today it always does `self.profile = TaskProfile.from_user_text(text)`.
- The precedent already exists in `_resume_turn` (service.py:273) for a pending `ask_user`. Generalise it: if the turn's text is a **pure continuation** (a short regex: `^(continue|contin[úu]e|siga|prossiga|segue|vai|go on|keep going)\b` with no substantive new objective) **and** `self.profile` already exists, take the resume path instead of reclassifying.
- **Where to detect it:** either in `begin_turn` (early, before `self.profile` is overwritten), or raise the decision to whoever calls `run_turn(resume_plan=...)` in `app/service.py`. Prefer `begin_turn` to keep the rule in one place.

**F1b — Escalate to mutation discipline on observed evidence.**
- Anchors: `record_tool_result` (service.py:374) and `_effective_profile` (service.py:771).
- When the model calls a mutating tool (`write_file`/`edit_code`, or a mutating shell — see F2) in a task classified as non-mutating, **promote** the profile at runtime: set a flag `self._observed_mutation = True` and make `_task_produces_workspace_effects` (service.py:755) and `task_context_block` treat the task as producing workspace effects **from there on**.
- The completion gate **already** does this in part (`has_file_change and not verified` in completion.py:201 fires regardless of the label) — F1b extends the same philosophy to the **context block** and the **verification requirement**, not just to the final gate.

**ADR / trade-offs.**
- *Decision:* engage on observation, with the classifier becoming only an initial hint.
- *Pros:* closes hole #1 at the root; robust to phrasing; reuses the philosophy already proven in the gate.
- *Cons:* a risk of re-injecting the heavy block (~2 KB) into a genuine conversation. *Mitigation:* F1b only switches on **after the first evidence of mutation** — a pure conversation never calls a mutating tool, so it never escalates. F1a is conservative: it only fires on pure continuation text with a pre-existing profile.
- *Alternative rejected:* improve the mutation regex (add more verbs to the dictionary). Rejected: it is a perpetual patch, does not close the class of hole, and does not cover continuation.

**Tests** (`tests/unit/test_planning.py`, 64 tests today):
- "continue" after an implementation turn keeps `requires_workspace_mutation=True`.
- A CONVERSATION turn that calls `write_file` starts requiring verification to complete.
- A pure conversation (no mutating tool) does **not** re-inject the block or require verification.

**Risks.** Do not let F1a swallow a turn that *looks* like a continuation but brings a new objective ("continue, but now do X") — the regex must require the text to be **essentially only** the continuation marker.

---

### F2 — Make shell mutations visible to the planner  *(covers 77% of the real mutations)*

**Motivation.** An `execute_command` that mutates the filesystem never becomes `FILE_CHANGED` (evidence.py:415). The chain of consequences: `has_file_change=False` (service.py:927) → post-mutation verification is never demanded → the completion gate has nothing to ask for. For shell-driven workloads (generating/editing files through scripts, filesystem automation), the whole discipline is blind.

**Change.**
- Anchor: the `execute_command` block at `evidence.py:415`.
- Detect a mutating shell by the shape of the command (do not parse paths — fragile): redirections `>`/`>>`, `Set-Content`/`Add-Content`/`Out-File`/`New-Item`, `cp`/`mv`/`Copy-Item`/`Move-Item`, `sed -i`, `tee`, `python -c` with a write, `rm`/`Remove-Item`. The regex is already sketched in this analysis's forensics.
- When mutating **and** exit 0: besides the current `COMMAND_SUCCEEDED`, emit a **coarse** mutation signal — a new `EvidenceType.WORKSPACE_MUTATED_BY_COMMAND` (or a ledger flag `command_mutated_workspace=True`) that makes `has_file_change`/`has_success(...)` return `True` **without** specific paths.
- Consume that signal in `_completion_context` (service.py:927): `has_file_change = ledger.has_success(FILE_CREATED, FILE_CHANGED) or ledger.command_mutated_workspace`.

**ADR / trade-offs.**
- *Decision:* a coarse-grained signal ("the workspace changed through a command"), not path extraction.
- *Pros:* engages verification for the dominant channel; simple; no arbitrary shell parser.
- *Cons:* without paths, post-mutation verification becomes generic ("run the project's check") instead of file-directed. Acceptable — the goal is to *require* verification, not to locate the diff. And `changed_paths` stays empty, so `changes_require_verification` has to consider the flag too (otherwise it returns `False` for the empty list — watch that interaction in completion.py:53).
- *False positive:* an `rm` of a temporary file counts as a mutation. Impact: it asks for verification once; fail-open covers it. Tolerable.

**Tests** (`tests/unit/test_completion_gate.py`, 28 tests; plus evidence in `test_planning.py`):
- `execute_command` with `echo x > f.py` exit 0 → `has_file_change=True`, completion without verification is rejected once.
- A reading `execute_command` (`cat`, `ls`) does **not** mark a mutation.
- A mutating command + verification afterwards → completion accepted.

**Risks.** The interaction `changes_require_verification([])` (completion.py:53) returns `False` for an empty list — if you set `has_file_change=True` with `changed_paths` empty, make sure the verification path still engages (adjust `verification_applies` in service.py:935 to consider the command flag).

---

### F3 — Completion driven by the runtime  *(unblocks the dead latch #2)*

> **Removed (2026-10):** the end-of-turn verification checkpoint, the prose correction and the `complete_task` evidence gate were all dropped: built for weak models, they got in the way of capable ones. A prose answer now simply ends the turn and `complete_task` accepts any well-formed claim.

**Motivation.** `complete_task`=0 in 241 steps. The evidence-based completion gate — the one hard latch — is dead code in real use, because it depends on the weak model calling a meta-tool. Turns end in prose (`_handle_no_tool_response`) or in a stall/wind-down, with no verification checkpoint at all.

**Change.** Move the checkpoint to the loop's **natural stop** (when the model answers with no tool call), keyed on **observed evidence**:
- Anchors: `_handle_no_tool_response` (orchestration.py:635) + `settle_agent_plan_on_final_answer` (service.py:1246).
- Before accepting the end in prose: if the task accumulated mutation evidence (`has_file_change`, including F2's flag) **and** there is no successful verification for the current change set, inject **one** deterministic nudge ("you changed the workspace but ran no verification; run the project's check or state the limitation") and run one more step. Bounded: **once per turn**, then fail-open (accept the prose and surface the gap as a limitation — the same spirit as the fail-open `CompletionGate` at completion.py:385).
- Reuse the existing `_verification_status`/`_completion_context` logic to decide "verification is missing" — do not duplicate it.

**ADR / trade-offs.**
- *Decision:* the runtime nails the checkpoint at the stopping point, independent of `complete_task`.
- *Pros:* makes the latch effective for models that never call the meta-tool; one nudge, fail-open, never traps the turn.
- *Cons:* an extra round trip at the end of unverified mutation tasks. Acceptable and proportional to the risk (mutation without verification is exactly what we want to catch).
- *Interaction:* do NOT change the `complete_task` path — it stays valid when a strong model calls it. F3 is a **second door** to the same gate, at the natural stop. Both converge on the same `_completion_context`.
- *Alternative rejected:* force `complete_task` through policy (refuse prose until the model calls it). Rejected: it traps a weak model in a loop, and the previous session already showed that "prose as the final answer" is legitimate in plenty of tasks.

**Tests** (`tests/unit/test_orchestration_resilience.py`, 21 tests; `test_completion_gate.py`):
- A turn that edits a file and ends in prose with no verification → one verification nudge, then accepted.
- A turn that edits + verifies + ends in prose → accepted with no nudge.
- A read-only task ending in prose → **no** nudge (do not regress inspection behaviour).
- The nudge is injected at most once per turn.

**Risks.** Do not collide with F1b (both touch "what counts as a mutation task"). Suggested order: F1 and F2 first (they define `has_file_change` correctly), F3 afterwards (it consumes it). Take care that the nudge does not fire on a read-only task — gate on `has_file_change`, not on the label.

---

### F4 — Streaming robustness  *(secondary, but real)*

**Motivation.** One session had **44 tool calls truncated mid-stream** (out of 97 steps — nearly half). `_retry_interrupted_tool_call` already exists (orchestration.py:788, max `_MAX_INTERRUPTED_CALL_RETRIES=2`), but 44 in one turn suggests the retry is not holding the case, or there is provider/timeout instability.

**Investigate before changing anything** (do not assume the cause):
- See which tools truncate (probably large `write_file`/`edit_code`) by crossing `tool.call.interrupted` with the payload size in that session's logs (`c7e68e6c605d`).
- Check whether it is a model timeout (`model_timeout`) or a real provider cut. The user's `config.json` has `max_model_step_seconds=900` — generous, so it is probably a stream cut, not a timeout.

**Possible directions** (decide after the investigation):
- Harden the instruction in `_interrupted_call_correction_text` (orchestration.py:844) to force more aggressive chunking after the first cut.
- If it is a recurring large write: make `oversized_write_gap` (preconditions.py:121) fire **before** the cut, not after.

**ADR / trade-offs.** *Low priority* — concentrated in one session, possibly specific to the provider/model. Do not invest here before F1–F3 unless it reproduces easily.

**Tests.** `tests/unit/test_orchestration_resilience.py` already covers the interrupted-call retry; extend it if the policy changes.

---

### F5 — Tool surface for a weak model  *(smallest — only after F1)*

**Motivation.** Always 43 tools exposed plus a ~18 KB system prompt. For a weak model, a focused per-phase set reduces error measurably. Today `tool_policy="advisory"` (defaults.py:186) exposes everything; `_strict_allowed_tool_names` (policy.py:131), which would focus by phase, is **dead code in production**.

**Change.** Do not switch strict on blindly (the previous session documented why advisory exists: strict traps a badly classified task, policy.py:106-118). **It is only safe after F1** makes the classification trustworthy. So: either (a) switch strict on with F1's engage-on-evidence as the safety net, or (b) keep everything callable but **highlight** the recommended ones in `task_context_block` (the "Recommended tools now" line already exists).

**ADR / trade-offs.** *Pros:* less surface = less weak-model error. *Cons:* it reintroduces the risk advisory solved. *Recommendation:* do (b) first (low risk), measure, and only consider (a) if F1 proves the classification robust.

**Tests.** `tests/unit/test_planning.py` has per-phase policy coverage — extend it.

---

## 5. Recommended order of execution

```
F1  (F1a + F1b)   -- root cause; correctly defines "this is a mutation task"
  |- F2           -- makes the shell count as a mutation (feeds has_file_change)
      |- F3       -- consumes has_file_change at the loop's natural stop (removed)
F4                -- independent; do it after investigating, or defer it
F5                -- only after F1 proves the classification robust
```

F1→F2→F3 are a **coherent package** (the "definition of observed mutation" flows between them) and deserve a single ADR in the history. F4 and F5 are independent and can wait.

**Suggested commits:** one per front (F1a, F1b, F2, F3), each with its tests in the same commit. Messages in the repo's style: `fix(orchestration): ...` / `feat(planning): ...`.

---

## 6. Test and verification strategy

- **Suite:** `pytest` (configured in `pyproject.toml`, `testpaths=["tests"]`, `pytest-asyncio`). Run the relevant subset while developing:
  ```bash
  python -m pytest tests/unit/test_planning.py tests/unit/test_completion_gate.py tests/unit/test_orchestration_resilience.py -q
  ```
- **Key regression not to break:** a read-only task ending in prose must **not** require verification or write a file (it is the reason half the current safeguards exist — see `_task_produces_workspace_effects` and the "READ-ONLY TASK" in service.py:741). Every F1b/F3 test has to prove read-only stays clean.
- **End-to-end validation (optional, high value):** re-run section 2's forensics after the changes, on a shell-driven workload, and confirm: the planner block present in >90% of mutation steps; `has_file_change=True` when there was only shell; at least one verification required per mutation task.
- Run `ruff` (there is a `.ruff_cache`) to keep the style.

---

## 7. What explicitly NOT to do

- Do **not** limit writes per model step — the data shows 1-call batches; it would be a no-op at the cost of complexity.
- Do **not** turn the fail-open guards (stall, precondition) into hard latches — they are healthy (8 and 7 distinct fires) and the "one nudge, fail-open" philosophy is what keeps a weak model from being trapped.
- Do **not** copy Claude Code's flat loop — it depends on a strong model; our target is the opposite.
- Do **not** add RAG for skills — the previous session already concluded that the bottleneck is enforcement/attention, not retrieval (the candidate is already in the context).
- Do **not** add an extra synchronous model call to the turn's critical path (the one-model-per-turn constraint; see `_cancel_pending_learning`).
- Do **not** reclassify a continuation from scratch (that is F1a's bug).

---

## 8. Quick reference (anchors)

| What | Where |
|---|---|
| Turn loop | `orchestration.py:570` (`_run_model_loop`) |
| Natural stop (no tool call) | `orchestration.py:635` (`_handle_no_tool_response`) |
| Tool batch | `orchestration.py:1300` (`_execute_tool_batch`) |
| Classifier | `models.py:132` (`from_user_text`), `:636` (`_is_mutation_request`) |
| Turn start / resume | `service.py:226` (`begin_turn`), `:273` (`_resume_turn`) |
| Context block | `service.py:625` (`task_context_block`) |
| "Does the task affect the workspace?" | `service.py:755` (`_task_produces_workspace_effects`) |
| Completion snapshot *(removed)* | `service.py:923` (`_completion_context`) |
| Settling on prose | `service.py:1246` (`settle_agent_plan_on_final_answer`) |
| `execute_command` evidence | `evidence.py:415` |
| Completion policies *(removed)* | `completion.py` (`select_policy:330`); `changes_require_verification` now lives in `service.py` |
| Per-phase tool policy | `policy.py:131` (`_strict_allowed_tool_names`, dead today) |
| Advisory nudges | `preconditions.py` (`oversized_write_gap:121`) |
| Planner defaults | `defaults.py:186` (`tool_policy="advisory"`) |

Related memories: `orchestration-forensics-2026-07`, `claude-code-orchestration-model`, `code-ai-project-architecture`, `plan-completion-on-prose-answer`.
