# Carvera Air — sending G-code with `carvera-cli`, machine operation & M-codes

[`carvera-cli`](https://github.com/hagmonk/carvera-cli) is a command-line tool
for talking to the Carvera / Carvera Air CNC over WiFi or USB — upload G-code,
list/download files, run device commands, read the config. It is vendored as a
submodule at [`vendor/carvera-cli`](../vendor/carvera-cli) so you (or an AI
agent such as Claude Code) can push the G-code a CAM pipeline emits (e.g.
[`cam/brass_holes/`](../cam/brass_holes/)) straight to the machine.

> **Local only.** This tooling is for the **PC that's on the same network as
> the Carvera** (the "lab PC" below). One of its deps needs a C compiler, so
> install it by hand per *One-time setup*.

## One-time setup (the bits that bite)

Everything runs out of a repo-local venv, `python/venv`.

```bash
# 1. Pull the submodule if it isn't checked out yet
git submodule update --init vendor/carvera-cli

# 2. Create the repo venv (if it doesn't exist)
python3 -m venv python/venv

# 3. System build deps for pyquicklz (see gotcha below) - needs sudo
sudo apt-get install -y build-essential python3-dev

# 4. Install carvera-cli (editable) + its deps into the venv
python/venv/bin/python -m pip install -e vendor/carvera-cli

# 5. Sanity check
python/venv/bin/carvera-cli --help
```

### Gotcha 1 — `pyquicklz` has no PyPI wheel, so it must compile

`carvera-cli` depends on `pyquicklz` (QuickLZ bindings, used for the **default
compressed upload**). PyPI ships it as an **sdist only — no prebuilt wheel for
any platform/Python** (`pip install --only-binary :all: pyquicklz` → "No
matching distribution"). So pip Cythonises and compiles a C extension at install
time, which needs:

- **`gcc`** → `build-essential`
- **`Python.h`** → `python3-dev`

Without them you get `error: command 'x86_64-linux-gnu-gcc' failed: No such file
or directory` (or a missing `Python.h`). Step 3 above installs both. The other
three deps (`pyserial`, `platformdirs`, `pyreadline3`) are pure Python and need
nothing extra.

### Gotcha 2 — WSL2 can't *discover* the machine (NAT), but can *reach* it

On WSL2 the Linux VM is behind a NAT, on a different subnet from the LAN. The
Carvera announces itself by **UDP broadcast on port 3333**, and that broadcast
**does not cross the NAT** — so `carvera-cli scan` finds nothing even when the
Windows host is on the same network as the machine:

```
WiFiStream.Discovery - INFO - Discovery finished. No devices found.
```

Outbound **unicast** is NAT'd through fine, though, so you *can* still talk to
the machine — just **connect by IP with `--device` instead of relying on
discovery** (see *Connecting*). (If you genuinely need discovery from WSL,
switching WSL to mirrored networking — `networkingMode=mirrored` in
`.wslconfig` — lets broadcasts through, but connecting by IP is simpler.)

> Minor upstream bug: when `scan` finds no WiFi devices it crashes with
> `AttributeError: type object 'WiFiStream' has no attribute 'UDP_PORT'` while
> printing the "none found" message. Harmless (discovery already finished), but
> it means `scan` isn't a reliable "is it there?" check on WSL — use a direct
> `--device ... command version` instead.

## Finding the machine on your network

The Carvera uses two ports:

| Port | Proto | Purpose |
| --- | --- | --- |
| **2222** | TCP | Control / file transfer (the connection `carvera-cli` uses) |
| **3333** | UDP | Discovery broadcast the machine emits for auto-detect |

Easiest is to read the IP straight from the **Makera Controller** connection
screen or your router's DHCP client list. If you can't (or you're on WSL, where
the UDP-3333 broadcast never arrives — see Gotcha 2), **scan the subnet for the
control port (TCP 2222)** instead. From the lab PC:

```bash
# Scan 192.168.0.1-255 for an open TCP 2222 (the Carvera). ~2s.
python/venv/bin/python - <<'PY'
import socket, concurrent.futures
def probe(n):
    ip = f"192.168.0.{n}"                       # <-- adjust subnet if needed
    s = socket.socket(); s.settimeout(0.6)
    try:
        if s.connect_ex((ip, 2222)) == 0: return ip
    finally:
        s.close()
for r in concurrent.futures.ThreadPoolExecutor(64).map(probe, range(1, 256)):
    if r: print("Carvera at", r)
PY
```

> **The examples below use `192.168.1.50` — substitute your machine's IP.** It's usually a DHCP lease, so it can change —
> re-scan (or reserve it in the router) if a connection by IP suddenly fails.
> WSL reaches it fine by unicast even though `eth0` is on a different (172.x)
> NAT subnet; only *broadcast discovery* is blocked.

## Connecting

> **Only one controller at a time.** The Carvera accepts a **single**
> connection. If the **Makera Controller** (or another `carvera-cli` session) is
> connected, the machine **resets** `carvera-cli`'s connection immediately —
> you'll see `WiFi connection established` followed by
> `Error during socket recv: [Errno 104] Connection reset by peer` and a command
> timeout. **Disconnect / close the Makera Controller first.** (A leftover
> half-open session clears with a Carvera power-cycle.)

Pass the IP with `--device`; global flags go **before** the subcommand.

```bash
CARVERA=192.168.1.50   # <-- your machine's IP (see "Finding the machine")

# Confirm it's reachable (prints firmware version, e.g. "version = 1.0.2")
python/venv/bin/carvera-cli --wifi --device "$CARVERA" command version
```

## Uploading G-code

Default remote directory is `/sd/gcodes`.

```bash
# Upload a file (remote name = local name) into /sd/gcodes
python/venv/bin/carvera-cli --wifi --device "$CARVERA" \
  upload cam/brass_holes/brass_holes.gcode

# Overwrite if it already exists
python/venv/bin/carvera-cli --wifi --device "$CARVERA" \
  upload path/to/file.gcode --overwrite

# Upload to a specific remote path/name
python/venv/bin/carvera-cli --wifi --device "$CARVERA" \
  upload path/to/local.gcode --remote-path /sd/gcodes/renamed.gcode
```

Uploads are QuickLZ-compressed by default (hence the `pyquicklz` requirement).
After uploading you select and run the file from the machine's own
screen / the Makera Controller — `carvera-cli` is for **transfer**, not for
starting a cut.

## Other handy commands

```bash
C="--wifi --device $CARVERA"
python/venv/bin/carvera-cli $C ls /sd/gcodes        # list files
python/venv/bin/carvera-cli $C stat /sd/gcodes/x.gcode
python/venv/bin/carvera-cli $C download /sd/config.txt
python/venv/bin/carvera-cli $C config               # dump device config
python/venv/bin/carvera-cli $C command "M115"       # arbitrary command
python/venv/bin/carvera-cli $C interactive          # interactive shell
```

USB is also supported (`--usb --device /dev/ttyACM0`), but **USB is not passed
through to WSL by default**, so on the lab PC WiFi-by-IP is the path of least
resistance.

## Running a job (and recovering from a stall)

You can start a job that's already on the SD card with the firmware's `play`
command (there's no dedicated subcommand — send it via `command`):

```bash
python/venv/bin/carvera-cli --wifi --device "$CARVERA" \
  command "play /sd/gcodes/myjob.gcode"
# -> "Playing /sd/gcodes/myjob.gcode" / "File size NNNN"
```

What we learned doing this:

- **`play` uses the *current* work zero and does NO scan-margin / auto-Z-probe.**
  Those are **Makera Controller** features layered on top; raw `play` just
  streams the file against whatever WCS/zero is already set. So it's the
  equivalent of running in the app with "scan margin" and "auto z probe"
  switched off — handy when you've already probed on a previous run and don't
  want it switching heads (e.g. to the laser) to re-probe.
- **`M0` does NOT pause a play-from-SD job.** An `M0` we put in the file as a
  "pause and let me check" gate was ignored — the job ran straight through it.
  Don't rely on `M0` as a safety stop when launching with `play`; watch it and
  use the e-stop.
- **Disconnecting `carvera-cli` after `play` does not abort the job** — it runs
  from the SD card independently of the connection.

### Recovering from a halt / stall (unlock + home)

If the machine halts (e.g. the spindle stalls on too heavy a cut), it goes into
**Alarm** and won't move. Check state and recover:

```bash
C="--wifi --device $CARVERA"
python/venv/bin/carvera-cli $C command "?"        # status: look for <Alarm|...>
python/venv/bin/carvera-cli $C command "M999"     # UNLOCK (clears the halt)
python/venv/bin/carvera-cli $C command "G28"      # then HOME (see below)
```

- **`?`** prints a one-line status: `<Alarm|MPos:...|WPos:...|...>` when halted
  (the `WPos` shows where it stalled — useful confirmation of the toolpath).
- **`M999` unlocks** (clears the halt). The firmware replies `ok` plus
  *"After HALT you should HOME as position is currently unknown."*
- **⚠️ Release the physical e-stop button BEFORE unlocking.** The e-stop
  **latches down**, and `M999` can't clear the halt while it's still depressed —
  the machine **re-locks immediately, with a beep** as the lock re-applies.
  Twist + pull the button **up** first, then `M999`. (Easy to forget you left it
  down after stopping.) So if an `M999` re-locks with that beep, the button's
  still down — **wait ~10 s for the operator to lift it, then retry the unlock.**
- **Spindle-overload halt (`H:41`) — try `M999` first; it often clears it.**
  When the spindle stalls on too heavy a cut the machine halts with `H:41`.
  **`M999` will *usually* unlock it** (confirmed on a deep-channel job in
  tall hardwood, 2026-07-16 — `M999` cleared `H:41` to `<Idle>` with position retained, and we
  hand-evacuated the bit normally). **Sometimes it doesn't**, and then only a
  **hard reset / power-cycle** clears the spindle alarm. That's the bad path on
  tall stock: ⚠️ **on reconnect after a power-cycle the Carvera homes / drives to
  clearance as its first move**, so if the bit is **still down in the work that
  move drags it through the part.** So: **always try `M999` first** — if it
  unlocks (the common case), you can evacuate the bit cleanly by hand and a
  power-cycle is unnecessary. Only if `M999` fails do you power-cycle, and then
  **get the bit clear / move the part out from under it first** and keep a hand
  on the e-stop as it reconnects.
- **Home with `G28`, NOT `$H`.** On the Carvera, `$H` is rejected
  (`error:Invalid statement`); **`G28` = "goto clearance position"** and is the
  homing/park move. It **raises Z first**, then traverses XY to the clearance
  corner. Afterward `?` shows `<Idle|...>`.
  - **⚠️ On a SHALLOW job** (the spindle can lift the bit clear of the stock
    top) G28's Z-first lift makes it safe even with the tool buried.
  - **⚠️ On a TALL job / deep pocket** (a Z-reach deficit — the bit can't lift
    above the stock top, e.g. a deep channel in a ~70 mm hardwood brick) G28 is **NOT safe**: it
    only raises Z to the reachable ceiling, which is **still down inside the
    work**, then traverses XY — dragging through walls / across the stock. There,
    **evacuate the bit by hand first** (next subsection), *then* G28 from free
    air off an open end.
  - **⚠️ Even from free air off an open end, supervise it.** The XY route G28
    takes to the clearance corner is machine-chosen, not guaranteed to head
    away from the stock: on a ~70 mm hardwood brick's successful full-channel
    run (2026-07-26), the **end-of-job G28** — fired from the job's +X free-air
    exit (work `X160`), the same spot earlier hand-sent G28s had parked cleanly
    from — set off on a straight line that **would have crossed the part**, and
    the operator had to e-stop it. With tall stock on the bed, keep a hand on
    the e-stop for **every** G28, including the one at the end of a played
    file.
- **Quoting:** `$H` (and any `$...`) must be **single-quoted** in bash
  (`command '$H'`) or the shell eats the `$H` and you send an empty command.

### Evacuating a stuck bit from a pocket (move-check-move)

When a job halts with the bit **down inside a deep pocket/channel on tall stock**
(it can't lift clear), don't reach for `G28` — its post-lift traverse drags or
crashes (above). Jog the bit out by hand, **one move at a time, reading the
position between every move**. (This worked cleanly on a deep-channel job in
tall hardwood on 2026-06-06.)

Key fact: an **e-stop / HALT retains position** — `?` shows the real `WPos`
(the bit's location in the *same* work coords as your G-code), so after `M999`
the machine accepts **absolute `G90` moves**. (A *power cycle*, by contrast,
loses position: `MPos` reads the `-1,-1,-1` placeholder and you must home first.)

```bash
C="--wifi --device $CARVERA"
# 1. READ where the bit is. WPos = work coords (what the toolpath uses).
python/venv/bin/carvera-cli $C command "?"            # <Alarm|...|WPos:146.3,51.9,-8.0|...>
# 2. UNLOCK. Position is retained, so absolute moves will work.
python/venv/bin/carvera-cli $C command "M999"
python/venv/bin/carvera-cli $C command "G90"          # absolute
# 3. RETRACT Z a little — only within reach (you can't lift above the ceiling).
#    Lift 1-2 mm off the cut floor so the lateral move doesn't drag.
python/venv/bin/carvera-cli $C command "G1 Z-6.5 F200"
python/venv/bin/carvera-cli $C command "?"            # CHECK: Z lifted, <Idle>?
# 4. Move OUT through already-cleared space, one axis at a time, checking each.
#    Recentre across the channel first if hard against a wall, then exit along
#    the OPEN axis to free air past the end face.
python/venv/bin/carvera-cli $C command "G1 Y47.5 F300"   # recentre in the channel
python/venv/bin/carvera-cli $C command "?"               # CHECK <Idle>
python/venv/bin/carvera-cli $C command "G1 X160 F300"    # exit +X to free air off the open end
python/venv/bin/carvera-cli $C command "?"               # CHECK now in free air
# 5. NOW G28 is safe — the bit is off the +X end, clear of the stock.
python/venv/bin/carvera-cli $C command "G28"
```

The discipline that makes this safe: **send one move → poll `?` until `<Idle>` →
verify the coords are where you expect → only then send the next move.** Never
fire a blind multi-axis move from a buried position. Pick the retract height from
the reach math (the ceiling is the `WPos` Z at clearance; stay just below it),
and exit along the channel's **open** axis through space the cut already cleared.

### Tall stock: launch with `send_to_cnc.sh`, not the Makera Controller's run button

On a **tall workpiece** — one where the spindle can't lift above the top of the
stock in Z — **the Makera Controller's run button crashes the cut**: before it
runs a file the Controller drives the spindle to the **path origin**, and that
traverse can't clear the top of a tall brick, so it drives the bit into the
side. A raw `play` does not do this.

So for tall stock:
- **Launch with [`scripts/send_to_cnc.sh`](../scripts/send_to_cnc.sh)** — it
  `play`s the file directly against the work zero already set, with **no
  path-origin init move**. Do **not** use the Makera Controller's run button.
- The cut file runs its normal preamble (`G54`, `T1 M6`, `M3`) as usual.
  Hand-prep still applies: load the tool and set the work Z zero first (the file
  doesn't probe or home).

### Caveat: the Carvera repositions the head *after a tool change* — STOCK 1.0.2 ONLY

*(Community 2.1.0c removed this: `M6` returns to the XY where it was
requested, verified live 2026-08-15 — see §"Community firmware 2.1.0c". Kept
for rollback scenarios.)*

**A tool change (`M6`) ends by moving the head somewhere of the firmware's
choosing — not to your toolpath's first point.** This is separate from the
Makera Controller's path-origin move above; it happens on a raw `play` too,
whenever the run actually performs a tool change.

Observed directly on a tall-stock job:
- **Run *with* a tool change** (a fresh `T1` load, **or** an `M495` auto-Z-probe,
  which swaps to the laser and then changes *back* to the cutter): after the
  change the head made an unexpected diagonal traverse toward the stock — at a Z
  below the stock top, so it drove at the side of the brick.
- **Re-run with `T1` already in the spindle** (so the file's `T1 M6` is a no-op,
  no physical change): the head went **straight to the toolpath's first point**,
  no stray move.

So the post-tool-change reposition is the hazard, and it bites exactly the runs
you can't avoid a change on — the first cut after loading the tool, and **any
`--auto-z-probe` run** (probe needs the laser, then changes back to the cutter).

Be cautious accordingly:
- On **tall stock**, treat any tool-change-containing run as a crash risk on that
  first post-change move. **Hand on the e-stop** through it.
- To launch a tall-stock cut *cleanly*, get the cutter loaded and the work Z set
  **first** (probe / load in a separate step), then `play` the cut file with the
  **tool already in the spindle** so its `T1 M6` is a no-op and no reposition
  occurs before the first cut.
- A run that *must* tool-change on a tall brick wants its first toolpath move to
  be reachable from wherever the head lands — still being characterised; until
  then, supervise it.

## Where to find Carvera M-codes (the vendored posts)

The Carvera's non-obvious M-codes aren't in this repo's own scripts — the best
reference is the **vendored CAM post-processors** under
[`vendor/CarveraProfiles/CAM_Post_Processors/`](../vendor/CarveraProfiles/CAM_Post_Processors/),
each of which emits real Carvera G-code you can copy verbatim into a `command`
send. In rough order of usefulness:

- **`Fusion360-profiles/Carvera.cps`** — the richest. Tool/collet/probe codes:
  `M490.2` / `M490.1` (open / close collet), `M491` (calibrate tool length —
  the auto-Z / TLO probe), `M491.1` (probe variant), `M493.2 T<n>` (set the
  *active tool number* logically, no physical move), `T-1 M6` (drop to empty).
  Also the WCS handling.
- **`VCarveDesktop-profiles/Carvera Laser (mm) (!.cnc).pp`** — laser head:
  **`M321`** (laser mode ON / switch to the laser), **`M322`** (laser mode OFF /
  back to the router), with `M3` / `M5` firing it. Its sequence raises to
  Z-home *before* firing — never traverses at cutting height.
- **`Freecad/1.1PostProcessor/makera_post.py`** — the FreeCAD post we actually
  drive (the `cam/*.py` pipelines call it). Its **default postamble homes with
  `G28`** and the default preamble is bare; this is why the pipelines override
  them (and why a pipeline that *doesn't* override leaves a `G28` in its output).
- `vectric-Profiles/*.cnc` / `*.pp` — worked examples of the
  `M493.2T1` → `M491` "set tool number then calibrate length" pairing.

Also handy: `coordinate.*` and `laser_module_offset_*` keys in the `config`
dump — e.g. `laser_module_offset_z : -7.0` is the laser head's Z offset.

### Accessory outputs — dust extractor, vacuum, air

The Carvera has **several separate switchable outputs**, each its own M-code pair
— don't confuse them (we did, at first):

| Output | ON | OFF | Notes |
| --- | --- | --- | --- |
| **External Control** (the **"ext out"** port — our dust extractor) | **`M851 S<pct>`** | **`M852`** | PWM, `S` = percent. **This is the one our extractor is wired to.** ✅ validated on the Air. |
| Built-in **vacuum** | `M801 S<pct>` | `M802` | A *different* output — **did NOT drive our extractor** on the Air. Don't reach for this when you mean the ext-out. |
| **Auto-vacuum** | `M331` | `M332` | |
| Compressed **air** | `M7` | `M9` | |

(Source: `Fusion360-profiles/Carvera.cps` — `ExtOn`/`ExtOff` = `M851 S100`/`M852`,
`VacOn`/`VacOff` = `M801`/`M802`; the Vectric post documents the same.)

- **CAM convention:** emit the extractor in the **preamble** next to `M3` and
  shut it off in the **postamble** next to `M05`, so it runs for exactly the cut
  — e.g. a slot-pocketing job adds `M851 S50` (50% — dust-only job) after
  `M3 S5400` and `M852` before `G28`. See [`docs/cam-conventions.md`](cam-conventions.md).
- **Mid-job injection works.** You can send these (and other non-motion commands)
  **while a job is running** and they take effect immediately without disturbing
  it — validated by sending `M851 S100` then `M851 S50` mid-cut (each returned
  `ok`, spindle/feed never hiccuped). Read-only `?` status also polls fine mid-job.
  (Motion commands are a different matter — don't inject those into a running job.)

## The Makera Controller's own routines (open source)

The single best reference for "what command does the Controller send when I click
X?" is the **Makera Controller source** — it's open on GitHub at
[`MakeraInc/CarveraController`](https://github.com/MakeraInc/CarveraController)
(community fork: `Carvera-Community/Carvera_Controller`). The desktop app is a
frozen Kivy/Python build, so the installed copy on the lab PC
(`C:\Program Files (x86)\CarveraController\`, i.e. WSL
`/mnt/c/Program Files (x86)/CarveraController/`) has the app code compiled into
the `.exe` and **isn't readable** — read the GitHub source instead. The useful
files:

- **`src/Controller.py`** — the command builders. This is where margin / Z-probe
  / auto-level / goto-origin all collapse into **one `M495` command** (see below),
  plus `M495.3` (XYZ probe), `M471` (pair wireless probe), and version/model/time
  queries.
- **`src/CNC.py`** — machine constants and the WCS/anchor model. Defines
  `prbcmd : "G38.2"` (the low-level probe move), the `anchor1_x/y`, `worksize_x/y`,
  and the default `zprobe` X/Y offset (`5.0, 5.0`).
- **`src/makera.py`** — the UI layer; shows how the Z-probe popup's editable
  **X/Y offset** fields flow into the `M495` `O`/`F` parameters.

> **We ran COMMUNITY firmware `2.1.0c` on the Carvera Air** (flashed
> 2026-08-15 — it doesn't void the warranty and rollback is one
> upload away, see [`carvera/README.md`](../carvera/README.md) and
> [`carvera-firmware-upgrades.md`](carvera-firmware-upgrades.md)). So:
> - The **`Carvera-Community/Carvera_Community_Firmware`** source is now
>   **authoritative** for how an M-code behaves on our machine — read the
>   release notes *and* the dispatch code (`src/modules/tools/zprobe/ZProbe.cpp`,
>   `src/modules/tools/atc/ATCHandler.cpp`) before firing something new.
> - The stock Controller source above still documents what the *stock app*
>   sends, but our scripted `carvera-cli` path doesn't depend on it.
> - ⚠️ Everything in this doc marked "verified" was verified **on stock
>   1.0.2 unless it says otherwise** — behaviour claims are being
>   re-verified on 2.1.0c; see the section below for what's
>   confirmed so far, and treat the rest as unverified-on-current-firmware.

### Community firmware 2.1.0c — what changed for us (flashed 2026-08-15)

Source: the [v2.1.0c release notes](https://github.com/Carvera-Community/Carvera_Community_Firmware/releases/tag/v2.1.0c)
+ firmware source. Verification state as of the flash day:

**Confirmed live on our machine:**

- **TLO stickout semantics + re-baselined gate values (2026-08-15):**
  `M491` on 2.1.0c writes `TL0 = probe-contact MZ − coordinate.reference_tool_mz`
  (verified: contact −88.272 − (−115.34) = **27.068** for T7). New gate
  numbers: **T1 6 mm router = 69.14** (was 26.96 stock-semantics), **T7
  chamfer bit = 27.07** (was −15.27). Verify with `$#` — see the `?` format
  gotcha below.
- **The post-`M6` dash to the work origin is GONE.** A real `M6 T1` swap
  (verified live 2026-08-15, chamfer bit → router) ends by returning to
  the **XY where the M6 was requested**, at clearance Z — ours went back
  to the clearance corner (−5, −21), nowhere near the stock. Operating
  rule: **request tool changes with the head parked somewhere safe**,
  because that's where it returns. The `M493.2`+`M491` no-motion recipe
  still works (both verified on 2.1.0c) but is no longer mandatory on
  tall stock.
- **`?` status `T:` field changed — don't gate on it mid-cycle.** Format is
  now 4 fields `T:<tool>,<TLO>,<pending>,<?>`, and during/after an ATC
  cycle without a registered tool it can show a **garbage tool number**
  (`T:-1030621184,0.000,…`) while `$#` holds the correct `TL0`. Read
  `$#` for the TL0 gate; `M493.2 T<n>` restores a sane `T:` field.
- **`G28` unchanged**: same `G28 means goto clearance position on CARVERA`
  reply, same destination (−5, −21, Z-top). Routing from beyond tall stock
  not yet re-observed — keep the end-of-job e-stop habit for now.

- `version` → `2.1.0c`; the flash **wiped all WCS offsets** (G54–G59.3 all
  zero) but preserved `TL0`, the active tool number, and `/sd/config.txt`
  byte-for-byte. **Re-probe the work origin before any cut after a flash.**
- **`G0` is a true rapid again** — moves at `default_seek_rate`
  (`config-get sd default_seek_rate` → 3000 mm/min) unless an `F` word is
  given. On stock 1.0.2, G0 ran at the prevailing feed; our CAM files'
  staged G0 entries now traverse at 3000.
- New read-only introspection works: **`M493.4`** (tool-setter position —
  ours reports the default `X-9.360 Y-5.880 Z-148.000` — plus current and
  reference tool offsets), **`M889`** (tool-slot table), `M119` (probe pin
  state — reads `Probe: 0` with nothing connected).

**From release notes / source, not yet exercised here:**

- TLO extras not yet used: `zprobe.three_axis_probe_tlo_correction`
  config, the `R` repeat parameter on `M491`/`M6` (facemills), and
  `M491.1` X/Y/Z offsets (off-centre cutting points). The tool-setter
  location is also now configurable (`coordinate.probe_mcs_x/y/z`).
- **3-axis probing cycles** (the reason we flashed — the Makera 3D Wired
  Probe): `M460` probe-tip-diameter calibration (`.2` boss, `.3` anchor),
  `M461` bore/pocket centre, `M462` boss/web centre, `M463` inside corner,
  `M464` outside corner, `M465` axis-angle (`.1`/`.2` A-axis variants),
  `M466` single-axis double-tap (`X`/`Y`/`Z`, `.1` square). Common
  parameters: `X`/`Y` probe radius/direction, `H` find-bottom first, `Q`
  rotation, `L1` repeat-from-found-centre, `D` tip diameter (from config
  `zprobe.probe_tip_diameter`, set via `M460` cal), **`S1` = save the
  found position as the WCS zero**, `F`/`K` feed overrides. A failed probe
  raises a machine halt (`PROBE_FAIL`). Reference: the firmware repo's
  `tests/TEST_ProbingM460toM465/` readme.
- Other: `M400` wait-for-moves; custom tool slots to 255 (`M890`/`M891`);
  ATC support on the Air; `config-delete sd <key>`; `switch.extendout.failsafe`
  (ext-out behaviour on halt); `spindle.max_rpm` cap; **inch mode `G20` now
  raises a halt** (known-buggy); wider override range (10–300%); a fix
  making tool calibration **re-run after an e-stop interrupt** (relevant to
  the stale-`TL0` e-stop trap); status `|P:…|` progress token format change.

### The `M495` auto routine (margin / Z-probe / level / goto-origin)

`Controller.autoCommand()` builds a single `M495`:

```
M495 X<xmin> Y<ymin> [C<xmax> D<ymax>] [O<zx> F<zy> | O0 | R0] [A<w> B<h> I<i> J<j> H<h>] [P1]
```

| Token | Meaning |
| --- | --- |
| `X<xmin> Y<ymin>` | Work-area start = the loaded path's bounding-box min, in work coords. |
| `C<xmax> D<ymax>` | Margin-scan max corner (only when margin scan is enabled). |
| **`O<zx> F<zy>`** | **Z-probe touch-down offset in X/Y** — the "probes 5x 5y from the work start" point. Editable in the Z-probe popup; default `5, 5`. **Accepts negatives** (e.g. `O-5 F5` to probe on the −X side — useful when the +X face is being machined). |
| `O0` / `R0` | Absolute-Z probe variants (4-axis path sets `zprobe_abs`). |
| `A B I J H` | Auto-leveling grid: width, height, X points, Y points, clearance. |
| `P1` | Return to work origin after probing. |

So the bare auto-Z-probe the Controller runs is effectively
`M495 X<xmin> Y<ymin> O5 F5 P1` (then it prompts for the first job tool). Related,
also in the source: `M495.3 H<h> D<dia>` (full XYZ probe), and commented-out
`M494` (Z-only) / `M496` (probe-leveling) showing the older split commands.

### Tool numbers, and the laser probe (what we use)

The probe touch-down in `M495` is performed by whatever **probe tool** is loaded.
From `Controller.changeToolCommand()` / `setToolCommand()` the tool slots are:

| Tool | Number | Change (physical) | Set (logical) |
| --- | --- | --- | --- |
| **Probe** | **T0** | `M6T0` | `M493.2T0` |
| **Laser** | **T8888** | `M6T8888` | `M493.2T8888` |
| Empty | T-1 | `M6T-1` | `M493.2T-1` |
| ATC bits | T1–T6 | `M6T<n>` | `M493.2T<n>` |

> **On this machine we probe with the LASER module, not the touch probe.** When
> the Controller's `M495` routine prompts for the probe tool, the operator loads
> the **laser module** into the collet and continues — so the auto-Z surface
> probe is done with the laser head (its Z reference is `laser_module_offset_z`,
> currently −7.0, and XY `laser_module_offset_x/y`). The **wireless touch probe**
> path — tool **T0**, paired with **`M471`** (the "press the Wireless Probe until
> the green LED blinks" flow in `makera.py`) — **exists but is not used here**, so
> ignore touch-probe pairing prompts/errors (`Wireless probe dead or not set`).
> The laser engraving mode toggles (`M321` on / `M322` off) are separate from
> using the laser as a probe.

> **The Controller's logs do NOT capture the command stream.** `CarveraController`
> writes a Kivy log to `C:\Users\<user>\.kivy\logs\kivy_<date>.txt`
> (WSL `/mnt/c/Users/<user>/.kivy/logs/`), but it only records framework events
> and **XMODEM file-transfer blocks** (uploads/downloads) — not the G-code/MDI it
> sends. To learn a routine, read the source above, not the log. (`MakeraCAM`'s
> CAM-side logs live at `…/AppData/Roaming/MakeraCAM/logs/` and are likewise not a
> command trace.)

## Reading machine state (position, tool length)

Read-only queries you can send with `command`:

- **`?`** — realtime status, one line of pipe-delimited fields:
  `<State|MPos:...|WPos:...|F:...|S:...|T:<n>,<len>,<pending>|...>`.
  The **`T:` field is `<active tool>,<tool-length offset mm>,<pending tool>`** —
  e.g. `T:1,26.963,-1` = tool 1, offset **26.963 mm**, no pending tool.
  *(Stock-1.0.2 format and value — on community 2.1.0c the field has FOUR
  sub-fields, TLO = cutter stickout (T1 ≈ 69.14), and the tool number can be
  garbage mid-ATC: gate on `$#` instead.)* That
  middle number is the value `M491` writes: a **relative offset** of this tool's
  tip against the machine's tool-setter datum, **NOT the bit's physical length**
  — so don't expect it to match the flute length (e.g. 52 mm) or the stickout.
  Our pipelines re-zero Z on the true stock top anyway, so this offset is
  informational, not load-bearing for cut depth. *(Verified 2026-06-06: `M491`
  on T1 ran clean — `ok` / `M497.3` / `ok` — and `?` then reported
  `T:1,26.963,-1`.)*
- **`M114`** — current position (`C: X.. Y.. Z..`). `M114.1` reports the last
  probe position. (`carvera-cli` special-cases `M114` and parses the `ok c:`
  reply; `M114.1`'s `ok wcs:` reply trips its "expected ok" check but the value
  still prints — harmless.)
- **`$#`** — work-coordinate offsets: one line each for `G54`–`G59.3`, plus
  `G28`/`G30`/`G92`, **`TL0`** (active tool-length offset) and **`PRB`** (last
  probe result in machine coords, trailing `:1` = success / `:0` = fail). This
  is the canonical read of where the work zeros are.
- **Active WCS:** `$G` (GRBL parser-state) is **rejected on this firmware**
  (`error:Invalid statement`), so you can't ask which WCS is modal-active
  directly. Derive it: the active WCS is the one whose offset satisfies
  `WPos = MPos − (WCS_offset + TL0)` (from `?` and `$#`). In practice it's
  **G54** — its XY offset equals `coordinate.anchor1_x/y` (the object is loaded
  on anchor 1).

## Threaded holes — thread milling

Tapped holes are a solved job on this machine: helical-bore the pilot with
whatever endmill/ball nose is smaller than the minor diameter, then run
one of Makera's multi-tooth thread mills up a climb helix at the thread
pitch. `cam/thread_mill/thread_holes.py` emits the whole thing (pilots +
threads, one file, one `Tn M6` per tool) from a probed `geometry.json`;
M3 and M5 recipes are proven in epoxy (2026-08-17, screws ran in
first try). Recipe, feeds (centre-feed conversion!), asserts and the log:
[`cam/thread_mill/thread-mill.md`](../cam/thread_mill/thread-mill.md).

## Fixtures at the anchor — heights to keep the tool above

The block sits against an L-bracket on anchor 1; anything that cuts through
or runs out past the Y−/X− faces must keep the tool tip **above the bracket
top** (and the shank/nut clear of it):

- **Large ("thick") L-bracket — 15 mm tall** (measured on ours, 2026-08-17).
  It's the one Makera pairs with the side clamps for stock thicker than
  20 mm, so on a tall block "the fixtures start at (block height − 15)"
  below the top — 22.5 mm down on the 37.5 mm epoxy block. Encode it in
  the toolpath generator: `part_off_gcode.py --obstacle-z <work Z of the
  bracket top>` refuses a floor within 2 mm of it.
- **Small ("thin") L-bracket / positioner — 5 mm tall** (measured on ours,
  2026-08-17). The one in the standard Air kit, used with the top clamps
  for stock under 20 mm. Makera publishes no height for either bracket
  (product page, wiki tool-kit page and the Amazon listings only say
  "thin"/"thick" and the 20 mm workpiece split) — these two numbers are
  our own measurements.

## Getting a fresh job to start *at the anchor* (pre-flight checklist)

The recurring first-timer stumble is *"why won't the job run where I put the
part?"* — an agent trying to establish an XY work origin that the machine
**already has**, and fighting it. The fix is three **read-only** reads before
you send anything (all safe at any time), then let the anchor do the work.

**The one idea:** on this machine you do **not** set or re-zero XY per job.
`G54`'s XY offset is a *machine constant* already equal to anchor 1
(`coordinate.anchor1_x/y`). A job "starts at the anchor" for free **when the
toolpath is authored in that frame** — place the part in **+X/+Y from anchor 1
at a known offset** (e.g. tangent to both axes → part centre at
`(radius, radius)`) so every coordinate is positive and referenced to anchor 1.
The only per-job prep is then **home + auto-Z-probe the stock top** — no
work-origin dance. (See the CAM side of this in
[`cam/drill_m5_hole_metal_v2/`](../cam/drill_m5_hole_metal_v2/drill-m5-hole-metal-v2.md).)

**Pre-flight reads — and what each tells you:**

1. **`?`** → `<State|MPos:...|...|T:<active>,<TLO>,<pending>|...>`
   - **State** must be `Idle` before you send motion. `Alarm`/`Halt` needs
     recovery — and **never auto-unlock it** (safety rule in CLAUDE.md; an
     e-stop may mean hands-in-machine or bit-in-work).
   - **`MPos:-1.0000,-1.0000,-1.0000` = NOT HOMED** (a power-cycle loses
     position). Coordinates are meaningless and `WPos` is stale until you home,
     so you **must** `G28` first (`send_to_cnc.sh --home`). If it shows real
     coordinates, it's already homed.
   - **`T:<active>,...`** — if it already reads your job's tool you'll still get
     the manual confirm at the file's `Tn M6`; if not, expect the swap prompt.
2. **`$#`** → confirm **`G54` X/Y is non-zero** (the anchor-1 offset). If `G54`
   X/Y reads `0,0` the anchor isn't set and the job runs in the wrong place —
   stop and fix that before sending. (`G54` **Z** is stale; the auto-Z-probe
   rewrites it — ignore it here.)
3. **Skip `--unlock` unless `?` actually showed an Alarm.** `--unlock` sends
   `M999`, the e-stop-recovery command — on an already-`Idle` machine it's
   needless, and reflexively unlocking an alarmed machine is exactly the banned
   move.

### What a `send_to_cnc.sh --home --auto-z-probe DX DY` run actually does

It fires its steps back-to-back and returns quickly, but **`ok` means "queued",
not "finished".** The machine then sequences on its own, **pausing for you at
two tool loads** — a bare send is *waiting on you at the machine*, not hung:

1. `G28` home (Z lifts first).
2. `M495` auto-Z-probe → prints `Change to probe tool first!` and enters
   **`<Tool>` state** waiting for the **laser probe**; `?` shows the pending-tool
   field as `0` (`T:<active>,...,0` → pending **T0 = probe**). Load the laser,
   confirm at the machine → it probes at work `(xmin+DX, ymin+DY)` and rewrites
   the work Z zero to the stock top.
3. The queued `play` runs; its `Tn M6` prompts you to load the **cutting tool**
   → confirm → it cuts.

Keep a hand on the e-stop through the first move after each tool change and the
first plunge. **For a shallow job** chaining probe+cut in one launch is fine.
**For tall stock** where the spindle can't lift fully clear of the part, do
**not** chain them — the post-tool-change reposition can drag across the work
(gouged a plate once). Split probe and cut into **two separate launches** — see
the tool-change-reposition note below.

### Picking the auto-Z-probe offset (`DX DY`) so it lands on solid stock

`--auto-z-probe DX DY` probes at work `(xmin+DX, ymin+DY)`, where `(xmin, ymin)`
is the **min X/Y of the job's `G0`–`G3` endpoint words** (`send_to_cnc.sh`'s awk
parse; for an **arc-only** bore that's the arc *endpoints*, not the bulge).
Compute where that point lands and make sure it's **on solid material, clear of
any hole/pocket and the stock edge** — a touch-down in mid-air or off the edge
crash-plunges the laser. Negative offsets are allowed (`-5 5`) to pull the probe
back onto stock, away from a face being machined (as the end-trim job below
does with `O-5 F5`). Worked example — a Ø25 disc, hole centred at (12.5, 12.5),
path-start corner (10.85, 12.5): `--auto-z-probe 1.65 6` probes at (12.5, 18.5),
6 mm out along the +Y centreline, well clear of both the hole and the rim.

## Prepping the machine before a job (verified raw sequence)

The Makera Controller hides pre-job prep (probe the stock, measure tools, load
the first tool) behind its UI. Below is the **equivalent raw command sequence**,
**verified end-to-end on the stock Carvera Air on 2026-06-06** against an
end-trim job: a side-profiling pass that squares the +X end face of a hardwood
brick, toolpath box `X158→166`.

Two things to know going in:

- **It's interactive.** Several steps **prompt for a physical tool swap and then
  wait**. `carvera-cli command` will appear to *time out* while you do the swap,
  but the machine keeps running its queued scripts (same as `play`). Keep a hand
  on the e-stop.
- **Order matters: probe Z with the probe tool *first*, then switch to the
  cutting tool.** Tool-length compensation then keeps work `Z=0` on the stock top
  across the swap (verified below).

### 1. `M491` — calibrate the cutting tool's length
Run with the job tool (T1) loaded. Writes its tool-length offset; read it back
from `$#` (on stock 1.0.2 we measured **T1 = 26.96 mm**; on community 2.1.0c
TLO = stickout, **T1 ≈ 69.14**). Do this *before* swapping
to the probe so T1's length is on record.

### 2. `M495 X<xmin> Y<ymin> O<dx> F<dy> P1` — auto Z-probe the stock top
- `X<xmin> Y<ymin>` = the **job toolpath's bounding-box min** in work coords —
  read it from the gcode (for that end-trim job: `X158 Y0`).
- `O<dx> F<dy>` = **Z-probe touch-down offset** from that corner; the probe lands
  at **work `(xmin+dx, ymin+dy)`**. Negatives are allowed.
- `P1` = return to the work-area origin after probing.
  - ⚠️ **Expect the head to move to the work origin (0,0 corner) before the cut
    runs.** When you use `--auto-z-probe` on the start command, `M495`'s `P1`
    return parks the head at the **workpiece origin at clearance height** before
    the gcode plays. Be aware of anything the gantry/tool could hit there —
    **fixtures, clamps, or stock sitting at or above that corner** — and keep a
    hand on the e-stop through the return. On **tall stock** where the tool can't
    lift above the workpiece top this return can drive the bit **into the top of
    the workpiece** (the reason a tall brick probes and cuts as two
    separate launches).
- It prints **`Change to probe tool first!`**, lifts to Z clearance, and **waits
  for you to load the probe** — we load the **laser as the probe**. Insert it,
  confirm, and it probes then returns. If you load *no* probe tool it just does
  the Z-clearance move and stops (safe — no plunge).
- **The near-instant `ok` is command *queuing*, NOT a skipped probe.** `carvera-cli
  command` (and `send_to_cnc.sh --auto-z-probe`) returns `ok` within a fraction of
  a second because the firmware *accepts and queues* the M495 — it then runs on the
  machine and **waits at the tool-change prompt for you to load the laser probe**.
  Do **not** read the fast `ok` (or a following queued `play`) as "it didn't probe
  and is cutting blind." The probe genuinely happens against the loaded laser; the
  cut only proceeds after the probe completes and T1 is reloaded. (An earlier
  session misdiagnosed this queuing as a failed probe — it is not.)
- The probe **rewrites `G54 Z` to the stock top**, so `Z=0 = stock top`. Confirm
  with `$#` (we saw `G54 Z` move to −22.9125).
- **Mind the offset sign vs. the toolpath.** This job's box is `X158→166`, and
  **`X160→166` is free air** off the +X end being machined. A default `+5x` probe
  (→ ~163) would touch down **mid-air and crash-plunge**. Because we cut the +X
  face, use **`O-5 F5`** to pull the probe back onto solid wood — verified: it
  probed at **work (153, 5)**.

### 3. `G28` — go to clearance / park
`$H` is rejected on this firmware; `G28` ("goto clearance position") lifts Z
first, so it's safe even with the tool near the stock.

### 4. `M6T1` — load the job's cutting tool
On this setup **T1 is hand-loaded, not in the ATC**, so this is a *manual* change
done entirely by the one command: it prints `Please change the tool to: T1`,
lifts to Z clearance, moves to the manual-change position, **waits for you to fit
T1**, then **auto-probes the new tool's length** and applies its TL0.

> ### ⚠️ Every real tool change ends with a move back toward the work origin — STOCK 1.0.2 ONLY
>
> *(Gone on community 2.1.0c: `M6` returns to its request XY, watched live
> 2026-08-15. Kept for rollback.)*
>
> **`M6` doesn't stop at the tool-setter — it finishes by rapiding the gantry
> back toward the work origin / resume position** (near the `(0,0)` work corner).
> This fires after **any tool change that actually swaps the tool** (T-number
> changes): the `M495` probe swap, a standalone `M6T1`, or a `T1 M6` inside a
> played job. A **no-op `M6`** (tool already loaded, T-number unchanged) does
> **not** move.
>
> On normal-height stock this return is harmless — Z lifts clear and traverses
> over the top. **It only bites on tall stock / deep pockets where the tool
> can't lift above the workpiece top** (a Z-reach deficit): then the return move
> traverses at a height *still inside the stock* and **drives the tool into the
> workpiece** (`Alarm H:13`, Z soft limit — can gouge the part). Verified live
> 2026-07-16 on a ~70 mm hardwood brick, across three separate tool
> changes.
>
> **Implications for tall work:**
> - **Keep a hand on the e-stop and stop the gantry after every real tool
>   change**, the instant it heads back toward the work origin, *before* it
>   reaches the stock. `TL0` and `G54` survive the e-stop; recover with `M999` +
>   `G28`.
> - **Run the cut with the tool already loaded** so the file's `T1 M6` is a
>   no-op — then no tool change happens mid-job and **no return move fires**. Set
>   Z and load T1 as separate *setup* steps first. This is the whole reason a
>   tall brick probes and cuts as two separate launches.
> - Doesn't matter for most jobs (short stock, tool clears the top); **it's
>   specifically a large-/tall-workpiece gotcha.**

#### Tall-stock workaround: change a tool WITHOUT the origin move (`M493.2` + `M491`) — optional on 2.1.0c

*(On community 2.1.0c a plain `M6` requested from the clearance corner has no
origin move, so this recipe is no longer required — it still works and remains
useful for registering the 3D probe without motion.)*

On a workpiece with a Z-reach deficit you can load a tool **without ever
triggering the origin-move** — skip `M6` entirely:

1. **Hand-load the tool** into the collet and confirm it's seated.
2. **Register it logically:** `M493.2 T<n>` — sets the *active tool number* with
   **no physical change and no motion** (so no origin move). *(Verified live
   2026-07-17 on a ~70 mm hardwood brick.)*
3. **Measure its length:** `M491` — probes the **tool-setter** (a fixed spot off
   to the side, **not** the work origin — it sends `G38.6 Z…` down onto the
   setter and returns to clearance) and writes `TL0`. **No move over the stock.**
4. **Verify** `?` / `$#`: `T:<n>` active with a sane `TL0` (≈ the tool length),
   and work Z=0 back on the stock top.

> ⚠️ **`M493.2` alone is NOT enough — you MUST follow it with `M491`.** `M493.2`
> sets the tool *number* but leaves **`TL0 = 0`** (the length offset isn't
> restored). If you cut with `TL0 = 0`, work Z=0 is off by ~the tool length
> (~27 mm) and the tool plunges straight into the part. `M491` is what puts the
> real offset back. (Caught live 2026-07-17 — `$#` showed `[TL0:0.0000]` right
> after `M493.2`.)

This is only worth the extra step on **tall / clearance-limited stock**. On
normal stock, just use `M6T<n>` — its origin move is harmless.

### 5. Verify before cutting (`$#` + `?`)
- `T:1` active; `TL0` back to the M491 value (stock 1.0.2: router 26.96–26.99;
  community 2.1.0c: stickout ≈ 69.14).
- `G54 Z` **unchanged** from the probe (≈ −22.91 was that stock-era setup's
  value — always compare against the number you probed *this* setup) — the
  stock-top reference
  survived the tool swap.
- Optional belt-and-braces: jog T1 down to kiss the stock top and confirm `?`
  shows work `Z ≈ 0`.

With `G54 Z` at the probed top and the router's `TL0` applied, commanding work
`Z=0` lands the **router tip on the stock top** — so the cut file's
`Z=0 = stock top` assumption holds.

### Recognising the two interactive tool-change pauses (what they look like)

A `send_to_cnc.sh --unlock --home --auto-z-probe DX DY` run **pauses twice** for a
physical tool swap, and each pause is **resolved at the machine** — insert the
tool into the chuck and **press Resume on the Carvera's screen**. carvera-cli has
already returned `ok` (the command is queued and running on the machine); you do
**not** send anything more from the CLI to continue. Both pauses show up as
machine **State `Tool`** in `?`, and the `T:<active>,<len>,<pending>` field's
**pending** number tells you which tool it wants. *(Captured live 2026-07-16 on
a channel-finishing job resume.)*

**Pause 1 — load the LASER probe (for `M495`).** During the run the CLI prints:

```
Change to probe tool first!
Auto z probe, offset: 20.000, -10.000
goto x and y clearance first
ok
G53 G0 Z-3.000
ok
```

The machine then **waits in State `Tool`** for you to fit the **laser module** as
the probe and press Resume. It then probes and returns. Confirm the probe
actually touched down with `$#`: the **`[PRB:x,y,z:1]` line's trailing `:1` = a
successful contact** (`:0` would be a miss), and `G54 Z` is rewritten to the
stock top. Verify the probe landed where you intended by backing out the work
coords: `PRB_xyz − G54_offset` (e.g. `PRB X −279.359 − G54 X −289.36 = work
X 10.0`, matching an `O20` offset off an `xmin −10` path corner).

**Pause 2 — reload T1 (the cut tool).** After the probe, the cut file's `T1 M6`
fires the second pause. `?` looks like:

```
<Tool|MPos:-31.3600,-5.8800,-3.0000,0,0|WPos:258.0,196.0,21.68,-90,0|...|T:0,0.000,1|...|P:21,2,199|C:2,1,0,1>
```

State **`Tool`**, `T:0,0.000,1` = **active tool 0 (the probe), pending tool 1** —
it's waiting for you to fit **T1** and press Resume. On Resume it auto-measures
T1's length (`TL0`), keeping `Z=0` on the stock top across the swap, and only
*then* runs the first cutting move. Nothing to send from the CLI — act at the
machine.

> **What this prep is for.** These steps establish the machine state the cut
> file then runs against: tool length calibrated (`M491`), work Z zeroed on the
> stock top (`M495`), and the job tool loaded (`M6T1`). The cut file carries its
> own `G54` + `T1 M6` preamble; these up-front commands are just the hand-prep
> done explicitly, equivalent to `send_to_cnc.sh --unlock --home --auto-z-probe`.

## Cleaning under the bed (Y travel extremes)

To clear the gantry to one end so you can clean the bed, jog Y to a travel
extreme in **machine** coordinates (`G53`), with X/Z parked up at the clear home
corner. The Y soft endstops are `y_min -212` (far end) and the home/max at `0`
(near end); leave a ~2 mm margin so you don't trip the soft limit:

```bash
C="--wifi --device $CARVERA"
# Near end (+Y, just before the 0 / home soft limit):
python/venv/bin/carvera-cli $C command "G53 G0 Y-2"
# Far end (just before the y_min -212 soft limit):
python/venv/bin/carvera-cli $C command "G53 G0 Y-210"
```

Run from Idle (`G28` home first if needed — that also parks X/Z up near the home
corner, clear of the stock). `G53` is machine-coordinate, so it ignores the work
offset and moves only the given Y. `G28` to return to clearance.

## Audible Profile (tuning for a quiet shop)

We run in a shared space and prefer **quiet** jobs so we don't disturb the
neighbours. This section logs how particular settings *sound* and is a lookup
for trading noise against speed — grow it as we learn.

### Observations
- **37 mm full-height side-profiling (52 mm up-cut bit, end-trim job).** The deep
  full-flute axial engagement made the cut go **low/deep and notably quiet** —
  much quieter than you'd expect for the amount of material moved (no
  high-pitched whine; it "lugs" at a low frequency).
- **Deep-axial trochoid at FULL feed (slot-pocketing job, VALIDATED 2026-06-06).**
  The counter-intuitive win: **2 × 25 mm-deep** trochoid passes at **0.2 mm**
  radial step and the **full 1080 mm/min** feed (5400 rpm) ran **quiet AND fast**
  — a "super manageable amount of sound" and *"cutting hella fast"*, spindle
  holding ~**5363 / 5400 rpm** (barely drooping, steady). The history that led
  here is the lesson: a 6 mm-wide, **8 mm-deep** band slot at 1080 *slammed/
  stalled*; a trochoid at 1080 was *loud (spindle labouring, rpm fluctuating)*;
  **slowing** the feed (450 → 225 → 56, band 38) only made it quieter-but-still-
  loud and risked rubbing. Going the **other way** — much deeper axial (25 mm)
  with a thin radial bite and the full feed — was quietest of all. A flute
  buried deep along its length is held against the cut and can't ring;
  **constant, deep engagement is what kills the chatter**, not a timid cut.

### What makes a Carvera cut quieter (lookup)
- **Keep the bit constantly, DEEPLY engaged — counter-intuitive, but the biggest
  lever we've found.** Chatter/squeal is the bit *ringing* when it's lightly or
  intermittently loaded; a deep axial pass (flute buried along its length) damps
  it. So you can often run a BIGGER axial DOC **and MORE feed** and get a
  *quieter* cut than a shallow, timid one. Reach for **deep-axial + light-radial
  (a trochoid)** at full feed *before* reaching for a slower feed. (Validated on
  a slot-pocketing job: 25 mm axial / 0.2 mm radial / 1080 feed beat both an aggressive
  shallow cut and a quartered-feed shallow cut on noise AND speed.)
- **Lower feed rate** helps *only when the bit is already well-engaged* — on a
  light/shallow cut, slowing the feed can tip into rubbing (thin chip at 5400 rpm)
  which is *louder*, not quieter. It is **not** the first lever to reach for.
  (Our Quiet bits run 650 / 220 vs standard 1080 / 350, paired with light steps.)
- **Lighter stepover + stepdown** (the Quiet bits use **0.5 / 0.5 mm**) — but see
  the first point: light *radial* is good, light *axial* invites chatter.
- Up-cut spiral clears chips (less re-cutting); a sharp, clean edge is quieter.

### The "Quiet" tools (in `tools/`)
Dedicated quiet-tuned bits — same geometry as the standard hardwood bits but
gentler feeds/steps:

| Tool (`tools/…`) | Name | Flute | RPM | Horiz | Plunge | Stepover | Stepdown |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `endmill_6mm_2F_25L_quiet_hardwood.fctb` (ATC **T2**) | 6mm 2F 25mm Quiet End Mill | 25 mm | 5400 | 650 | 220 | 0.5 | 0.5 |
| `endmill_6mm_2F_52L_quiet_hardwood.fctb` | 6mm 2F 52mm Quiet Up-Cut Router Bit | 52 mm | 5400 | 650 | 220 | 0.5 | 0.5 |

Both came from Fusion 360's "Quiet" hardwood/plastic presets. Compare the
standard `endmill_6mm_2F_52L_hardwood.fctb` (1080 / 350): the quiet variants
trade ~40 % feed for a markedly calmer cut. Reach for these when running
out-of-hours or near the neighbours.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `gcc ... No such file or directory` or missing `Python.h` during install | `pyquicklz` is compiling; install `build-essential python3-dev` (Gotcha 1). |
| `scan` finds no devices on WSL | NAT blocks the UDP broadcast; find the IP by scanning TCP 2222 (see *Finding the machine*) and connect with `--device`. |
| `Connection reset by peer` right after "connection established", then timeout | Something else holds the machine's single connection — **disconnect the Makera Controller** (or power-cycle the Carvera to clear a half-open session). |
| TCP 2222 **refused on every host** subnet-wide (a scan finds nothing) even though the LAN is fine (gateway pings, other hosts' web ports open) and the machine is powered on and answers ping | Same root cause, seen from the scan side: while the **Makera Controller (or any client) holds the single connection**, the firmware stops accepting new TCP-2222 connections, so the machine looks "gone" to a port scan. **Disconnect the Controller in-app**, then re-scan — the control port reappears (verified 2026-07-16: reappeared at the same IP seconds after disconnect). Not a DHCP/IP problem — a full-range scan would still catch a moved IP. |
| `AttributeError: ... 'WiFiStream' has no attribute 'UDP_PORT'` | Upstream bug in the "no devices found" path of `scan`; ignore — use `--device ... command version` to check reachability. |
| Connection times out by IP | Wrong IP, machine asleep/off, or firewall on the Windows host blocking the WSL subnet. Confirm the IP in the Makera Controller; bump `--timeout`. |
| `No module named 'serial'` / `'quicklz'` | Deps didn't install — re-run step 4; if `quicklz` specifically, the C build failed (Gotcha 1). |
| `<Alarm\|...>` in status / machine won't move | Halted (e.g. spindle stalled). `command "M999"` to unlock, then `command "G28"` to home. |
| `error:Invalid statement` from `$H` | `$H` isn't supported; use `G28` to home. Also single-quote `$...` in bash or the shell blanks it. |
| `Cannot execute empty command string` | The shell expanded a `$...` arg (e.g. unquoted `$H`); single-quote it: `command '$H'`. |
