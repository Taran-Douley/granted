#!/usr/bin/env bash
# Build every folder the demo shows, ahead of time.
#
# A run makes five to nine model calls and takes minutes. That is fine for a
# background agent on a cron and fatal in front of an audience, so nothing here
# is left to demo time: this builds the finished states, and the demo shows them.
# Run one command live if you want to prove it is real -- the silence case is the
# fastest and the most on-message.
#
#   ./demo/prepare.sh            # ~13 minutes, needs AWS credentials
#   ./demo/prepare.sh --live     # rebuild only the live folder (beat 6), ~7 minutes
#   ./demo/prepare.sh --check    # verify what is already built, make nothing
set -euo pipefail
cd "$(dirname "$0")/.."

FUNDER=GB-CHC-274100
OUT=demo/runs
GR="${GR:-./.venv/bin/granted}"
# Not bare `python`: many machines only have python3, and the venv is where
# granted and its dependencies actually live.
PY="${PY:-./.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3 || command -v python)"
MOVED=/tmp/granted-calls-moved.json

green() { printf '  \033[32m%s\033[0m %s\n' "ok" "$1"; }

# Bedrock returns a transient internalServerException often enough to matter,
# and with `set -e` one of them costs the entire build. Retry twice before
# giving up: three runs is cheaper than rebuilding four folders at midnight.
run_step() {
  local label="$1"; shift
  local attempt
  for attempt in 1 2 3; do
    if "$@"; then return 0; fi
    printf '  \033[33m--\033[0m %s failed (attempt %s/3), retrying\n' "$label" "$attempt"
    sleep 5
  done
  printf '  \033[33m--\033[0m %s failed three times. Not a transient error.\n' "$label"
  return 1
}
warn()  { printf '  \033[33m%s\033[0m %s\n' "--" "$1"; }

if [[ "${1:-}" == "--check" ]]; then
  echo
  bad=0
  # A folder that exists is not a folder that demos. The silence case must be
  # silent; the other two must actually carry the decision the demo shows. This
  # printed "ok ... (0 workspace/s)" once -- reporting the number that proved it
  # was broken, next to the word ok.
  for d in silence decision drift; do
    md=""; for f in "$OUT/$d"/digests/*.md; do [[ -f "$f" ]] && { md="$f"; break; }; done
    if [[ ! -f "$OUT/$d/dashboard.html" || -z "$md" ]]; then
      warn "$OUT/$d missing - run ./demo/prepare.sh"; bad=1; continue
    fi
    n=0; for w in "$OUT/$d"/workspaces/*/; do [[ -d "$w" ]] && n=$((n + 1)); done
    case "$d" in
      silence)
        if grep -q "Nothing to report" "$md"; then green "$OUT/$d - silent, as it should be"
        else warn "$OUT/$d - expected silence, got a decision"; bad=1; fi ;;
      decision)
        if [[ "$n" -ge 1 ]] && grep -q "One decision" "$md"; then
          green "$OUT/$d - one decision, $n workspace(s)"
        else
          warn "$OUT/$d - NO DECISION (beats 4 and 5 have nothing to show). Rebuild."; bad=1
        fi ;;
      drift)
        # Beat 7 needs the alert. Whether a decision sits under it does not matter.
        if ! grep -q "in flight" "$md"; then
          warn "$OUT/$d - no in-flight alert; beat 7 has nothing to show"; bad=1
        elif grep -q "$OUT/decision" "$md"; then
          warn "$OUT/$d - alert points into $OUT/decision. Rebuild."; bad=1
        else
          green "$OUT/$d - in-flight alert, $n workspace(s)"
        fi ;;
    esac
  done
  # Beat 6 shows a real, installed folder: every live source, one morning.
  LIVE="$OUT/live/Granted"
  note=""; for f in "$LIVE/Daily notes"/*.md; do [[ -f "$f" ]] && { note="$f"; break; }; done
  if [[ -z "$note" || ! -f "$LIVE/Dashboard.html" ]]; then
    warn "$LIVE missing - run ./demo/prepare.sh --live"; bad=1
  elif ! grep -q "Funders active near you" "$note"; then
    warn "$LIVE - no local funders in its note; beat 6 loses its last point. Rebuild with --live."; bad=1
  else
    n=0; for w in "$LIVE"/Bids/*/; do [[ -d "$w" ]] && n=$((n + 1)); done
    green "$LIVE - $(grep -o '[0-9]* calls considered' "$note" | head -1), $n bid folder(s)"
  fi
  # No beat reads data/run.json; only the published dashboard does. Worth
  # knowing, not worth failing the check on.
  missing=0
  for d in silence decision drift; do [[ -f "$OUT/$d/data/run.json" ]] || missing=$((missing + 1)); done
  [[ "$missing" -eq 0 ]] && green "run records present - the published dashboard can show these runs" \
                         || warn "$missing run(s) have no data/run.json - fine for the demo; rebuild to publish them"
  [[ -f ORG.demo.md ]] && { warn "ORG.demo.md exists - beat 2 will refuse. rm it."; bad=1; } \
                       || green "no ORG.demo.md - beat 2 will work"
  echo
  [[ "$bad" -eq 0 ]] || { printf '  \033[33m%s\033[0m\n\n' "Not ready to demo."; exit 1; }
  green "Ready to demo."; echo
  exit 0
