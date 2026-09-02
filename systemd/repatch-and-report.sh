#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jonathan George
#
# Root half of the A4 auto-repair: run the (root-owned) patcher, and when it
# actually changed something — or failed — publish an event file for the
# user-session notifier to explain in a dialog. A no-op run ("already
# patched") publishes nothing: a repair that repaired nothing is not news,
# and a dialog per boot would teach the user to dismiss the one that matters.
#
# The event file is the cross-boundary completion signal (AUR P15): the user
# side never guesses from timers, it reads what this wrote.

set -u
OUT=$(/usr/bin/python3 /usr/local/lib/aur/patches/vscode-spellcheck-menu.py 2>&1)
RC=$?
# Full output to the journal either way — the event file is for the dialog,
# the journal is for diagnosis.
echo "$OUT"

case "$OUT" in
  *"already patched"*)
    [ "$RC" -eq 0 ] && exit 0
    ;;
esac

mkdir -p /var/lib/aur
{
  date -Is
  echo "rc=$RC"
  echo "$OUT"
} > /var/lib/aur/vscode-repatch-event
chmod 644 /var/lib/aur/vscode-repatch-event
exit "$RC"
