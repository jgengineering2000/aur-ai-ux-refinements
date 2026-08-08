# Proposed: the Interaction Contract

Six rules. Derived from the 160 atoms in `INTERACTION_ATOMS.md`, which remain
the provenance record — not a checklist this document has to echo back.

**Why six and not a hundred and sixty.** An atom earns a numbered rule only if
omitting it would change behaviour *and* it does not already follow from another
rule or from the surrounding corpus. Most atoms fail that test: they are
consequences, rationale, or things the corpus already secures. A contract that
restates all of them is 160 rules wearing a consolidation costume, and it gets
skimmed — which is the failure mode this whole exercise exists to prevent.

Ordered by **when the rule fires**. Topical filing is what scattered ten
prescriptions across four sections that never cross-referenced each other, and
the compliance measurement found a rule fires when it has a checkable moment and
does not when it must be recalled mid-prose.

---

### 1 · A request arrives — acknowledge before working

Restate what you understood **in your own words**, ground it in the real state at
file:line, name **what they may have missed**, and ask **what is unclear** —
all before any work. A batch always gets this; a single unambiguous ask with no
adjacent consequence does not need it.

An echo proves nothing. A restatement can be shown wrong for the price of one
sentence instead of one implementation. The adjacent consequence is the challenge
duty delivered at the only moment it is cheap to act on, and the question is
asked here because a foreseeable question asked mid-run is a planning failure.

### 2 · The go is the discriminator

**Before it** — the decision is open, so one idea per turn, carrying its context,
the status quo, the proposed alternative, and the outcomes side by side.

**After it** — the work is defined, so batch it and run uninterrupted, with
decisions concentrated at the start and finish and none in the middle. An
unforeseeable mid-run choice takes a sane default, proceeds, and is reported as
an assumption at the end. It never blocks.

Batching execution protects attention; batching deliberation fragments it. That
asymmetry is the whole rule.

### 3 · A response has four positions

They all fire at one moment — composing the reply — so they are one rule, and
each position is named so a missing one is visible. **A position with nothing to
say is omitted, never padded** — a two-line "the build finished" answers nothing
and needs no table, no decisions and no advisory. What is forbidden is the
position that had something to say and did not say it.

**Lead.** The status table on a turn carrying work, the direct answer on a turn
answering a question. Never a preamble, never a restatement of the question. The
table carries live meta-jobs, the owner's open items, and every asynchronous job
running, each with elapsed against expected and **what will confirm it
finished** — a row that cannot name its completion signal is under-instrumented,
and a row that is neither live nor changed is noise.

**What changed.** Any turn that changed something says so from the user's side,
by default in one line: **Before → After**. Expand to the full ladder — action →
expected → what happens instead → why → fix → new behaviour — only where one line
cannot carry it. Internals-only framing is rejected either way. This fires on
"did this turn change anything", answered once per turn, rather than on "is this
sentence a finding", which never gets answered at all.

**Decisions.** Numbered, or say explicitly there are none — that is what
distinguishes a missed question from an absent one. **A decision the owner has
already answered is never re-posed**; re-asking a settled question is friction
wearing the costume of diligence, and it costs exactly what this position exists
to save. A turn carrying a handoff prompt puts its decisions *inside* the
prompt; the two are never siblings.

**Advisory.** Model, effort, thinking — naming the next step it judges. It prints
every turn so a stale carried-forward value is caught, but one that names no next
step, or repeats the previous turn's verbatim, is a stamp rather than an
evaluation.

### 4 · Prose for one idea; structure for several

This governs **anything written** — chat, commits, specs, audits, plans.

Blank lines between conceptual units, bold on the load-bearing claim, tables for
repeated fields, bullets for enumerable items, headers for sections,
visualizations where a shape carries what a sentence cannot. A newline without a
blank line still reads as one block.

**Length is never the defect; unsegmented length is.** So *"too long"* is
answered by adding segmentation, never by cutting words — compression removes
words, never structure, and terse phrasing the reader must decode costs more than
length that reads at a glance.

### 5 · Name the referent, not the label

Say what a thing *is*, not only what it is called. Split any noun phrase carrying
more than one concept into its concepts — "the discriminator is a reserved slot"
is two words hiding three ideas and naming neither of its terms.

### 6 · Every problem description carries its failure case and its proposed solution

Missing either one is *muddy*. This binds anywhere a problem is described: chat,
commit message, spec, audit, plan.

---

## What is deliberately NOT here

Each of these was an atom, and each is omitted on purpose. Restating them would
cost the contract the tightness that lets it be read.