fi

# Beat 6: a real Granted folder, set up and run against every live source.
build_live() {
  local live="$OUT/live/Granted"
  rm -rf "$OUT/live"
  "$GR" setup --home "$live" --name "Rye Lane Community Kitchen" \
        --archive fixtures/rye-lane-funding >/dev/null
  # The Southwark ORG.md carries the registered number and area the run needs.
  cp demo/ORG.southwark.md "$live/ORG.md"
  run_step live "$GR" run --home "$live" --quiet
  green "$live"
}

if [[ "${1:-}" == "--live" ]]; then
  echo
  echo "Rebuilding the live folder only: every live source, real model calls, ~7 minutes."
  echo
  build_live
  exit 0
fi

echo
echo "Preparing the demo. This makes real model calls and takes a few minutes."
echo

rm -rf "$OUT"; mkdir -p "$OUT"

echo "1/6  archives"
"$PY" -m granted.dashboard --out . >/dev/null 2>&1 || true
"$PY" scripts/make_synthetic_archive.py --profile easton   >/dev/null
"$PY" scripts/make_synthetic_archive.py --profile rye-lane >/dev/null
green "fixtures/easton-funding and fixtures/rye-lane-funding"

echo "2/6  silence  - a Bristol org against a London funder"
run_step silence $GR run --funder "$FUNDER" --org ORG.example.md \
        --calls demo/calls.json --archive fixtures/easton-funding \
        --out "$OUT/silence" --cap 300 --quiet
green "$OUT/silence"

echo "3/6  decision - a Southwark org, same funder"
run_step decision $GR run --funder "$FUNDER" --org demo/ORG.southwark.md \
        --calls demo/calls.json --archive fixtures/rye-lane-funding \
        --out "$OUT/decision" --cap 300 --quiet
green "$OUT/decision"

echo "4/6  drift    - the same bid, after the funder pulls the deadline forward"
# Built from scratch, not copied from demo/runs/decision. The register stores
# each workspace path as it was written, so a copied folder carries paths
# pointing back at the folder it came from -- and beat 6's alert then links a
# viewer into the wrong demo. Two runs: one to put the bid in flight, one
# after the funder moves the deadline.
run_step "drift (first pass)" $GR run --funder "$FUNDER" --org demo/ORG.southwark.md \
        --calls demo/calls.json --archive fixtures/rye-lane-funding \
        --out "$OUT/drift" --cap 300 --quiet
"$PY" - "$MOVED" <<'PY'
import json, sys
d = json.load(open("demo/calls.json"))
d["calls"][0]["closes"] = "2026-09-20"      # was 2026-10-23
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
run_step "drift (deadline moved)" $GR run --funder "$FUNDER" \
        --org demo/ORG.southwark.md --calls "$MOVED" \
        --archive fixtures/rye-lane-funding --out "$OUT/drift" --cap 300 --quiet
green "$OUT/drift"

echo "5/6  live     - a real folder, every live source, one morning (~7 min)"
build_live

echo "6/6  onboard  - make sure there is no ORG.md to overwrite"
rm -f ORG.demo.md
green "ready"

echo
echo "Built. Open demo/RUNBOOK.md and rehearse it once."
echo
