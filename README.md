# linux-zen-rtcfix

A one-patch fix for an intermittent **suspend-then-hibernate** bug, packaged so
it builds and installs side-by-side with Arch's stock `linux-zen` kernel — plus
the harness that proves it works.

## The bug, in brief

systemd's suspend-then-hibernate arms a `CLOCK_BOOTTIME_ALARM` timer, drops to
s2idle, and on resume decides whether to hibernate with a *single zero-timeout
poll* of that timer. The kernel's `alarmtimer_suspend()` builds the wake alarm
from an RTC time that has been **floored to whole seconds**, so the alarm can
fire up to ~1 s early — early enough that, at the poll, the timer isn't marked
fired yet, systemd concludes "the user woke me," and the machine never
hibernates. It misfires only at certain sub-second phases, which is why it looks
random (~1 in 4 cycles here).

The fix recovers the discarded sub-second fraction from the system clock so the
alarm is derived from the true RTC instant; the kernel's existing
round-up-to-the-next-second then guarantees the timer is *never* early. Full
write-up: **[docs/DIAGNOSIS.md](docs/DIAGNOSIS.md)**.

## What's here

| path                 | what                                                                   |
|----------------------|------------------------------------------------------------------------|
| `build-rtcfix.sh`    | clone stock `linux-zen`, apply the patch(es), build `linux-zen-rtcfix` |
| `patches/`           | the kernel patch(es) carried on top of the zen patch                   |
| `tools/s2h-probe.py` | one suspend cycle, prints the verdict                                  |
| `tools/s2h-sweep.py` | phase sweep / `--hunt` / `--phase` reps: the statistical proof         |
| `docs/DIAGNOSIS.md`  | root-cause analysis + why the patch works                              |
| `docs/HANDOFF.md`    | current project state and roadmap                                      |

## Prerequisites

- An Arch-based system with `linux-zen` installed (developed on Garuda).
- `devtools`, `base-devel`, `pacman-contrib`.
- Secure Boot **off** — the resulting package is unsigned.
- Build as a normal user (makepkg refuses root); run the probes as root at a console.

## Build and install

```sh
# 1. check the patch still applies to your installed kernel (~2 min, no compile)
./build-rtcfix.sh -n

# 2. build a kernel matching your installed linux-zen, then install it (~30–45 min)
./build-rtcfix.sh -i
#    reboot and pick the "linux-zen-rtcfix" boot entry
```

Other targets: `-b latest` (upstream HEAD), `-b 7.0.10.zen1-1` (an exact tag).
Drop more patches into `patches/` and they stack. Each build gets a timestamped
`pkgrel`, so `uname -r` always tells you which build you booted. The script
**fails loudly** if a patch stops applying after a kernel bump, rather than
silently shipping an unpatched kernel.

## Verify the fix

The probes reproduce systemd's exact wake-detection logic *without* involving
systemd, so they isolate the kernel behaviour. Run at a text console and **do
not touch the keyboard or mouse while it sleeps** — an input wake confounds the
result:

```sh
# single cycle, 8 s delay
sudo python3 tools/s2h-probe.py 8

# find this box's worst sub-second phase, then hammer it
sudo python3 tools/s2h-sweep.py 8 --hunt --reps 12
```

Read the output by two fields:

- `offset = slept − N` — how early or late the wake was. **Negative = early = the bug.**
- `woken_by_timer` — systemd's verdict at its zero-timeout poll. **False = would not hibernate.**

On a stock kernel the worst phase fails ~100 % (`offset` negative,
`woken_by_timer=False`). On the patched kernel every offset is positive and
`woken_by_timer=True`, and `--hunt` reports *no failing phase in band*.

## Reproducing on your own machine

The probes and build script have no machine-specific values baked in. The only
thing you supply is your own swap UUID in the kernel `resume=` cmdline (and
confirm s2idle is your working sleep state). The concrete values quoted in
`docs/HANDOFF.md` are this project's reference machine — substitute your own.

## Status

- ✅ Fix working locally, proven by a single-variable A/B (see DIAGNOSIS).
- ⏳ Battery-laptop path (systemd's other branch) not yet tested.
- ⏳ Upstream submission not yet sent.

## License

The kernel patch is **GPL-2.0-only** by derivation. The build script and probes
are **MIT** (see each file's `SPDX-License-Identifier`).
