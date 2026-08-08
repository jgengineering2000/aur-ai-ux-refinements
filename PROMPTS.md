# AUR — Quick Prompt Specifications

Each entry defines one trigger phrase that appears in the Manifest quick-prompt bar.
The **Trigger** is the exact short phrase the user copies and pastes into the AI chat.
The **Spec** is the full optimized instruction set Claude should execute when it receives that trigger.

---

## session close

**Purpose:** Consolidate and gate-check all knowledge discovered in a session before any
promotions to specification documents are executed.

**Spec:**

> Prepare session close.
>
> 1. Inventory every piece of knowledge discovered or confirmed in this session that has
>    not yet been promoted to a specification document. Cast a wide net — include decisions,
>    discoveries, reversals of prior assumptions, deferred items, and open questions.
> 2. For each knowledge item: identify the target document and section, categorise as
>    **ready-to-promote**, **needs-verification**, or **defer**, and draft the exact text
>    to insert (for ready items).
> 3. Locate any prior unprocessed session-close documents in `aur-docs/`; merge their
>    unresolved items into this inventory (newer source wins on conflicts).
> 4. Generate a session-close review document with:
>    - Master table — ID | Source | Knowledge Item | Target Document | Action | Notes
>    - Per-item draft cards for every "ready-to-promote" and "needs-verification" entry.
>      Each card must include four header fields before the description so the user
>      can evaluate the item without digging into source code or spec docs:
>      - **Code state** — what the implementation currently does, in present tense
>        (e.g. "mti-fuse currently returns ENOENT on generation failure"). Must be
>        verifiable and specific enough to challenge if wrong.
>      - **Spec state** — what the target document currently says, or explicitly note
>        that it is silent on this point (e.g. "MES.md §8 has no mention of this
>        behaviour" or "DESIGN.md §8 currently says X, which contradicts the code").
>      - **After approval** — exactly what changes in the spec: add a new section,
>        correct an existing claim, record an empirical measurement, or note a
>        design decision. Show the specific paragraph or bullet that will be inserted.
>      - **Interpretation** — one of: Design decision | Empirical result |
>        Bug workaround | Documented fix | Open question. This tells the user
>        whether they are endorsing an architectural choice, recording a fact,
>        acknowledging a known issue, or something that needs investigation first.
>    - Proposed next actions, priority-ordered
> 5. Do not promote anything. Await explicit confirmation for each promotion.

**Output format:** HTML session-close document written to `aur-docs/`.
**Gate:** Nothing is written to spec docs until the user confirms each item.

---

## bench plan

**Purpose:** Specify the complete performance measurement contract before any optimisation
work begins. Results come later; this is the plan only.

**Spec:**

> Create a performance benchmarking plan.
>
> Define the synchronous baseline for all measurable workloads. The output is a
> specification — a contract for what will be measured and how — not results.
>
> Include:
> 1. **Goals & scope** — one paragraph stating what is and is not in scope.
> 2. **Test corpus** — table of test inputs with file counts, formats, and coverage
>    rationale for each subset.
> 3. **Unit benchmarks** — table of individual component tests with expected values and
>    acceptable tolerances.
> 4. **End-to-end scenarios** — table of full-stack scenarios with timing budgets per
>    phase.
> 5. **Measurement matrix** — all configuration combinations to test (e.g. logging on/off,
>    tracing on/off) with a rationale column.
> 6. **Exact commands** — the literal shell commands or test invocations for each
>    benchmark, ready to copy-paste.
> 7. **Execution schedule** — ordered steps for a complete benchmark run session.

**Output format:** HTML bench-plan document written to `aur-docs/`.
**Gate:** No benchmarks are run during this step.

---

## bench results

**Purpose:** Document and interpret the results of a completed benchmark run, validating
each result against the plan's expected values.

**Spec:**

> Document benchmark results against the bench plan.
>
> For each benchmark defined in the plan:
> 1. Record the measured value alongside the expected value and tolerance.
> 2. Mark status: **PASS**, **FAIL**, or **INVESTIGATE**.
> 3. For FAILs: note the delta and the most likely cause.
>
> Produce:
> - **Results summary table** — scenario | expected | measured | delta | status
> - **Performance trend section** — before/after comparison if a prior bench-results
>   document exists; show improvement or regression per scenario.
> - **Outlier cards** — one card per FAIL or INVESTIGATE item with root-cause hypothesis
>   and recommended next step.
> - **Open items** — benchmarks not yet run, deferred measurements, infrastructure gaps.

**Output format:** HTML bench-results document written to `aur-docs/`.
**Gate:** No code changes — document findings only.

---

## project audit

**Purpose:** Full specification-vs-implementation audit producing a prioritised gap
analysis and implementation roadmap.

**Spec:**

> Perform a full specification-vs-implementation audit.
>
> 1. Read every specification document in the project root (all-caps `.md` files).
> 2. For each requirement or design decision, locate the implementing code.
> 3. Classify each item:
>    - **Implemented** — code matches spec
>    - **Partial** — exists but incomplete or diverged
>    - **Missing** — not implemented
>    - **Undocumented** — implemented but absent from specs
>    - **Deferred** — explicitly out of scope
> 4. Produce:
>    - **Executive summary** — coverage table by category (total | implemented | partial |
>      missing | %)
>    - **Full cross-reference matrix** — requirement | source doc | implementing files |
>      status | notes
>    - **Gap analysis** — ranked by severity: correctness → functional → architectural →
>      cosmetic; each row has ID, description, severity, affected files, user-visible impact
>    - **Implementation roadmap** — P1–P4 priority buckets with effort estimates
> 5. Flag dead code and deferred-scope items separately.

**Output format:** HTML audit document written to `aur-docs/`.
**Gate:** No code changes during audit.

---

## code quality

**Purpose:** Systematic review of code correctness, security, and maintainability without
making changes.

**Spec:**

> Perform a code quality review of the current codebase.
>
> Examine each source file across five dimensions:
> 1. **Correctness** — logic errors, unhandled edge cases, error handling only at system
>    boundaries (not internal), trust violations between layers.
> 2. **Security** — input validation, injection risks (SQL, shell, path traversal), secret
>    handling, OWASP Top 10 applicability.
> 3. **Maintainability** — unclear naming, functions that do too much, premature
>    abstractions, backwards-compatibility shims for code that no longer needs them, dead
>    code, misleading comments.
> 4. **Performance** — algorithmic complexity issues, unnecessary allocations, blocking
>    operations in hot paths, missing caches.
> 5. **Test coverage** — non-trivial logic lacking tests, tests that only mock instead of
>    exercising real behaviour.
>
> Format findings as severity-tagged cards: **critical | high | medium | low**.
> Each card: file path and line range, description, why it matters, suggested fix.
> Close with a prioritised fix list — critical and high items first.

**Output format:** HTML code-quality document written to `aur-docs/`.
**Gate:** Review only — no code is changed.

---

## debug plan

**Purpose:** Structured hypothesis-driven plan for isolating a specific bug or performance
problem before any fixes are attempted.

**Spec:**

> Create a debug plan for [describe the symptom and observed vs. expected behaviour].
>
> 1. **Symptom table** — what was expected vs. what was observed, with all available
>    measurements.
> 2. **Hypothesis tree** — one card per suspected root cause, ordered by likelihood;
>    each card states: hypothesis, what measurement would confirm it, what measurement
>    would rule it out.
> 3. **Known-correct list** — causes already ruled out and the evidence that rules them out.
> 4. **Controlled experiments** — for each hypothesis: exact steps to isolate it, what to
>    measure, how to interpret the result.
> 5. **Ordered debug steps** — a numbered sequence from highest to lowest likelihood, each
>    with clear pass/fail criteria.
>
> Do not attempt any fixes. The output is the plan only.

**Output format:** HTML debug-plan document written to `aur-docs/`.
**Gate:** No code changes — plan only.

---

## debug results

**Purpose:** Document the outcome of a debug session, linking each finding back to the
plan and validating that fixes resolved the correct root cause.

**Spec:**

> Document debug results against the debug plan.
>
> For each hypothesis in the plan:
> 1. State whether it was **confirmed**, **ruled out**, or **untested**.
> 2. For confirmed hypotheses: describe the fix applied, the code location, and the
>    measurement proving resolution.
> 3. Show before/after measurements side-by-side.
> 4. Explain the root cause in plain language (one paragraph, no jargon).
>
> Produce:
> - **Results table** — build/iteration | key metric | notes; one row per fix applied
> - **Per-fix cards** — each linking back to the debug-plan hypothesis it addresses
> - **Updated measurement table** — post-fix values alongside original observations
> - **Side-effect check** — confirm fixes did not regress adjacent behaviour
> - **Open items** — remaining hypotheses not yet tested, follow-on work

**Output format:** HTML debug-results document written to `aur-docs/`.
**Gate:** No further code changes — document findings only.

---

## push release

**Purpose:** Pre-flight check, release note generation, and gated push to the remote
repository and package registry.

**Spec:**

> Prepare a release.
>
> Pre-flight checks (report status for each — do not proceed past failures):
> 1. All tests pass (run the test suite; show the result).
> 2. No uncommitted or untracked changes relevant to the release.
> 3. Version number is bumped per semver relative to the last git tag
>    (patch for fixes only, minor for new features, major for breaking changes).
> 4. CHANGELOG or release notes drafted covering every change since the last release
>    (derive from git log if no CHANGELOG exists).
> 5. README reflects current capabilities and installation instructions.
>
> Generate a release-summary document showing: new version, changelog, test results,
> files changed since last tag, and any known limitations.
>
> Await explicit confirmation before executing any of:
> - Creating the git tag
> - Pushing branch and tag to origin
> - Creating the GitHub release
> - Publishing to a package registry
>
> After confirmation: execute the above steps in order, reporting the result of each.

**Output format:** HTML release-summary document written to `aur-docs/`.
**Gate:** Full confirmation required before any push, tag, or publish action.

---

*Add project-specific triggers below this line.*
