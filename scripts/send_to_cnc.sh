#!/usr/bin/env bash
#
# send_to_cnc.sh — send a G-code job to the Carvera Air CNC over WiFi using
# carvera-cli (vendored at vendor/carvera-cli, installed into python/venv per
# docs/carvera-air.md). Optionally unlock, home, and auto-Z-probe the stock
# before starting the cut.
#
# LOCAL-ONLY — run this standing at the lab PC, from the repo root, e.g.:
#   scripts/send_to_cnc.sh cam/brass_holes/brass_holes.gcode --home --auto-z-probe 5 5
#
# Never run this from CI or any unattended automation. This script can
# START a cut and move the machine. Only drive the machine from a session
# physically at the Carvera.
#
# ── WHAT IT DOES (in order) ─────────────────────────────────────────────────
#   1. --unlock : M999 — clear an Alarm/HALT. Required after an e-stop or a
#      stall before the machine will move ("...before doing anything").
#   2. Upload the job to /sd/gcodes/<basename> (--overwrite) and VERIFY it
#      landed. We never `play` a stale/partial file — a broken-pipe upload that
#      silently leaves yesterday's file on the SD card is how you cut the wrong
#      program (hard-won; the run is gated on a confirmed upload).
#   3. --home : G28 — Carvera "goto clearance position"; lifts Z first, so it's
#      safe even with the tool down in the work. ($H is rejected on this fw.)
#   4. --auto-z-probe DX DY : M495 X<xmin> Y<ymin> O<DX> F<DY> P1 — auto-Z-probe
#      the stock top DX/DY from the job's path-start corner (the gcode's X/Y
#      bounding-box minimum) and rewrite the work Z zero, then return to origin
#      (M495 path observed on stock 1.0.2; not yet re-verified on 2.1.0c).
#      Negative offsets are allowed: `-5 5` probes 5 mm in −X / +5 mm in +Y, to
#      stay on solid stock and clear of a +X face being machined.
#   5. play /sd/gcodes/<basename> — start the job against the live work zero.
#
# ── DEVICE ──────────────────────────────────────────────────────────────────
#   By default the Carvera's IP is AUTO-DETECTED by scanning the 192.168.0.*
#   subnet for the control port (TCP 2222) — the reliable method on WSL, where
#   the UDP-3333 discovery broadcast doesn't cross the NAT (docs/carvera-cli.md).
#   Pass --ip ADDR to skip the scan and target a specific machine. Override the
#   scanned subnet with $CARVERA_SUBNET (default "192.168.0"). The scan needs
#   the venv's Python (it's there — carvera-cli depends on it).
#
# ── INTERACTIVITY / SAFETY (read before wiring to PR comments) ──────────────
#   • M495 PROMPTS for the probe tool and WAITS for someone to load it (we use
#     the laser as the probe). A PR-comment dispatch with nobody at the machine
#     will hang on that prompt.
#   • The Carvera accepts ONE connection — the Makera Controller must be
#     disconnected, or every command times out ("Connection reset by peer").
#   • Always supervise a cut with a hand on the e-stop.
#   • WHY THIS SCRIPT EXISTS (tall stock): the Makera Controller's run button
#     drives the bit to the PATH ORIGIN before a job; on a tall workpiece the
#     spindle can't clear the top, so that init move crashes. This script
#     `play`s the file directly — no path-origin init — so it's the safe launcher
#     for tall-stock jobs. The cut file's own G54 / T1 M6 preamble is fine. See
#     docs/carvera-cli.md.
#
# ── PORTABILITY ─────────────────────────────────────────────────────────────
#   Targets macOS /bin/bash 3.2 and Windows Git Bash: no
#   bash 4+ features, no GNU-only coreutils flags. carvera-cli and the Python
#   used for the IP scan are located in the repo venv across Linux/macOS (bin/)
#   and Windows (Scripts/), or taken from $CARVERA_CLI / $CARVERA_PY.
# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

usage() {
  printf 'usage: %s <repo-relative-gcode> [--unlock] [--home] [--auto-z-probe DX DY] [--ip ADDR]\n' "$0" >&2
}

