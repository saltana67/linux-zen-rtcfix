# Handoff — linux-zen-rtcfix

Living record of project state. Update at the end of each work session.

## Working agreement

Gated workflow: **propose → approve → act**. Evidence over assumption — read the
file/log/output, don't guess. The assistant cannot run commands on the machines;
the operator runs them and pastes output. Login shell is **fish** (mind `&|`, no
bash-isms at the interactive prompt — the build script is `#!/usr/bin/env bash`
and runs fine regardless).

## Environment — reference machine (the NUC)

> Real values, published as a concrete worked example. None of it is sensitive:
> filesystem/swap UUIDs and the hostname are local identifiers, not network
> reachable and not secrets. On your own box, substitute your own (see README →
> "Reproducing on your own machine").

- Host `brix-gbbrr7h4800`, user `adam`. ASUS NUC13 Pro, i7-1370PE (6P + 8E = 20 threads), 64 GB.
- OS: Garuda (Arch-based), KDE Plasma 6, fish. Verified config-identical to stock Arch `linux-zen`.
- Kernel: Arch `linux-zen` is the build target; patched build installs side-by-side as `linux-zen-rtcfix`.
- Boot: btrfs root, subvol `@`, root UUID `6411dc93-a161-4759-be12-fb3607a39274`. mkinitcpio + GRUB on the ESP.
- Sleep: only s2idle works (S3/deep is broken on this board). Load-bearing cmdline:
  `resume=UUID=6d8bad7e-89f3-46fa-a51e-8f5824108b64 rtc_cmos.use_acpi_alarm=1`.
- Swap / resume device: `/dev/nvme0n1p3`, UUID `6d8bad7e-89f3-46fa-a51e-8f5824108b64` (129.5 G).

## Status

- ✅ Root cause found and source-verified — see `docs/DIAGNOSIS.md`.
- ✅ Patch authored and verified to apply against pristine v7.0.10
  (`git apply --check` and `patch -Np1`).
- ✅ Build automation (`build-rtcfix.sh`) — patch-agnostic, fail-loud, env-driven
  (no `makepkg.conf` edits, so container-portable).
- ✅ A/B proof: control 12/12 fail at worst phase, patched 0/12; `--hunt` clean.
- ✅ Real-world KDE confirmation at a 3-minute `HibernateDelaySec`.
- ⏳ Containerized build env (future `ai-dev-template`).
- ⏳ Battery-laptop test of systemd's other branch.
- ⏳ Upstream submission.

## Two tracks

### Track 1 — carry locally (active)

Rebuild on every kernel bump and on the laptop with `build-rtcfix.sh`. Iterate
the patch with `-n` (fast apply-check) → `-i` (build + install) → reboot →
`tools/s2h-sweep.py`. Containerize when time permits; the script already lifts
into a clean Arch `base-devel` + `devtools` + `pacman-contrib` container as-is
(use `-b latest` or `-b TAG` there, since `-b installed` reads the host pacman DB).

### Track 2 — upstream (once the patch is refined)

The kernel takes **email patches, not pull requests**. Workflow on a kernel tree:

1. `scripts/get_maintainer.pl <patch>` — the recipients (maintainers + lists).
2. `scripts/checkpatch.pl --strict <patch>` — style; fix nits (settles the
   nested-block question).
3. Set a real `From:` / `Signed-off-by:` (currently placeholders).
4. `git send-email` or `b4` to the timekeeping/alarmtimer maintainers + linux-pm
   + LKML. Add `Link:` to systemd issues #35743 / #24279.

`docs/DIAGNOSIS.md` is the basis for the commit message and cover letter.

## The battery-branch question (laptop)

The probes write `/sys/power/state` directly, so they exercise the **kernel**
alarmtimer regardless of battery — the bug and the fix should reproduce
identically on the laptop. The separate, unanswered question is whether
systemd's real battery-present branch (`battery_trip_point_alarm_exists()` true)
even reaches the racy zero-timeout poll — i.e. whether battery laptops are
*user-visibly* affected at all. Read systemd `src/sleep/sleep.c` to form the
hypothesis before building/testing there.

## Loose ends (anytime, unrelated to the fix)

- `/etc/mkinitcpio.conf.pacnew` — keep the live file; the pacnew drops the
  resume hook. **Do not apply it.**
- `/etc/pam.d/kde.pacsave` — leftover, safe to remove.
- Pre-existing broken-soname packages (old Qt5/KF5, vmware-horizon,
  icu/libvpx/libical bumps) — unrelated cruft to clean someday.

## Patch provenance

`patches/0001-alarmtimer-preserve-sub-second-offset.patch` — `git format-patch`
against pristine v7.0.10. `From:` / `Signed-off-by:` are placeholders until the
upstream identity is set (needed for the DCO sign-off).
