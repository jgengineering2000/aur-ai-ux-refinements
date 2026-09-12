---
name: rd-regrounding
description: Read when an R&D session on a hard perceptual or algorithmic discrimination problem is stuck, looping on statistics, about to propose a gate/discriminator/threshold, or opening a new R&D turn on such a problem — the expert-system frame (properties scored at macro/mid/micro altitude, never one scalar), the regrounding protocol (look → literature → unit and altitude → real corpus → refute-and-record), the stop rule, and the pointer to the project's own insight index.
---

# R&D Regrounding — the expert-system frame and the protocol

Read this when you notice any of: the same statistic re-derived under a new
name; a third gate proposed in three days; a number quoted before a picture
was opened; a proposal that starts from a file or a section number instead
of from what the thing is FOR; a session that has stopped being productive
and cannot say why. Each of those is the moment this skill exists for.

The project-specific half — the insights, findings, constants, corpus and
instruments of one problem — lives in that project's own index skill
(`<project>/.claude/skills/<domain>-insights/SKILL.md` — a posterization
problem's index is its project's `posterization-insights`). This skill is
the method; that one is the memory. Read both.

## 1 · The frame (owner, 2026-09-12)

A hard perceptual discrimination is solved the way an **old-school expert
system** solves it, not the way a single detector does:

