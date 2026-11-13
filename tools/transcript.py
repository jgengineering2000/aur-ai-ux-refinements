#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
"""
One reader for the transcript corpus, shared by every rule instrument.

Both the compliance markers and the grading sampler need the same two facts out
of a session file: what the owner actually said, and which assistant text was
the first answer to it. Getting that wrong is not a rounding error. A tool
result is recorded as a `"user"` record, so a reader that trusts the record
type alone believes a request arrives after every tool call, and then scores
acknowledgement against a denominator of turns where no request existed. The
filter lives here once so two instruments cannot drift into disagreeing about
what a request is.
"""

import json
import re

# Harness scaffolding arrives inside the owner's own record: hook output, the
# IDE's open-file and selection notices, slash-command expansions, and the
# notification that a backgrounded task finished. It is not something the owner
# said, and a record consisting only of it is not a request.
#
# `task-notification` is the one that hides, and it is the largest: 766 records
# in this corpus BEGIN with one, so a filter that keeps them counts a request as
# having arrived every time a background job reported in, and then scores the
# acknowledgement that was never owed. It was caught by reading a sampled draw,
# where three of nine "requests" turned out to be a Monitor reporting an exit
# code — which is why the draw is read and not merely counted.
NOISE = re.compile(
    r"<(system-reminder|ide_opened_file|ide_selection|command-name"
    r"|command-message|command-args|local-command-stdout|task-notification"
    r"|cross-session-message)(?:\s[^>]*)?>"
    r".*?</\1>",
    re.S)

# Plain-text or unclosed scaffolding that can arrive inside an owner envelope
# without paired XML tags; checked only after stripping NOISE wrappers so a real
# prompt alongside an IDE/hook tag survives intact.
SCAFFOLDING_PREFIXES = (
    "[Request interrupted",
    "Caveat: The messages below",
    "<cross-session-message",
    "<task-notification",
    "<local-command-stdout",
    "<system-reminder",
)

# Rule 1's exemption is the message's FORM, not its difficulty, so the test has
# to be decidable without reading intent: the whole message is assent and
# nothing else. Anything carrying a clause beyond that is a request.
CONFIRMATION = re.compile(
    r"(go ahead|go|yes|yep|yeah|ok|okay|sure|do it|proceed|continue|carry on"
    r"|next|thanks|thank you|please do|sounds good|lgtm|approved|agreed)"
    r"([\s.,!]+(please|now|thanks|then))*[\s.,!]*", re.I)


def _origin_is_non_human(rec):
    origin = rec.get("origin")
    return (isinstance(origin, dict) and origin.get("kind") is not None
            and origin.get("kind") != "human")


def _content_texts(content):
    """Clean owner text parts from a string or block-list content field."""
    if isinstance(content, str):
        raw = [content]
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result"
               for b in content):
            return []
        raw = [b.get("text") or "" for b in content
               if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return []
    out = []
    for part in raw:
        cleaned = NOISE.sub("", part).strip()
        if not cleaned or cleaned.startswith(SCAFFOLDING_PREFIXES):
            continue
        out.append(cleaned)
    return out


def owner_items(rec):
    """Return `(text, timestamp, queued)` tuples for owner prose in one record.

    Includes typed `user` turns (`isMeta`, `isCompactSummary`, non-human origin,
    `tool_result`, and harness scaffolding excluded) as well as `queued_command`
    attachments queued while the assistant was busy.
    """
    kind = rec.get("type")
    if kind == "attachment":
        att = rec.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "queued_command":
            return []
        origin = att.get("origin")
        if isinstance(origin, dict) and origin.get("kind") != "human":
            return []
        if origin is None and att.get("commandMode") != "prompt":
            return []
        ts = att.get("timestamp") or rec.get("timestamp")
        return [(t, ts, True) for t in _content_texts(att.get("prompt"))]
    if kind != "user":
        return []
    if rec.get("isMeta") or rec.get("isCompactSummary") or _origin_is_non_human(rec):
        return []
    content = (rec.get("message") or {}).get("content")
    return [(t, rec.get("timestamp"), False) for t in _content_texts(content)]


def owner_text(rec):
    """The owner's own prose from one record, or None if it is not a request.

    None covers distinct non-request traffic arriving typed as `"user"`: tool
    results, harness or IDE injections, compaction summaries, peer subagent
    hand-backs, and scaffolding-only messages. This is the single definition.
    """
    if rec.get("type") != "user":
        return None
    parts = [t for t, _ts, _queued in owner_items(rec)]
    return "\n".join(parts).strip() or None


def is_confirmation(text):
    """True when the whole message is assent — rule 1's only exemption."""
    return bool(CONFIRMATION.fullmatch(text.strip()))


def turns(path):
    """Yield this session's owner asks and assistant replies, in order.

    Each reply carries `answers`: the ask it is the FIRST assistant text after,
    or None. Only the first qualifies, because rule 1 fires when a request
    arrives — an assistant turn resuming after its own tool call is not a
    request arriving, and counting it as one is what made the acknowledgement
    rate uninterpretable.
    """
    try:
        fh = open(path, errors="replace")
    except OSError:
        return
    pending = None
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = (rec.get("timestamp") or "")[:19].replace("T", " ")
            ask = owner_text(rec)
            if ask is not None:
                pending = ask
                yield {"kind": "ask", "text": ask, "at": ts,
                       "confirmation": is_confirmation(ask)}
                continue
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "text"):
                    continue
                text = (b.get("text") or "").strip()
                if not text:
                    continue
                yield {"kind": "reply", "text": text, "at": ts,
                       "answers": pending}
                pending = None
