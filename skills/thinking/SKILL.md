---
name: thinking
description: Read when you do not understand why something did or did not work — a missed prediction, a fix or change that failed its gate, a number you cannot explain, a line of work about to try "the next" approach after one failed — and before proposing a fix, experiment or design choice whose why you cannot state. The design written in mathematical symbols first (goal, progress and context grounded in it, every assumption a symbol, so a miss localises to the math or the implementation), the owner's scientific-method loop (the recursive why, written and audited; a proposal with expectations; an experiment that informs; back to the start on a miss), the checks that catch a test which cannot discriminate and a mechanism named too shallow, and when to reground with the literature or the architect.
---

# Thinking — the recursive why

This is the operational form of PROCESS §11 ("Understand before you fix").
That section is the principle; this skill is the procedure and the checks
that catch it failing in the moment.

## The owner's statement (2026-10-17)

> Literally the scientific method:
> 1. Identify the problem. Ask why, recursively, until you understand the
>    answer or have a specific test. Answer all of the whys on the way back
>    up (tail recursion).
> 2. Propose a solution with defined expectations.
> 3. Try the experiments, gathering enough information to inform 1-3.
> 4. If those expectations aren't met then go to 1.
>
> Failure is not an option, and don't keep trying random approaches without
> knowing why things did work or didn't work. Reground with the internet or
> the architect (me) if it truly seems impossible. This is what I call the
> "Thinking" skill. It's what you do when you don't understand the answer.

Earlier, the same owner: "If you don't understand why then it isn't correct
no matter what" (2026-10-12); "It's never 'bummer, that didn't work', it's
why didn't it work because I thought this would behave this way ... If an
idea/model is good then you fix it, you don't throw it out" (2026-10-13).

## The design in symbols (owner 2026-10-28)

> Always model the design in mathematical symbolism, and ground the goal,
> progress, and context in a mathematically clean version of the design.
> If the math is right then the implementation is the problem. If the
> implementation is right then the math is the problem.

- **Write the model before the chain.** The goal is a statement in the
  model's symbols; progress is which terms are realised and exercised;
  context is the variables the world supplies and their ranges. Prose
  that cannot be written this way names a gap in the design, not in the
  notation.
- **Every assumption is a symbol or a constraint.** A variable the design
  fixes silently ("the thumbnail is letterboxed") is exactly where the
  math goes wrong while every test stays green.
- **Each side is proved on its own evidence, never inferred from the
  other's failure.** The math against the goal: derive that the model,
  computed exactly, meets the goal over every value the world can supply
  (the real corpus included). The implementation against the math: a
  fixture constructed from the model, where the code's output must equal
  the model's. Only once one side is shown right does the dichotomy
  localise the fault; both sides can be wrong at once.

Paid for the same day: a placement rule mapped one image onto another as
`x_A = s ⊙ x_B + t`, with `s` taken from one fit assumption. Fixtures
built from that same model placed within 1/8 px, so the implementation
matched the math. The real inputs were `B = R_θ · F(A)`, with `θ` a
quarter-turn and `F` one of two fits; the rule had fixed both without
saying so, and the math was the problem. Written in symbols at the
start, the two missing variables are visible before any run.

## When it fires

- A prediction missed, in either direction.
- A change failed its gate, or moved the outcome less than predicted.
- The next step on the list is "the next lever / gate / approach" and the
  last one's failure is explained in one sentence.
- A number you cannot derive from the definition of what produced it.
- A test is about to run with a decision rule attached (audit the rule
  first, below).

## The loop

1. **Identify, written down.** In the work item's own progress record, a
   numbered chain. Each node is one question, answered from a source read
   in this session (code at file:line, a run's output, the spec, the
   paper), never recalled. Start at the trunk: the design in symbols
   (above), what the thing is FOR, what it measures, by its definition in
   the code, before what it did.
2. **Recurse until a leaf is deterministic**: settled by evidence on disk,
   or reduced to one specific test whose decision for each outcome is
   written before it runs.
3. **Unwind.** Restate each level with its child's answer. "It depends"
   carries its condition up. A branch that opens another problem is kept
   as a link, never dropped.
4. **Audit each claim**: does it make sense, is it true (re-read, re-look,
   re-measure), what could go wrong. Verdict per node: holds, corrected,
   unproven, refuted. A refuted node rewinds the chain to it; the
   conclusion is never patched.
5. **Propose with expectations**: the prediction, the chain node it rests
   on, and what each outcome will mean.
6. **Experiment to inform**, not only to pass or fail: collect what would
   explain a miss as well as what would confirm a hit.
7. **On a miss**: write "expected X because Y; got Z; the premise Y broke
   because …", fix the premise, go back to 1. The model is repaired at the
   broken premise, never discarded for it. First say which side broke,
   the math or the implementation, and the evidence that clears the other.

## The checks — each paid for once

- **Does the test discriminate?** Before it runs, write what EACH candidate
  cause predicts for it. If two causes predict the same outcome, the test
  cannot decide between them; redesign it. Paid for: a test split its cases
  by "within a filter's reach of an edge, or beyond it"; the arithmetic of
  the filter's radius against the geometry put every sample within the
  reach, so both causes predicted the same thing.
- **The lever smell.** A queue of approaches ordered by how many cases
  each failure class holds selects by symptom count, not by understanding.
  Before taking the next one, ask why the last one moved exactly what it
  moved. Paid for: five changes in a row, each closed with a one-level
  mechanism. One more level on the last showed it did improve the part it
  targeted (that part's separation rose), and the composite did not move
  because that part contributed little to the composite's separation in
  the first place. The lever was on the wrong term.
- **Name a mechanism at the level that predicts.** "It lifts the wrong
  cases too" describes a result; it does not say what a similar change
  would do. Ask the next why until the answer does.
- **A prediction's reason is a claim to audit.** Paid for: "precision is
  low, so the score separates weakly" broke; precision at an operating
  point is set by the prior (how rare the positives are), the separation
  by the ranking (AUC), and they were 1.6 % and 0.83.
- **Compare like with like.** Two runs that keep different populations
  differ by the population as well as the change; pair on the same items
  before reading a difference.
- **Read the definition before the behaviour.** A quantity's own formula,
  its scale and its reach (a blur's radius against the structure it reads,
  a window against the period it holds) answers more whys than any run.

## Regrounding — when the chain stops

- A node that no source on disk settles goes to the literature (the paper
  itself, not its abstract), the upstream source, or one measurement.
- When it truly seems impossible: the architect (the owner). Bring the
  chain, the node where it stops, and what each possible answer would
  change. One question.
- Never fill the wait with another untested approach.

Related: PROCESS §11 (the principle), the `rd-regrounding` skill (the frame
for perceptual discrimination problems), ai_common P2 ("a single viewing
angle breaks the design" means the model is wrong).