DO_UNLOCK=0
DO_HOME=0
DO_ZPROBE=0
ZX=""
ZY=""
GCODE=""
IP_ARG=""
SUBNET="${CARVERA_SUBNET:-192.168.0}"

while [ $# -gt 0 ]; do
  case "$1" in
    --unlock) DO_UNLOCK=1; shift ;;
    --home)   DO_HOME=1; shift ;;
    --auto-z-probe)
      ZX="${2:-}"; ZY="${3:-}"
      if [ -z "$ZX" ] || [ -z "$ZY" ]; then
        printf 'error: --auto-z-probe needs two numbers: DX DY\n' >&2; usage; exit 1
      fi
      DO_ZPROBE=1; shift 3 ;;
    --ip)   IP_ARG="${2:-}"; shift 2 ;;
    --ip=*) IP_ARG="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) printf 'unknown option: %s\n' "$1" >&2; usage; exit 1 ;;
    *)
      if [ -z "$GCODE" ]; then GCODE="$1"; shift
      else printf 'unexpected extra argument: %s\n' "$1" >&2; usage; exit 1; fi ;;
  esac
done

if [ -z "$GCODE" ]; then usage; exit 1; fi

# Reject absolute paths and parent-traversal — only allow files inside the repo.
case "$GCODE" in
  /*|*..*) printf 'gcode path must be repo-relative and must not contain "..": %s\n' "$GCODE" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ ! -f "$GCODE" ]; then
  printf 'gcode not found (run from the repo root): %s\n' "$GCODE" >&2; exit 1
fi

# Locate a binary across the repo venv (Linux/macOS bin/, Windows Scripts/) and
# PATH. Usage: find_bin OUTVAR OVERRIDE name1 name2 ...
find_bin() {
  _out="$1"; _override="$2"; shift 2
  if [ -n "$_override" ]; then eval "$_out=\$_override"; return 0; fi
  for _n in "$@"; do
    for _c in "$ROOT_DIR/python/venv/bin/$_n" "$ROOT_DIR/python/venv/Scripts/$_n" "$ROOT_DIR/python/venv/Scripts/$_n.exe"; do
      if [ -x "$_c" ]; then eval "$_out=\$_c"; return 0; fi
    done
  done
  for _n in "$@"; do
    if command -v "$_n" >/dev/null 2>&1; then eval "$_out=\$_n"; return 0; fi
  done
  return 1
}

CLI_BIN=""
find_bin CLI_BIN "${CARVERA_CLI:-}" carvera-cli \
  || { printf 'carvera-cli not found — install per docs/carvera-cli.md or set CARVERA_CLI\n' >&2; exit 1; }

# Resolve the device: --ip wins; else auto-detect on $SUBNET via TCP 2222.
DEVICE="$IP_ARG"
if [ -z "$DEVICE" ]; then
  PY_BIN=""
  find_bin PY_BIN "${CARVERA_PY:-}" python python3 \
    || { printf 'no Python for IP auto-detect — pass --ip ADDR, or set CARVERA_PY\n' >&2; exit 1; }
  printf '==> Detecting Carvera on %s.* (TCP 2222, may take ~20s)...\n' "$SUBNET" >&2
  DEVICE="$(SUBNET="$SUBNET" "$PY_BIN" - <<'PY'
import os, socket, concurrent.futures, sys, time
subnet = os.environ["SUBNET"]
# Generous timeout + repeated sweeps: a single 0.6s pass missed the machine when
# it was briefly busy (e.g. just after an alarm). Better to wait than to fail.
timeout = float(os.environ.get("CARVERA_SCAN_TIMEOUT", "1.5"))
sweeps  = int(os.environ.get("CARVERA_SCAN_SWEEPS", "3"))
def probe(n):
    ip = "%s.%d" % (subnet, n)
    s = socket.socket(); s.settimeout(timeout)
    try:
        return ip if s.connect_ex((ip, 2222)) == 0 else None
    finally:
        s.close()
hits = []
for attempt in range(sweeps):
    hits = sorted({r for r in concurrent.futures.ThreadPoolExecutor(64).map(probe, range(1, 256)) if r})
    if hits:
        break
    if attempt < sweeps - 1:
        time.sleep(1.0)
if not hits:
    sys.exit(3)
if len(hits) > 1:
    sys.stderr.write("WARNING: multiple hosts on TCP 2222: %s - using %s\n" % (", ".join(hits), hits[0]))
print(hits[0])
PY
)" || { printf 'ERROR: no Carvera found on %s.* (TCP 2222) after retries. Pass --ip ADDR.\n' "$SUBNET" >&2; exit 1; }
  printf '==> Found Carvera at %s\n' "$DEVICE" >&2
fi

# carvera-cli global flags go BEFORE the subcommand.
cli() { "$CLI_BIN" --wifi --device "$DEVICE" "$@"; }

REMOTE="/sd/gcodes/$(basename "$GCODE")"

# 1. Unlock (before anything else).
if [ "$DO_UNLOCK" = 1 ]; then
  printf '==> Unlock (M999)\n' >&2
  cli command "M999"
fi

# 2. Upload + verify — never play a stale file.
printf '==> Upload %s -> %s\n' "$GCODE" "$REMOTE" >&2
UP_OUT="$(cli upload "$GCODE" --overwrite 2>&1 || true)"
printf '%s\n' "$UP_OUT" >&2
# Verify the UPLOAD specifically succeeded. NB: carvera-cli logs "WiFi connection
# established successfully" on every connect, so a bare "*successful*" match is a
# false positive that lets a FAILED upload through (and then plays a stale file).
# Reject the explicit failure line, and require the upload-layer success marker.
case "$UP_OUT" in
  *"upload failed"*|*"Upload failed"*|*"File upload failed"*)
    printf 'ERROR: upload failed — refusing to run a possibly-stale file.\n' >&2; exit 1 ;;
esac
case "$UP_OUT" in
  *"Upload process successful"*) : ;;
  *) printf 'ERROR: upload not confirmed (no "Upload process successful") — refusing to run a possibly-stale file.\n' >&2; exit 1 ;;
esac

# 3. Home before running.
if [ "$DO_HOME" = 1 ]; then
  printf '==> Home (G28)\n' >&2
  cli command "G28"
fi

# 4. Auto-Z-probe the stock top, DX/DY from the path-start corner.
if [ "$DO_ZPROBE" = 1 ]; then
  # Path-start corner = the X/Y bounding-box minimum over the job's motion
  # lines (G0–G3), matching how the Makera Controller derives M495's X/Y.
  # awk (not python3 — not on PATH on the Windows runner) over space-separated
  # words, as emitted by the makera FreeCAD post.
  BBOX="$(awk '
    /^[[:space:]]*[gG][0-3]([^0-9]|$)/ {
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^[Xx]-?[0-9.]+$/) { v = substr($i, 2) + 0; if (nx == 0 || v < xmin) { xmin = v; nx = 1 } }
        if ($i ~ /^[Yy]-?[0-9.]+$/) { v = substr($i, 2) + 0; if (ny == 0 || v < ymin) { ymin = v; ny = 1 } }
      }
    }
    END { if (nx && ny) printf "%.4f %.4f", xmin, ymin }
  ' "$GCODE")"
  if [ -z "$BBOX" ]; then
    printf 'ERROR: could not parse an X/Y bounding box from %s for --auto-z-probe\n' "$GCODE" >&2; exit 1
  fi
  XMIN="${BBOX%% *}"; YMIN="${BBOX##* }"
  printf '==> Auto-Z-probe: M495 X%s Y%s O%s F%s P1  (probe %s,%s from path start)\n' \
    "$XMIN" "$YMIN" "$ZX" "$ZY" "$ZX" "$ZY" >&2
  cli command "M495 X$XMIN Y$YMIN O$ZX F$ZY P1"
fi

# 5. Run.
printf '==> Play %s\n' "$REMOTE" >&2
cli command "play $REMOTE"
printf 'Started %s on %s\n' "$REMOTE" "$DEVICE"