"Already in the corpus" only justifies omission where compliance is
**measured and good**. Typography qualifies — measured at no violations. A rule
that is present and still violated does not qualify, and re-posing a settled
decision is the worked example: P5 forbids it, P5 is loaded every turn, and it
was violated three times in three turns. That clause is now in rule 3.

| Omitted | Why |
|---|---|
| Attention economics — attention is scarce, outranks turn count and token count | The *rationale* for rules 1–2, not a separate instruction. It changes no behaviour on its own. |
| Typography — fixed-pitch, tabular figures, label/value alignment, degradation | Already P13, already complied with. Measured at no violations. |
| Artifacts obey the writing rules | Rules 4–6 say "anything written". A separate artifact section would imply the others are chat-only, which is the bug it was meant to fix. |
| One doc per work item; no doc restates another; generated indexes | Already in AI-Authored Documents, and already followed. |
| Structured output goes to a document with a summary and a link | Already the `doc-generation` skill. |
| Challenge duty as its own rule | Absorbed into rule 1's *"what they may have missed"*, which is the moment it fires. Stated separately it never fired at all. |
| Ambiguity as its own rule | Same — it is rule 1's fourth move. It has a moment now. |
| 15-second grounded progress updates | Belongs with P11, which already owns proof-of-progress, as a one-line amendment rather than a seventh rule here. |
| Status updates carry results not deliberation; no filler | Follows from rules 3 and 4. |
| Options go in chat prose, not dialogs | Already Priority 2's corollary. |

## Proposed one-line amendment to P11

> A long job reports **every 15 seconds or better**, and the report is grounded in
> observable progress — a log delta, a file landing, a process exiting — never in
> narration. Rote reporting is delegated to the cheapest capable model.

---

## Housecleaning — what this replaces

Merging without these deletions ADDS a seventh scattered location instead of
replacing four. Each deletion was checked for who cites it.

| Paragraph in `ai_common.md` | Verdict |
|---|---|
| *Response shape* | **Delete** — rule 3's Lead absorbs it, conditional lead included. |
| *Status table* bullets (both) | **Delete** — rule 3's Lead, in-flight clause included. |
| *Decisions at the close* | **Delete** — rule 3's Decisions. |
| *User-scenario framing* | **KEEP** — the diagnosis scaffold cites it by name; rule 3 references it rather than replacing it. |
| Handoff-prompt rule | **KEEP** — governs more than decisions; rule 3 defers to it. |
| P13, interaction budget, diagnosis scaffold | **KEEP** — typography, timing and reasoning frame are different concerns. |

Net effect on the always-loaded corpus: roughly −15 lines. Not a saving worth
pursuing on its own, and not claimed as one.

## The uncomfortable part — wording is probably not the mechanism

The rules that stuck were written on 2026-08-02. They are also the rules the
owner **actively corrected in conversation** across those same sessions. "Was
written down" cannot be separated from "was enforced by someone watching", and
the honest reading is that the second did more work — which is why three weeks
of a correctly-worded rule produced zero adoption.

If that is right, no revision of the text above is sufficient by itself, and the
durable mechanism is measurement: `tools/rule-compliance.py` over the corpus,
`tools/rule-tests/` over real sampled responses. Run them periodically; a number
that is falling gets attention before it reaches zero. **This is the
recommendation to keep if only one survives.**

Baseline recorded 2026-08-04: status table 81 %, advisory 69 %, decisions 56 %,
Before/After 12 %, user-scenario 0 %, ambiguity raised 6 %, structure 69 %.

---

# Two further proposals, unrelated to interaction

Kept here only so they live in a tracked file rather than in an untracked HTML
document. They belong to `ai_common.md`'s engineering sections, not to the
contract above.

## P15 register — a new hazard class

> - **Privileged paths no configuration can open.** An agent's own configuration
>   directory — settings, hooks, permission allow-lists — refuses unattended
>   writes BY DESIGN, and cannot be made writable through the very config it
>   protects. The failure is not the refusal but the diagnosis: it presents as a
>   permissions bug, is "fixed" in settings, appears to work, and returns. Treat
>   a prompt that survives a correct-looking config change as a boundary, not a
>   bug, and move the work out rather than widening the permission.

## Preamble extension — tooling is generic

The split is already stated three times for the written corpus and never for
code. An extension to the existing sentence rather than a sixteenth principle:

> Applies to every project inheriting from AUR; project `CLAUDE.md` files add
> specifics and must not duplicate this. **The same split governs code, not only
> prose: a conceptual framework — the document system and its locations, the
> build system's design and tools, the test harness, the console — is AUR's and
> is inherited, while what a project DOES is the project's. A project-local fork
> of AUR tooling is a debt to reconcile, never a variant to maintain.**