- **Properties at three altitudes.** Every candidate case has known
  properties at the **macro** level (what the object is: a regular
  manufactured structure, a shaded cylinder, a sky), the **mid** level (the
  structure's units: contours, bands, cycles, chains and their geometry),
  and the **micro** level (per-pixel: steps, flatness, noise, chroma). A
  property that is real at one altitude can be invisible at another — that
  is not a contradiction, it is the reason there are three.
- **Relativistic scoring, never one scalar.** Each property contributes
  *evidence for* one reading of the case against the others; the decision
  is the accumulated weight, and no single property is asked to decide.
  Every gate that failed in this project's history failed because one
  scalar was asked to carry the whole verdict at one altitude.
- **Signal density and distribution are the foundation.** Where the signal
  is, how densely, how it is distributed across the frame — these are the
  first properties measured, because every finer property is read relative
  to them (a step count means nothing until the noise-step density of the
  frame is known).
- **Layering already exists in the product** where a stage reads context at
  several radii and scores departure from each — a multi-scale stage is the
  macro/mid/micro ladder without the scoring. Look for that shape before
  inventing a new one.
- **The division of labour.** The model brings what a trained network has
  learned — the literature, the mechanisms, the instruments; the owner
  brings vision — what the eye knows, the definition, the ruling. The
  product is an expert system built from the first and steered by the
  second. Neither half substitutes for the other: a definition the owner
  states is authority; a statistic the model derives is a lead.

## 2 · The protocol — in this order, every time

1. **Name the trunk before the leaf.** Ask what the item is FOR (the user
   action, the visible defect, the outcome) before touching what it is IN.
   Work that starts from a grep hit, a file or a task-list number inherits
   an altitude it never chose.
2. **LOOK, then measure.** Render the intermediate — the mask, the map, the
   folded cycle, the contour lines — as a picture and open it BEFORE
   computing anything about it. A metric written before the defect is
   known is blind to it by construction; it may say *how much* and *where
   else*, never *whether*. Measuring first sets the agenda and the eye
   never reaches what the number did not point at. If a stage has no
   viewable artifact, building one is the first task.
3. **Literature before derivation.** You are not the first to look. Name
   the method's literature domain and its documented failures outside it,
   pull the equations (the PDF, not the abstract), and write the "what the
   field settled / where it stops" paragraph into the finding. Where the
   field stops is itself a finding — it tells you which altitude nobody has
   published.
4. **State the UNIT and the ALTITUDE.** Before any statistic: in which unit
   does this property live (a pixel step, a plateau-to-plateau transition,
   a linked chain, a smooth wave, a region) and at which altitude (per
   pixel, along a line, the object)? Then check that the same window can
   hold both cases in their own units. Two things the eye separates
   instantly may differ only in *which unit to read them in* — and
   knowing which unit to read is the recognition itself, which no
   statistic inside one unit can supply.
5. **The eye knows, so derive it.** A detector that cannot separate two
   cases the eye separates has a missing characteristic, never a missing
   possibility. But the corollary cuts both ways: a failed scalar indicts
   the instrument, not the world — so when scalars fail, draw the map and
   look at it, and when three altitudes of maps agree, believe them.
6. **Sizes are derived, never assumed.** Every window, period, phase and
   radius comes from a measurement on the frame in hand; a constant in
   pixels is a claim about one data set, and recurring round numbers (8,
   64) are the warning sign. A value at the search's ceiling is the ceiling,
   not a measurement — re-run at a window that holds three of the thing.
7. **The real corpus, cases combined in one frame.** Synthetic controls
   prove geometry and catch regressions; they cannot stand in for a real
   source, and a corpus that separates the cases hides the disqualifying
   one (the arm that wins on texture and wrecks a face, in different files).
   Rank the subjects the way the product's user does and review by that
   rank, not by maximum gradient.
8. **Correct only what is discernible; detect everything.** The threshold
   below which a correction costs more signal than it recovers is a gate on
   *correcting*, keyed to the eye, never on detecting.
9. **Refute and record, structurally.** Every dead end goes to the ledger
   with *why it fails* stated structurally enough to retire its obvious
   variants; nothing is deleted; a superseded result is marked in place.
   The next session reads the ledger before proposing, and a proposal that
   re-derives a recorded refutation is a defect of the proposer.
10. **Delegate the mechanical, keep the looking.** Renders, strips, metric
    tables and extraction go to the cheapest capable model in a clean
    session with a self-contained brief; opening the pictures and judging
    the unexpected stay with the supervisor.

## 3 · The stop rule

When every altitude the frame offers has been measured on the real corpus
and each candidate that separated a few windows failed on the whole —
**stop building and put the decision to the owner**: close with the cost
recorded, or continue on the one named residue. State what each option
gives and misses, recommend one, and stop at the question. A fourth gate
built on one window's evidence, after three refutations, is the failure
mode this rule prevents; so is the opposite — silently parking a case the
eye separates as "undiscriminable", which the owner has refused as a move.
The honest form is: *the differences are real; they live in a unit the
current instrument does not choose; here is what choosing it would cost.*

## 4 · Instrument hygiene — paid for once each

- **Pooled versus per-pixel.** A population statistic answers a per-pixel
  question with ~0 correlation and hides everything when the rule is a
  conjunction or the population is mixed. Ask the question at the unit the
  decision is made in.
- **A period is a peak after the first trough.** A smooth signal's
  autocorrelation decays from 1, so its "first local maximum" is the
  smallest lag; the still-high correlation at lag 1 is not a period.
- **Averaging along a structure reconstructs what quantisation destroyed.**
  The operation that makes a measurement robust to noisy boundaries — the
  along-band mean — also erases the staircase that made the boundary an
  artifact. Know which side of that trade the instrument is on.
- **Synthetic noise must be hashed.** A modular pattern such as
  `(a·x + b·y) mod 3` is periodic and turns a ramp into a perfect staircase;
  the test then proves the noise, not the rule.
- **Noise chains percolate on dark frames.** A step-linking instrument reads
  a dark, noisy frame as densely contoured; density must be read against the
  frame's own noise-step baseline, and orientation lock separates linked
  noise (unoriented) from linked structure (parallel).
- **A short-cut render must equal the stack.** Where a stage is run alone on
  a cached intermediate for speed, measure once that it matches the full
  pipeline to the level of pixel differences that matter, then say so with
  the render.

## 5 · Anti-patterns, named so they are recognisable in the moment

| Tell | What it is | Do instead |
|---|---|---|
| "The number says X, let me look at where" | measure-then-look | open the picture first, then bound what you saw |
| "This case needs its own rule" | the definition treated as the live branch | the definition is authority; find the gap in the implementation |
| "Period 46" on every window | a search ceiling read as a measurement | re-derive the window from the period |
| Three gates in three days, each keeping one case and losing another | one scalar per altitude, no scoring | properties at all three altitudes, weighted |
| "Parked as undiscriminable" | the eye-knows rule refused | draw the map; name the unit; then decide with the owner |
| A proposal identified by section number | inherited altitude | name what it is FOR first |
| A dead end argued rather than recorded | the next session pays again | ledger row, structural why, ID cited |
