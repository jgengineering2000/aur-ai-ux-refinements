#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
Measure how consistently the assistant applied its own interaction rules.

The rules in `ai_common.md` are only worth what gets applied, and the
failure mode is not a rule being wrong — it is a rule quietly going unused
while every response still looks fine. That decay is invisible turn by turn
and obvious across a corpus, so it is measured rather than recalled.

Each detector below is a MARKER, not the rule. A marker can only see a
phrasing; it cannot see whether the reasoning behind it happened. So a marker
going to zero is strong evidence of a lapse, while a marker at 100 % is weak
evidence of compliance — read the numbers in that direction and no further.

Usage:
    rule-compliance.py [--project DIR] [--since YYYY-MM-DD] [--csv]

    --project DIR   transcript directory
                    (default: active repository under ~/.claude/projects/)
    --since DATE    ignore sessions that started before DATE
    --csv           machine-readable rows instead of the table
    -h, --help      this text
"""

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcript import turns  # noqa: E402


def default_project_dir():
    """Infer the active repository's Claude transcript directory from cwd."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    root = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else Path.cwd()
    encoded = str(root.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded

# A response short enough to be an acknowledgement is not evidence of anything:
# "Done." carries no status table because it needs none, and counting it as a
# lapse would bury the real signal under noise.
SUBSTANTIVE_CHARS = 400

# marker -> (label, compiled test). Ordered for the report.
MARKERS = [
    ("table",     "status table",
     lambda t: re.search(r"^\|.+\|\s*$\n^\|[\s:|-]+\|\s*$", t, re.M) is not None),
    ("advisory",  "model-settings advisory",
     lambda t: "Model settings" in t),
    # Both of the next two read 0% across the first 399 measured turns, and that
    # was NOT purely a lapse: each regex named one phrasing and missed the shape
    # the rule actually produces. `decisions?  (needed|for you|required)` never
    # matched the `## Decisions` heading or `## Decision 1 of 3`; the scenario
    # test anchored `Why:` at line start and so missed `- **Why:**`, which is
    # what the six-step ladder emits when written as a list. A detector that
    # cannot see the compliant form reports zero forever, and a zero nobody can
    # act on is worse than no column — it looks like a measured lapse. Widened
    # 2026-08-09 so that zero now means the rule went unused.
    ("decisions", "decisions at the close",
     lambda t: re.search(r"(?i)decisions? (needed|for you|required)", t) is not None
     or re.search(r"(?im)^#{1,4}\s*decisions?\b", t) is not None),
    ("beforeaft", "Before/After framing",
     lambda t: re.search(r"(?i)\bbefore\s*(:|→|->)", t) is not None),
    ("scenario",  "user-scenario framing",
     lambda t: re.search(r"(?im)^[\s>*\-+\d.]*\*{0,2}why\*{0,2}\s*:", t) is not None),
    ("clarify",   "ambiguity raised as a question",
     lambda t: re.search(
         r"(?i)(can you clarify|which of the|i understand that you want"
         r"|you may have missed|two readings|ambiguous)", t) is not None),
    ("structure", "structural rendering (headers/bullets)",
     lambda t: re.search(r"^\s*(#{2,}\s|\s*[-*]\s|\d+\.\s)", t, re.M) is not None),
]

# Rules 1 and 2 are owed only where the owner actually said something, so they
# get their own denominator. Scored against every assistant turn they are
# uninterpretable: most substantive turns resume after a tool call, no request
# arrived, and nothing was owed — which silently caps the achievable rate at
# whatever fraction of turns happen to follow an owner message. The markers
# above keep the all-turns denominator so their series stays comparable with
# the 84-transcript baseline.
ASK_MARKERS = [
    ("understand", "restates the ask",
     lambda t: re.search(r"(?i)\bI understand that\b", t) is not None),
    ("ground",     "agrees on the ground",
     lambda t: re.search(r"(?i)\bI (agree|disagree) that\b", t) is not None),
    ("recommend",  "recommends, naming the alternative",
     lambda t: re.search(r"(?i)\bI recommend\b", t) is not None
     and re.search(r"(?i)\balternative\b", t) is not None),
]

# Rule 5 — name the referent, not the label. It was the only Interaction Contract
# rule with no detector at all, and it is the one that failed on every turn of
# 2026-08-09 while the owner asked four separate times what a named thing WAS.
#
# The rule has no checkable moment: it fires on every noun phrase, so nothing is
# structurally missing when it lapses. What IS observable is the artifact — a turn
# that asks the owner to decide about a spec section, file, symbol, memory or
# commit either shows that thing's content or only its address. An address is a
# label; the owner then has to go read it, which is the attention cost the rule
# exists to prevent.
#
# The deeper tell this catches: a label is what gets written when the content was
# never read. Every 2026-08-09 lapse was a proposal about something located by
# grep and never opened.
IDENT_RE = re.compile(
    r"§\d+(?:\.\d+)*"                          # spec section
    r"|\b\w+\.(?:md|py|rs|sh|json|toml)\b"     # filename
    r"|\b\w+::\w+"                             # module path / symbol
    r"|\b(?:project|feedback)_\w+"             # memory slug
    # A sha must contain a digit. Without that, `[0-9a-f]{7,40}` matches ordinary
    # English — "defaced" and "effaced" both did, inflating the denominator with
    # turns that named nothing.
    r"|\b(?=[0-9a-f]{7,40}\b)[a-f]*[0-9][0-9a-f]*\b")

# An explanation adjacent to the identifier. SKIP exists because identifiers are
# written inside backticks and links, so the clause never touches the match end —
# omitting it scored the turn that satisfied the owner at 0.08.
_VERB = (r"is|are|was|were|records?|says?|holds?|states?|contains?|makes?|reads?"
         r"|carr(?:y|ies)|covers?|declares?|means?|shows?|has|have|specifies"
         r"|defines?|asserts?|gives?|marks?|sets?|writes?|names?|lists?|reports?")
_SKIP = r"[`*_\)\]\"'’\s]*"
EXPLAINED_RE = re.compile(rf"^{_SKIP}(?:[—–]|:|\(|\||,?\s*(?:{_VERB})\b)")
QUOTE_AFTER_RE = re.compile(r"\n\s*(?:>\s|```)")


def referent_density(text):
    """(explained, total) identifier mentions — a SMELL, never a verdict.

    Deliberately NOT a marker in the table above. A boolean form was built and
    then tested against the nine turns of the 2026-08-09 session, where the
    owner's own reactions give ground truth, and it FAILED: "does this turn
    contain a blockquote" scored 6/9, marking the two canonical violations
    compliant because they quoted something unrelated, and marking the turn that
    finally satisfied the owner a violation because it explained in prose. This
    density form reaches 7/9 but the classes overlap — compliant turns scored
    0.31-0.47, violating turns 0.00-0.32 — so no threshold separates them.

    The distinguishing signal is whether the explanation is about the thing the
    owner must decide on, which is semantic and invisible to a regex. So rule 5's
    real measurement is the LLM grader in `rule-tests/`, which enumerates it; what
    is reported here is the raw shape — how many things a turn named and how many
    it explained. Fifty bare identifiers in one turn is informative on its own.
    """
    ids = list(IDENT_RE.finditer(text))
    if not ids:
        return 0, 0
    explained = 0
    for hit in ids:
        tail = text[hit.end():hit.end() + 110]
        if EXPLAINED_RE.search(tail.split("\n")[0]) \
           or QUOTE_AFTER_RE.search(tail[:300]):
            explained += 1
    return explained, len(ids)

# Rule 0 — characterize before you ask. Unlike rule 5 this one IS mechanically
# checkable, because the rule names literal slots rather than a quality of prose:
# either the block is present or the owner was asked to reconstruct the frame.
# Scored over turns that actually put something to the owner, since a turn asking
# nothing owes no characterization.
POSES_QUESTION_RE = re.compile(r"\?\s*$|\?\*{0,2}\s*$", re.M)
# Each slot allows a markdown heading/list prefix and intervening words, because
# the compliant form is written as a HEADING ("## Where this actually sits") and a
# regex that only matches the bare phrase reports zero against real compliance —
# the precise mistake documented for `decisions` and `scenario` above.
_LEAD = r"^[#\s>*\-+\d.|]*"
_IM = re.I | re.M
CHAR_SLOTS = (
    re.compile(_LEAD + r".{0,20}\bwhere (it|this)\b.{0,20}\bsits?\b"
               r"|\bin the (architecture|hierarchy)\b", _IM),
    re.compile(r"\bdesigned\b.{0,400}\bimplemented\b"
               r"|\bimplemented\b.{0,400}\bexercised\b", re.I | re.S),
    re.compile(r"\bwhat happens today\b|\bstatus quo\b|\btoday[,:]", re.I),
    re.compile(_LEAD + r".{0,24}\b(options?|why these|alternatives?)\b", _IM),
)

CHAR_MARKERS = [
    ("charblock", "characterization block present",
     lambda t: sum(1 for s in CHAR_SLOTS if s.search(t)) >= 3),
]

EFFORT_RE = re.compile(r"Effort\s*=\s*(\d)")
VERDICT_RE = re.compile(r"Model settings (are appropriate|change to)")


def session_rows(path):
    """(started, sid, texts, answers) for one transcript.

    `texts` is every assistant text block; `answers` is the subset that was the
    first reply to an owner message which was not pure assent — the only turns
    on which rules 1 and 2 are owed.
    """
    started, texts, answers = None, [], []
    confirmations = set()
    for turn in turns(path):
        if started is None and turn["at"]:
            started = turn["at"][:10]
        if turn["kind"] == "ask":
            if turn["confirmation"]:
                confirmations.add(turn["text"])
            continue
        texts.append(turn["text"])
        ask = turn["answers"]
        if ask is not None and ask not in confirmations:
            answers.append(turn["text"])
    return started or "?", Path(path).stem[:8], texts, answers


def names_a_referent(text):
    """Rule 5 is owed only where the turn actually named something."""
    return IDENT_RE.search(text) is not None


def analyse(files, since):
    out = []
    for f in files:
        row = session_rows(f)
        if not row:
            continue
        started, sid, texts, answers = row
        if since and started < since:
            continue
        big = [t for t in texts if len(t) >= SUBSTANTIVE_CHARS]
        if not big:
            continue
        replies = [t for t in answers if len(t) >= SUBSTANTIVE_CHARS]
        rec = {"started": started, "sid": sid, "turns": len(big),
               "asks": len(replies)}
        for key, _label, test in MARKERS:
            rec[key] = sum(1 for t in big if test(t))
        for key, _label, test in ASK_MARKERS:
            rec[key] = sum(1 for t in replies if test(t))
        # Rule 5, as a shape rather than a rate. Turns that named nothing owe
        # nothing, the same reasoning ASK_MARKERS documents above.
        named = [t for t in big if names_a_referent(t)]
        rec["named"] = len(named)
        exp = tot_ids = 0
        for t in named:
            e, n = referent_density(t)
            exp += e
            tot_ids += n
        rec["idents"] = tot_ids
        rec["explained"] = exp
        # Rule 0's denominator: turns that put something to the owner.
        asking = [t for t in big if POSES_QUESTION_RE.search(t)]
        rec["asking"] = len(asking)
        for key, _label, test in CHAR_MARKERS:
            rec[key] = sum(1 for t in asking if test(t))
        efforts, verdicts = [], []
        for t in texts:
            efforts += EFFORT_RE.findall(t)
            verdicts += VERDICT_RE.findall(t)
        rec["efforts"] = efforts
        rec["verdicts"] = verdicts
        out.append(rec)
    out.sort(key=lambda r: (r["started"], r["sid"]))
    return out


def main(argv):
    ap = argparse.ArgumentParser(
        add_help=True, description="Measure interaction-rule compliance.")
    ap.add_argument("--project", default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--csv", action="store_true")
    # argparse already refuses an unknown option, names it, and exits non-zero.
    args = ap.parse_args(argv)

    if args.since and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.since):
        ap.error(f"--since expects YYYY-MM-DD, got {args.since!r}")

    project = args.project if args.project is not None else str(default_project_dir())
    files = sorted(glob.glob(os.path.join(project, "*.jsonl")))
    if not files:
        print(f"no transcripts under {project}", file=sys.stderr)
        return 1

    rows = analyse(files, args.since)
    if not rows:
        print("no sessions matched", file=sys.stderr)
        return 1

    keys = [k for k, _l, _t in MARKERS]
    if args.csv:
        # Every group the table reports, including each group's own denominator
        # — a csv carrying fewer columns than the table is the same defect as a
        # stage reporting green having examined nothing.
        acols = [k for k, _l, _t in ASK_MARKERS]
        print("started,session,turns," + ",".join(keys)
              + ",asks," + ",".join(acols) + ",named,idents,explained,asking,charblock")
        for r in rows:
            print(f"{r['started']},{r['sid']},{r['turns']},"
                  + ",".join(str(r[k]) for k in keys)
                  + f",{r['asks']}," + ",".join(str(r[k]) for k in acols)
                  + f",{r['named']},{r['idents']},{r['explained']}"
                  + f",{r['asking']},{r['charblock']}")
        return 0

    hdr = f"{'started':<11}{'session':<10}{'turns':>6}  " + "".join(
        f"{k:>11}" for k in keys)
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        cells = "".join(f"{100 * r[k] / r['turns']:>10.0f}%" for k in keys)
        print(f"{r['started']:<11}{r['sid']:<10}{r['turns']:>6}  {cells}")

    print("-" * len(hdr))
    tot = sum(r["turns"] for r in rows)
    cells = "".join(f"{100 * sum(r[k] for r in rows) / tot:>10.0f}%" for k in keys)
    print(f"{'ALL':<11}{'':<10}{tot:>6}  {cells}")

    # Rules 1-2 against their own denominator. Reported apart from the table
    # above rather than as extra columns in it, because a percentage is only
    # readable next to the population it was taken over.
    akeys = [k for k, _l, _t in ASK_MARKERS]
    print("\nrules 1-2 — scored only on substantive replies to a real request "
          "(pure assent exempt)")
    ahdr = f"{'started':<11}{'session':<10}{'asks':>6}  " + "".join(
        f"{k:>12}" for k in akeys)
    print(ahdr)
    print("-" * len(ahdr))
    for r in rows:
        if not r["asks"]:
            print(f"{r['started']:<11}{r['sid']:<10}{0:>6}  "
                  + "".join(f"{'—':>12}" for _ in akeys))
            continue
        cells = "".join(f"{100 * r[k] / r['asks']:>11.0f}%" for k in akeys)
        print(f"{r['started']:<11}{r['sid']:<10}{r['asks']:>6}  {cells}")
    print("-" * len(ahdr))
    atot = sum(r["asks"] for r in rows)
    if atot:
        cells = "".join(
            f"{100 * sum(r[k] for r in rows) / atot:>11.0f}%" for k in akeys)
        print(f"{'ALL':<11}{'':<10}{atot:>6}  {cells}")

    # Rule 0 first, because it outranks the rest: a well-structured turn asking
    # about an uncharacterized item is still the failure.
    print("\nrule 0 — characterization, scored only on turns that ask the owner "
          "something")
    chdr = f"{'started':<11}{'session':<10}{'asking':>7}{'charblock':>11}"
    print(chdr)
    print("-" * len(chdr))
    for r in rows:
        if not r["asking"]:
            print(f"{r['started']:<11}{r['sid']:<10}{0:>7}{'—':>11}")
            continue
        print(f"{r['started']:<11}{r['sid']:<10}{r['asking']:>7}"
              f"{100 * r['charblock'] / r['asking']:>10.0f}%")
    print("-" * len(chdr))
    ctot = sum(r["asking"] for r in rows)
    if ctot:
        cnum = sum(r["charblock"] for r in rows)
        print(f"{'ALL':<11}{'':<10}{ctot:>7}{100 * cnum / ctot:>10.0f}%")

    # Rule 5 as a SHAPE, not a rate — see referent_density()'s docstring for the
    # ground-truth test that rejected the boolean form. Printed as counts so it
    # cannot be misread as a compliance percentage: `ident/turn` is how many
    # things were named, `expl` how many carried an adjacent explanation. A high
    # count with a low fraction is the smell the owner reads as "what are you
    # talking about"; the graded verdict belongs to rule-tests/.
    print("\nrule 5 — shape only, NOT a verdict (semantic; graded in "
          "rule-tests/). Turns naming a file, section, symbol, memory or commit")
    rhdr = (f"{'started':<11}{'session':<10}{'named':>6}{'idents':>8}"
            f"{'ident/turn':>12}{'expl':>7}{'expl%':>7}")
    print(rhdr)
    print("-" * len(rhdr))
    for r in rows:
        if not r["named"] or not r["idents"]:
            print(f"{r['started']:<11}{r['sid']:<10}{0:>6}{'—':>8}"
                  f"{'—':>12}{'—':>7}{'—':>7}")
            continue
        print(f"{r['started']:<11}{r['sid']:<10}{r['named']:>6}"
              f"{r['idents']:>8}{r['idents'] / r['named']:>12.1f}"
              f"{r['explained']:>7}{100 * r['explained'] / r['idents']:>6.0f}%")
    print("-" * len(rhdr))
    rnamed = sum(r["named"] for r in rows)
    rids = sum(r["idents"] for r in rows)
    rexp = sum(r["explained"] for r in rows)
    if rnamed and rids:
        print(f"{'ALL':<11}{'':<10}{rnamed:>6}{rids:>8}{rids / rnamed:>12.1f}"
              f"{rexp:>7}{100 * rexp / rids:>6.0f}%")

    # The advisory is the one rule that can be present and still not be
    # followed: printing it every turn satisfies the marker while saying
    # nothing. Its spread across the corpus is what distinguishes an evaluation
    # from a rubber stamp, so it is reported separately from the rate above.
    efforts = [e for r in rows for e in r["efforts"]]
    verdicts = [v for r in rows for v in r["verdicts"]]
    print()
    if efforts:
        dist = {v: efforts.count(v) for v in sorted(set(efforts))}
        print("advisory — Effort values printed: "
              + ", ".join(f"{k}×{v}" for k, v in dist.items()))
        top = max(dist.values()) / len(efforts)
        print(f"  most-repeated value is {100 * top:.0f}% of all advisories"
              + ("   <- rubber stamp" if top > 0.8 else ""))
    if verdicts:
        same = verdicts.count("are appropriate")
        print(f"advisory — 'are appropriate' {same}, "
              f"'change to' {len(verdicts) - same}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
