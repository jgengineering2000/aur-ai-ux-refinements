#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
commit-guard — check staged content for PII and cross-project leakage.

Written because the alternative is a manual audit before every commit, and a
check performed by remembering to perform it is one that eventually is not.

    python3 tools/commit-guard.py <repo>            # staged content (default)
    python3 tools/commit-guard.py <repo> --all      # whole worktree
    python3 tools/commit-guard.py <repo> --quiet    # exit code only

Exit 0 clean, 1 findings, 2 usage. Two classes of finding:

  PII / external data — absolute home paths, addresses, and references into the
  user's personal tree. The policy is not "no personal information in obvious
  places" but "data sourced from outside the project tree, AND ITS DERIVATIVES —
  manifests, indexes, paths, basenames — never lands in a commit." Basenames are
  the ones that get missed, because they look like ordinary strings.

  Cross-project leakage — a file owned by shared infrastructure that names one
  consuming project. Shared code that knows a project's name has already forked;
  it just has not been noticed yet.

Both are reported with file:line so the finding is actionable rather than a
verdict.

THE CEILING (ai_common.md, PII): git's world ends at the project's own path,
and everything above it is PII by construction — the user's home, the machine's
layout, and the project's OWN absolute location. So the PII scan is content-only
and covers every added line of every tracked path: no exempt directory class, no
allowlisted file. Until 2026-09-03 this guard skipped dated records, memory,
generated documents, transcripts and a list of "user-level config" files on the
theory that rewriting a record is falsification. It is not: a record's truth is
preserved by the mapping that lives outside git, and replacing a real path with
its anonymized name is what the record should have said. Every one of those
skips was where a path got in.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

HOME = str(Path.home())
USER = Path.home().name

# Personal tree: directories whose contents are the user's, never the project's.
# THE EXCLUDING NEIGHBOURS of the private-IPv4 rule. Owner ruling 2026-10-28
# (second, replacing the first): "modify the rule to exclude double quotes, or
# a terminal period or semi-colon. There can always be some corner case. We
# just have to have a sane baseline." A four-part private dotted number is NOT
# counted when the character before or after it is one of these (a quoted or
# clause-style citation, not an address), when a period follows it, or when it
# is a piece of a longer dotted number (a fifth part, or a fused digit). Every
# other neighbour counts. This is the one definition the rule reads.
UNCOUNTED_NEIGHBOURS = '";'
_X = re.escape(UNCOUNTED_NEIGHBOURS)

# Owner ruling 2026-10-28 (third): "We can exclude ({A}) and {{A}} for example
# but count (x={A}) because patterns can consider the range". A number that is
# the WHOLE content of a matching bracket pair is not counted; inside a pair
# with anything else beside it, the neighbour rule decides. The class beyond
# () and {} - [] - is the supervisor's reading of the ruling. (open, close).
EXCLUDED_PAIRS = (("(", ")"), ("{", "}"), ("[", "]"))

_ADDR = r"(?:192\.168|10\.\d+|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+"
_NOT_PAIRED = "".join(
    rf"(?:(?<!{re.escape(o)})|(?={_ADDR}(?![\d{re.escape(c)}])))"
    for o, c in EXCLUDED_PAIRS)

PERSONAL_DIRS = ("Pictures", "Documents", "Downloads", "Desktop", "Videos",
                 "Music", "git-backups", ".ssh", ".gnupg")

