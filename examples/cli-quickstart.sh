#!/bin/sh
# Coordina CLI quickstart. Run after `python3 install.py`.
set -e
WS="${1:-$HOME/.local/share/coordina/intercambio}"
coordina status
# Find open conversations that could take the job
coordina conversations claude --workspace "$WS" --task "Second opinion on a plan"
# Ask a fresh Claude executor for a read-only opinion and wait up to 60 s
coordina submit claude "List the three biggest risks in this plan: ..." \
  --workspace "$WS" --mode consult --route new --rounds 1 --wait 60
