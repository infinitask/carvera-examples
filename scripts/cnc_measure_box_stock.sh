#!/usr/bin/env bash
# Fully probe a rectangular box/boss workpiece on the Carvera Air in ONE
# command: fit the 3D probe, set the WCS origin on the X-/Y- top corner,
# survey the top tilt and all four side faces, and fit the geometry.
#
#   scripts/cnc_measure_box_stock.sh --ip 192.168.1.50 --x 100.8 --y 35.2 \
#       --origin -289.2 -201.7 -96.8 --out cam/relief_engrave/jobs/<job>/survey.txt
#
# What it does, in order (docs/carvera-probe-uneven-stock.md):
#   0. `?` must say Idle - an Alarm/HALT is the operator's to clear, the
#      script never sends M999/G28 (see the CNC rules in CLAUDE.md).
#   1. `M6 T999990` -> the machine parks for the swap. Fit the probe, PLUG
#      IT IN (LED lit), confirm on the machine; the firmware auto-measures
#      TLO. The script polls `?` until that's done (T:999990, fresh TLO,
#      Idle). --no-tool-change skips this if the probe is already fitted
#      and measured.
#   2. From --origin (the block's X-/Y- top corner as a G54 triple, i.e.
#      exactly what `$#` printed last time / what M464 wrote - only needs
#      to be right to a few mm): safe height, one sanity Z touch over the
#      middle, then find the real X- and Y- faces with long M466 touches
#      from 8 mm outside, hover ~2.7 mm inside the true corner and run
#      `M464 X10 Y10 H15 E5 S2` -> G54 = the corner, in the WCS.
#   3. 5 x M466 Z over the top (4 inset corners + centre), then FIT THE
#      TILT from those five touches, so the side-touch depths are taken
#      from the LOCAL top on a tilted block.
#   4. 2 stations x 2 depths on each side face (16 touches), approaching
#      from --off outside; every raw [PRB:] logged to --out.
#   5. cam/relief_engrave/probe_survey_fit.py -> geometry.json next to
#      the survey (tilt plane, top-edge lines, true corners, extents).
#
# Every reply is checked; the first error/Alarm stops the script with the
# state printed - it never unlocks, homes or "recovers".
#
# Options:
#   --x/--y          nominal block size (mm)                       [required]
#   --origin X Y Z   approximate G54 of the X-/Y- top corner        [required
#                    unless --no-origin: keep the current G54 as-is]
#   --out FILE       survey log; geometry.json is written beside it [required]
#   --depth D        side-touch depth below the LOCAL top (default 2.5;
#                    set it deeper than any edge chamfer / rounded edge)
#   --stations a,b   along-X stations for the Y faces (default 8,x-8)
#   --ystations a,b  along-Y stations for the X faces (default 8,y-8)
#   --off D          approach distance outside each face (default 6)
#   --inset D        Z-survey inset from the edges (default 5)
#   --tip-r R        probe tip radius for the fit (default 0.975)
#   --no-tool-change probe already fitted + measured
#   --no-origin      G54 already set on the corner; skip step 2
#   --origin-only    stop after step 2 (re-zero on power-up: top + X- + Y-
#                    touches -> M464 corner origin; no survey, no --out)
set -euo pipefail
cd "$(dirname "$0")/.."

IP=""; X=""; Y=""; OUT=""; ORIGIN=(); DEPTH=2.5; STA=""; YSTA=""; OFF=6; INSET=5
TIPR=0.975; DO_TOOL=1; DO_ORIGIN=1; ORIGIN_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ip) IP=$2; shift 2;; --x) X=$2; shift 2;; --y) Y=$2; shift 2;;
    --out) OUT=$2; shift 2;; --origin) ORIGIN=("$2" "$3" "$4"); shift 4;;
    --depth) DEPTH=$2; shift 2;; --stations) STA=$2; shift 2;; --ystations) YSTA=$2; shift 2;;
    --off) OFF=$2; shift 2;; --inset) INSET=$2; shift 2;; --tip-r) TIPR=$2; shift 2;;
    --no-tool-change) DO_TOOL=0; shift;; --no-origin) DO_ORIGIN=0; shift;; --origin-only) ORIGIN_ONLY=1; shift;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[ "$ORIGIN_ONLY" = 1 ] && OUT=${OUT:-/dev/null}