PII = [
    ("absolute home path", re.compile(rf"{re.escape(HOME)}/")),
    ("home path (~ form)", re.compile(rf"/home/{re.escape(USER)}\b")),
    # a path encoder (dashes, %2F, underscores) carries the username outside every slash-form pattern
    ("username (any encoding)",
     re.compile(rf"(?<![A-Za-z0-9]){re.escape(USER)}(?![A-Za-z0-9])", re.I)),
    # Extension ids (foo@ubuntu.com, bar@gnome.org) share the email shape and
    # are not addresses; excluding the known suffixes beats a cleverer regex.
    # An scp-style git remote (git@host:org/repo.git) has the same shape too,
    # and appears legitimately in any code that talks about remotes — flagging
    # it is the cry-wolf failure that teaches people to skip the guard. The
    # trailing ':' is what distinguishes a host from a mailbox.
    ("email address",
     re.compile(r"(?![\w.+-]+@(?:ubuntu\.com|gnome\.org|localhost)\b)"
                r"(?<![\w.+-])(?!git@[\w.-]+:)"
                r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    ("personal tree reference",
     re.compile(rf"[~/]\s*(?:{'|'.join(PERSONAL_DIRS)})/")),
    ("private IPv4", re.compile(
        rf"(?<![{_X}\d])(?<!\d\.){_NOT_PAIRED}{_ADDR}(?![{_X}.\d])")),
]

# Dated records may legitimately NAME a consuming project (a checkpoint says
# which project the session was in), so the cross-project LEAKAGE check skips
# them. The PII scan skips nothing — see the docstring.
LEAKAGE_EXEMPT = ("checkpoints/", "checkpoints-archive/", "/rule-tests/",
          "pending-insights.md", "/.claude/projects/", "aur-docs/",
          "claude-docs/", "/memory/", "transcript")



# A remote web address is not a folder on this machine. The personal-tree rule
# judges what a string NAMES, not where it sits, and the path of a URL on a
# remote host (https://example.org/Documents/x) names a document on that host,
# nothing of the user's. This is NOT an exemption class - no directory, file or
# line is exempt (see THE CEILING): the same text as a bare path, a file:// URL,
# a localhost / 127.x.x.x / [::1] URL, or elsewhere on the same line is still a
# finding. Applies to the personal-tree rule only; the home-path, email and
# private-IPv4 rules still read the whole line, URLs included.
URL_SPAN = re.compile(r"https?://[^\s|)\]>\"'`]+", re.I)
LOCAL_HOST = re.compile(r"(?:localhost|127(?:\.\d+){3}|\[::1\])$", re.I)


def without_remote_urls(line):
    """The line with each remote http(s) URL span replaced by a placeholder."""
    def blank(m):
        authority = re.split(r"[/?#]", m.group(0).split("://", 1)[1], maxsplit=1)[0]
        host = authority.rsplit("@", 1)[-1]
        if not host.endswith("]"):
            host = re.sub(r":\d*$", "", host)
        return m.group(0) if LOCAL_HOST.match(host) else "URL"
    return URL_SPAN.sub(blank, line)


def tracked_projects():
    """Consuming project names, from the shared-root registry."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import aurshare
        return [Path(r["path"]).name for r in aurshare.registry_read()]
    except Exception:
        return []


def added_lines(repo, mode):
    """Lines this change ADDS, as {path: [(lineno, text), ...]}.

    Scanning whole files was the first version and it was wrong: every commit
    reported the same pre-existing findings, and a guard that cries wolf on
    legacy content is one you learn to skip. Only what the change introduces is
    actionable; what was already there is a separate, deliberate cleanup.
    """
    args = ["git", "-C", repo, "diff", "--unified=0"]
    if mode == "staged":
        args += ["--cached"]
    elif mode.startswith("range:"):
        # Everything a push would introduce. The pre-push hook uses this: the
        # commit-time scan is bypassable with --no-verify, and this stage is
        # where the content stops being recoverable by a cheap history rewrite.
        # Split, because a new branch's range is multi-token
        # ("<oid> --not --remotes=router"), not a single A..B revision.
        args += mode[len("range:"):].split()
    else:
        args += ["HEAD"]
    # A binary with no NUL byte diffs as text; its bytes need not be UTF-8, and
    # a decode error here would end the scan with no verdict at all.
    out = subprocess.run(args, capture_output=True, text=True,
                         errors="replace").stdout
    files, cur, lineno = {}, None, 0
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            files.setdefault(cur, [])
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++") and cur:
            files[cur].append((lineno, line[1:]))
            lineno += 1
    return {k: v for k, v in files.items() if v}


def project_name(repo, log=print):
    """The project's name: the directory of the MAIN worktree. A linked
    worktree is named <proj>-<branch>, which is not the name the project's
    own XDG folders carry. Falls back to the given directory's name, logged."""
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True)
    common = r.stdout.strip()
    if r.returncode == 0 and common:
        return Path(common).parent.name
    fallback = Path(repo).resolve().name
    log(f"commit-guard: project name for {repo} from directory name `{fallback}`: "
        f"git --git-common-dir failed (rc={r.returncode}: {r.stderr.strip()[:100]})")
    return fallback


def pii_patterns(repo, log=print):
    """The PII patterns for THIS repository: the fixed set plus the project's
    own absolute path. A project under $HOME is already caught by the home
    pattern; one elsewhere (/srv, /mnt, a temp dir) is not, and its own
    location is above the ceiling all the same."""
    project = str(Path(repo).resolve())
    pats = list(PII)
    # Home-relative forms (a tilde-slash path, the HOME variable in either
    # brace form) name the user's home as surely as the absolute path. Allowed
    # under it: the claude config dir (not flagged before this pattern existed)
    # and the repository's OWN XDG dirs, <name> or <name>-<suffix>, with <name>
    # taken from the repo directory so no project is hard-coded. The lookbehind
    # leaves URLs, "foo" glued to a tilde, and "~5 ms" alone; a bare tilde-slash
    # with no path after it is prose.
    own = re.escape(project_name(repo, log))
    pats.append(("home-relative path",
                 re.compile(rf"(?<![\w/.~$}}-])(?:~|\$HOME|\$\{{HOME\}})/"
                            rf"(?!(?:\.claude(?![\w-])|(?:\.config|\.local/share|\.cache)"
                            rf"/{own}(?!\w)))(?=[\w.])")))
    if not project.startswith(HOME + "/"):
        pats.insert(0, ("project path (above the ceiling)",
                        re.compile(re.escape(project) + r"(/|\b)")))
    return pats


def scan(repo, added, projects, log):
    findings = []
    repo_name = project_name(repo, log)
    pats = pii_patterns(repo, log)
    for rel, lines in added.items():
        leak_exempt = any(k in f"/{rel}" for k in LEAKAGE_EXEMPT)
        # Shared infrastructure: anything AUR owns and every project inherits.
        shared = (repo_name == "aur" and not leak_exempt
                  and not rel.startswith((".claude/", "aur-docs/")))

        for n, line in lines:
            for label, pat in pats:
                text = (without_remote_urls(line)
                        if label == "personal tree reference" else line)
                if pat.search(text):
                    findings.append(("PII", rel, n, label, line.strip()[:100]))
                    break
            if shared:
                for proj in projects:
                    if proj in ("aur",) or len(proj) < 3:
                        continue
                    if re.search(rf"\b{re.escape(proj)}\b", line, re.I):
                        findings.append(("LEAK", rel, n,
                                         f"names project '{proj}'",
                                         line.strip()[:100]))
                        break
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--all", action="store_true", help="whole worktree, not just staged")
    ap.add_argument("--range", metavar="A..B",
                    help="everything a push would introduce (pre-push hook)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.all and a.range:
        print("commit-guard: --all and --range are mutually exclusive",
              file=sys.stderr)
        return 2

    repo = str(Path(a.repo).expanduser().resolve())
    # Ask git, not the filesystem: a linked worktree's .git is a FILE, and the
    # is_dir() test that stood here refused every worktree as "not a repo" —
    # exit 2, which the pre-commit hook reads as nothing-staged and waves
    # through. A guard that cannot run must refuse loudly, not quietly pass.
    probe = subprocess.run(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        print(f"commit-guard: not a git repo: {repo}", file=sys.stderr)
        return 2

    mode = f"range:{a.range}" if a.range else ("all" if a.all else "staged")
    added = added_lines(repo, mode)

    # An empty staged set is not a pass. Reporting "clean" here is the exact
    # failure this tool exists to prevent — a guard that examined nothing and
    # said so in the vocabulary of approval. It happens routinely, because
    # git-scope.py does not stage until commit time, so the documented
    # "guard, then commit" order runs the guard against an empty index.
    if not added and mode == "staged":
        # "No added lines" is NOT "nothing staged". A pure-deletion commit
        # stages plenty and adds nothing, and is legitimately clean — this
        # scanner only ever reads added lines. Ask git what is staged rather
        # than inferring it from this tool's own empty result.
        staged_any = subprocess.run(
            ["git", "-C", repo, "diff", "--cached", "--name-only"],
            capture_output=True, text=True).stdout.strip()
        if staged_any:
            if not a.quiet:
                print(f"commit-guard: {Path(repo).name} — "
                      f"{len(staged_any.splitlines())} file(s) staged, no added "
                      f"lines to scan (deletions only)\n  clean")
            return 0
        print("commit-guard: NOTHING STAGED — this is not a clean result, it is "
              "no result.\n  Stage the work first (`git add -A -- <paths>`), or "
              "pass --all to scan the\n  worktree. git-scope.py stages at commit "
              "time, so run the guard after\n  staging, not before.",
              file=sys.stderr)
        return 2

    projects = tracked_projects()
    findings = scan(repo, added, projects, print)

    if not a.quiet:
        n_lines = sum(len(v) for v in added.values())
        print(f"commit-guard: {Path(repo).name} — {len(added)} file(s), "
              f"{n_lines} added line(s), {mode}")
        if not findings:
            print("  clean — no PII, no external-data references, no cross-project leakage")
        else:
            for kind, rel, n, label, text in findings:
                print(f"  [{kind}] {rel}:{n}  {label}")
                print(f"         {text}")
            print(f"\n  {len(findings)} finding(s). Each is a location, not a verdict — "
                  f"read it before acting.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
