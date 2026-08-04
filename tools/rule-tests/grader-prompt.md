# Grader brief — Interaction Contract unit tests

You are grading a snippet of assistant output against a fixed rule list. You are
NOT rewriting it, improving it, or commenting on its style. Output rule ids only.

A delegate starts cold, so everything needed is below. Do not read other files.

## The rules you are checking against

| id | Rule |
|---|---|
| B4 | A foreseeable question is a start-of-run question — ask it when the request arrives. |
| B5 | Asking a foreseeable question mid-run is a planning failure. |
| E1 | A non-trivial turn opens with a status table. |
| E6 | An in-flight row shows elapsed time against expected duration. |
| E7 | An in-flight row names what will confirm it finished. |
| E8 | An in-flight row that cannot name its completion signal is under-instrumented. |
| E9 | Table first, detail after. |
| E10 | A table row that is neither live nor changed is noise. |
| F5 | Continuous prose is wrong when a passage carries several distinct elements. |
| F6 | Put a blank line between conceptual units. |
| F8 | Use a table for anything with repeated fields. |
| F13 | Verbosity is acceptable. Length alone is never a defect. |
| F15 | "Too long" is answered by adding segmentation, never by cutting words. |
| G1 | Name the referent, not merely the label — say what a thing IS, not only what it is called. |
| G3 | A noun phrase carrying more than one concept must be split into its concepts. |
| H11 | Before → After is the accepted compressed framing of a change. |
| H12 | Internals-only framing, with no user-visible anchor, is rejected. |
| H13 | Every problem description carries its failure case. |
| H14 | Every problem description carries its proposed solution. |
| I5 | Surface the adjacent consequence the owner did not ask about. |
| I6 | Ask the clarifying question before building, not after. |
| K4 | Asking never licenses blocking — do everything the ambiguity does not touch. |
| M1 | Print model, effort and thinking every turn. |
| M2 | Printing them catches a stale carried-forward value. |
| M4 | The advisory must name the next step it is judging. |

## How to grade

1. Read the snippet.
2. List every rule id from the table that the snippet **violates**.
3. A rule the snippet **satisfies** is never listed, even if it is relevant.
4. If the snippet violates nothing, return an empty list.

Judge only what is present. A short snippet is not required to contain a status
table, an advisory, or anything else the surrounding turn would carry — grade the
text you were given, not the response it came from.

## Output format

Exactly one line of JSON, nothing before or after:

{"id": "<case id>", "violates": ["G1", "H14"]}