[ -n "$IP" ] && [ -n "$X" ] && [ -n "$Y" ] && [ -n "$OUT" ] || { echo "need --ip --x --y --out" >&2; exit 2; }
if [ "$DO_ORIGIN" = 1 ] && [ ${#ORIGIN[@]} -ne 3 ]; then echo "need --origin X Y Z (or --no-origin)" >&2; exit 2; fi
CLI="python/venv/bin/carvera-cli --wifi --device $IP --timeout 120 command"
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
[ -n "$STA" ]  || STA="8,$($PY -c "print($X-8)")"
[ -n "$YSTA" ] || YSTA="8,$($PY -c "print($Y-8)")"
D1=$DEPTH
IFS=, read -r S1 S2 <<<"$STA"
IFS=, read -r T1 T2 <<<"$YSTA"
HOVER=10
PROBE_TOOL=999990

status() { $CLI "?" 2>&1 | grep -o '<[^>]*>' | tail -1; }
send() {  # send one command; abort on any error/alarm reply
  # never let a malformed word out: "G0 Z" (empty value) is read by the
  # firmware as Z0 and drives the probe to the top height (2026-08-17)
  if echo " $1" | grep -q -E "[ ][XYZFHES]([ ]|$)"; then
    echo "!! refusing to send malformed command: '$1'" >&2; exit 1
  fi
  local r; r=$($CLI "$1" 2>&1) || true
  if echo "$r" | grep -q -E "error|Alarm|ALARM|HALT"; then
    echo "!! machine reported an error on: $1" >&2; echo "$r" | tail -3 >&2
    echo "!! stopping - state: $(status)" >&2
    echo "!! not recovering: clearing an Alarm/HALT is the operator's call" >&2; exit 1
  fi
  echo "$r"
}
prb()  { $CLI '$#' 2>&1 | grep -o '\[PRB:[^]]*\]'; }
g54()  { $CLI '$#' 2>&1 | grep -o '\[G54:[^]]*\]' | tr -d '[]' | cut -d: -f2 | tr ',' ' ' | cut -d' ' -f1-3; }
tl0()  { $CLI '$#' 2>&1 | grep -o '\[TL0:[^]]*\]' | tr -d '[]' | cut -d: -f2; }
touch_() {  # label, moves..., probe-cmd  -> appends "label probe [PRB:...]"
  local label=$1; shift; local probe=${*: -1}; local n=$(($#-1))
  for c in "${@:1:$n}"; do send "$c" >/dev/null; done
  local r; r=$(send "$probe")
  local p; p=$(prb)
  echo "$label $probe $p" | tee -a "$OUT"
  # the compensated position the cycle prints is handy for the human log
  echo "$r" | grep -E "Final Position" | tail -1 | sed 's/^/    /' >&2 || true
}

# ---------------------------------------------------------------- 0. state
st=$(status)
case "$st" in
  "<Idle"*) ;;
  *) echo "!! machine is not Idle: $st" >&2
     echo "!! (Alarm/HALT? that's yours to clear - unlock + home from where you can see it, then re-run)" >&2; exit 1;;
esac

# ---------------------------------------------------------------- 1. probe tool
if [ "$DO_TOOL" = 1 ]; then
  before=$(tl0)
  echo ">> M6 T$PROBE_TOOL - fit the 3D probe, PLUG IT IN (LED lit), confirm on the machine ..."
  send "M6 T$PROBE_TOOL" >/dev/null
  # wait for the swap + the firmware's automatic TLO measure
  for i in $(seq 1 600); do
    sleep 2
    st=$(status)
    case "$st" in
      *Alarm*|*HALT*) echo "!! $st" >&2; echo "!! probe-tool measure alarmed (probe unplugged?) - clear it yourself, then re-run" >&2; exit 1;;
    esac
    tool=$(echo "$st" | grep -o 'T:[^|]*' | cut -d: -f2 | cut -d, -f1)
    now=$(tl0)
    if [[ "$st" == "<Idle"* ]] && [ "$tool" = "$PROBE_TOOL" ] && [ "$now" != "$before" ]; then
      echo ">> probe fitted, TL0=$now"; break
    fi
    [ $i -eq 600 ] && { echo "!! timed out waiting for the tool change" >&2; exit 1; }
  done
fi
TL0=$(tl0)
echo ">> TL0=$TL0 (probe should read ~34.3)"

# ---------------------------------------------------------------- 2. origin
if [ "$DO_ORIGIN" = 1 ]; then
  OX=${ORIGIN[0]}; OY=${ORIGIN[1]}; OZ=${ORIGIN[2]}
  # temporarily park G54 on the approximate corner so we can work in "work"
  # coords - M464 S2 overwrites it with the probed corner right after
  send "G10 L2 P1 X$OX Y$OY Z$OZ" >/dev/null
  send "G90" >/dev/null; send "G54" >/dev/null
  send "G53 G0 Z-3" >/dev/null
  XC=$($PY -c "print($X/2)"); YC=$($PY -c "print($Y/2)")
  send "G0 X$XC Y$YC" >/dev/null
  send "G0 Z12" >/dev/null
  echo ">> sanity Z touch over the centre"
  r=$(send "M466 Z-15"); ztop=$(echo "$r" | grep "Final Positon Z\|Final Position Z" | tail -1 | grep -o -- '-\?[0-9.]*$')
  echo "   top at work Z $ztop (approx origin was 0)"
  # find the real X- and Y- faces from 8 mm outside, ~3.5 mm below the top
  zf=$($PY -c "print(round(($ztop) - 3.5, 3))")
  send "G0 Z12" >/dev/null; send "G0 X-8 Y$YC" >/dev/null; send "G0 Z$zf" >/dev/null
  r=$(send "M466 X12"); xf=$(echo "$r" | grep "Final Position X" | tail -1 | grep -o -- '-\?[0-9.]*$')
  send "G0 Z12" >/dev/null; send "G0 X$XC Y-8" >/dev/null; send "G0 Z$zf" >/dev/null
  r=$(send "M466 Y12"); yf=$(echo "$r" | grep "Final Position Y" | tail -1 | grep -o -- '-\?[0-9.]*$')
  echo "   X- face at work X $xf, Y- face at work Y $yf (tip-compensated)"
  hx=$($PY -c "print(round($xf + 2.7, 3))"); hy=$($PY -c "print(round($yf + 2.6, 3))")
  hz=$($PY -c "print(round($ztop + 10, 3))")
  send "G0 Z12" >/dev/null; send "G0 X$hx Y$hy" >/dev/null; send "G0 Z$hz" >/dev/null
  echo ">> M464 X10 Y10 H15 E5 S2 (corner origin -> G54)"
  send "M464 X10 Y10 H15 E5 S2" >/dev/null
fi
read -r GX GY GZ <<<"$(g54)"
echo ">> G54 = $GX $GY $GZ   TL0 = $TL0"
if [ "$ORIGIN_ONLY" = 1 ]; then echo ">> --origin-only: G54 set, probe left at hover; done"; exit 0; fi

# ---------------------------------------------------------------- 3. Z survey
: > "$OUT"
{
  echo "# box-stock survey $(date -Iseconds) block ${X}x${Y} depth $DEPTH off $OFF"
  echo "# G54 $GX $GY $GZ TL0 $TL0 tip_r $TIPR"
} >> "$OUT"
send "G90" >/dev/null; send "G54" >/dev/null; send "G0 Z$HOVER" >/dev/null
XI=$INSET; XO=$($PY -c "print($X-$INSET)"); YI=$INSET; YO=$($PY -c "print($Y-$INSET)")
XC=$($PY -c "print($X/2)"); YC=$($PY -c "print($Y/2)")
for p in "$XI $YI" "$XO $YI" "$XO $YO" "$XI $YO" "$XC $YC"; do set -- $p
  touch_ "Z x=$1 y=$2" "G0 Z$HOVER" "G0 X$1 Y$2" "M466 Z-12"
done
# fit the tilt from the five Z touches (work Z = PRB_Z - (G54_Z + TL0))
read -r PA PB PC <<<"$($PY - "$OUT" "$GZ" "$TL0" <<'EOF'
import re, sys, numpy as np
rows = []
for ln in open(sys.argv[1]):
    if not ln.startswith("Z "):
        continue
    x = float(re.search(r"x=(-?[\d.]+)", ln).group(1)); y = float(re.search(r"y=(-?[\d.]+)", ln).group(1))
    z = float(re.search(r"PRB:-?[\d.]+,-?[\d.]+,(-?[\d.]+)", ln).group(1)) - (float(sys.argv[2]) + float(sys.argv[3]))
    rows.append((x, y, z))
A = np.array([[1, x, y] for x, y, _ in rows]); b = np.array([z for *_, z in rows])
(a, bx, cy), *_ = np.linalg.lstsq(A, b, rcond=None)
print(f"{a:.6f} {bx:.6f} {cy:.6f}")
EOF
)"
echo ">> top plane z = $PA + $PB*x + $PC*y  (from the 5 Z touches)"
ztop() { $PY -c "print(round($PA + $PB*($1) + $PC*($2), 3))"; }
zd()   { $PY -c "print(round($(ztop "$1" "$2") - $3, 3))"; }

# ---------------------------------------------------------------- 4. side faces
YN=$($PY -c "print(-$OFF)"); YP=$($PY -c "print($Y+$OFF)")
XN=$($PY -c "print(-$OFF)"); XP=$($PY -c "print($X+$OFF)")
for s in $S1 $S2; do
  touch_ "Y- x=$s d$D1" "G0 Z$HOVER" "G0 X$s Y$YN" "G0 Z$(zd $s 0 $D1)" "M466 Y8"
  send "G0 Y$YN" >/dev/null
done
for s in $S2 $S1; do
  touch_ "Y+ x=$s d$D1" "G0 Z$HOVER" "G0 X$s Y$YP" "G0 Z$(zd $s $Y $D1)" "M466 Y-8"
  send "G0 Y$YP" >/dev/null
done
for s in $T1 $T2; do
  touch_ "X- y=$s d$D1" "G0 Z$HOVER" "G0 X$XN Y$s" "G0 Z$(zd 0 $s $D1)" "M466 X8"
  send "G0 X$XN" >/dev/null
done
for s in $T2 $T1; do
  touch_ "X+ y=$s d$D1" "G0 Z$HOVER" "G0 X$XP Y$s" "G0 Z$(zd $X $s $D1)" "M466 X-8"
  send "G0 X$XP" >/dev/null
done
send "G0 Z$HOVER" >/dev/null
echo ">> survey complete: $(grep -c PRB "$OUT") touches -> $OUT"

# ---------------------------------------------------------------- 5. fit
GEOM="$(dirname "$OUT")/geometry.json"
$PY cam/relief_engrave/probe_survey_fit.py "$OUT" --g54 "$GX" "$GY" "$GZ" --tl0 "$TL0" --tip-r "$TIPR" --out "$GEOM" \
  | $PY -c "import json,sys; d=json.load(sys.stdin); e=d['extents']; p=d['plane']; print(f\">> block {e['x_len_bottom']:.2f} x {e['y_len_left']:.2f} mm, rotation {e['rotation_deg']:.3f} deg, plane resid {p['max_residual_mm']*1000:.0f} um\"); print('>> corners', [[round(v,3) for v in c] for c in d['corners_ccw_from_xminus_yminus']])"
echo ">> geometry -> $GEOM   (probe still fitted, parked at Z$HOVER)"
