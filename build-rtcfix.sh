#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# build-rtcfix.sh - build a locally-patched linux-zen kernel package.
#
# Clones the current Arch `linux-zen` packaging repo, applies one or more local
# patches on top of the zen patch, and builds it as `linux-zen-rtcfix` so it
# installs SIDE-BY-SIDE with the stock kernel. Designed to be re-run on every
# kernel bump: it FAILS LOUDLY (never silently ships an unpatched kernel) if a
# patch stops applying because upstream moved.
#
# Patch-agnostic: drop any .patch into ./patches/ (or pass -p) and rebuild - so
# you can iterate on the fix and spin a fresh test kernel cheaply.
#
# USAGE
#   ./build-rtcfix.sh [-p FILE]... [-b installed|latest|TAG] [-s SUFFIX]
#                     [-n] [-i] [-w DIR] [-c DIR] [-o DIR]
#
#   -p FILE   Patch to apply (repeatable). Default: ./patches/*.patch
#   -b WHAT   Base: 'installed' (default; match the running linux-zen),
#             'latest' (upstream HEAD), or an exact tag e.g. 7.0.10.zen1-1
#   -s SUFFIX pkgbase suffix (default: rtcfix -> linux-zen-rtcfix)
#   -n        Check only: extract + apply patches, do NOT compile (fast gate)
#   -i        Install the built main package with `sudo pacman -U` afterwards
#   -w DIR    Work dir              (default ./.build)    - safe to delete
#   -c DIR    Source cache/SRCDEST  (default ./.srccache) - keeps the kernel tarball
#   -o DIR    Output dir            (default ./out)
#
# Run as a NORMAL user (makepkg refuses root). Needs: devtools, base-devel,
# pacman-contrib. Builds with -j$(nproc) regardless of makepkg.conf.
#
set -euo pipefail

PKG="linux-zen"
PATCHES=(); BASE="installed"; SUFFIX="rtcfix"
CHECK=0; INSTALL=0
WORKDIR="$PWD/.build"; SRCDEST_DIR="$PWD/.srccache"; OUTDIR="$PWD/out"

while getopts "p:b:s:niw:c:o:h" o; do case "$o" in
  p) PATCHES+=("$OPTARG");;
  b) BASE="$OPTARG";;
  s) SUFFIX="$OPTARG";;
  n) CHECK=1;;
  i) INSTALL=1;;
  w) WORKDIR="$OPTARG";;
  c) SRCDEST_DIR="$OPTARG";;
  o) OUTDIR="$OPTARG";;
  h) sed -n '2,40p' "$0"; exit 0;;
  *) echo "try -h" >&2; exit 2;;
esac; done

[ "$(id -u)" -ne 0 ] || { echo "ERROR: run as a normal user, not root." >&2; exit 2; }

# default patch set: ./patches/*.patch
if [ "${#PATCHES[@]}" -eq 0 ]; then
  shopt -s nullglob; PATCHES=( "$PWD"/patches/*.patch ); shopt -u nullglob
fi
[ "${#PATCHES[@]}" -gt 0 ] || { echo "ERROR: no patches (use -p, or put them in ./patches/)." >&2; exit 2; }

# absolutise patch paths before we cd away
ABS=(); for p in "${PATCHES[@]}"; do
  [ -f "$p" ] || { echo "ERROR: no such patch: $p" >&2; exit 2; }
  ABS+=( "$(readlink -f "$p")" )
done

NEWBASE="${PKG}-${SUFFIX}"
mkdir -p "$WORKDIR" "$SRCDEST_DIR" "$OUTDIR"
# These env vars steer makepkg without editing makepkg.conf, so the script is
# portable to a clean container too. MAKEFLAGS guarantees parallelism even if
# /etc/makepkg.conf leaves it commented out.
export SRCDEST="$SRCDEST_DIR" PKGDEST="$OUTDIR" MAKEFLAGS="-j$(nproc)"

echo "==> base=$BASE  pkgbase=$NEWBASE  patches=( ${ABS[*]##*/} )"

# ---- fresh clone of the Arch packaging repo ----
( cd "$WORKDIR" && rm -rf "$PKG" && pkgctl repo clone --protocol=https "$PKG" >/dev/null )
cd "$WORKDIR/$PKG"

# ---- pin the base version ----
case "$BASE" in
  installed)
    inst="$(pacman -Q "$PKG" | awk '{print $2}')" \
      || { echo "ERROR: $PKG not installed; use -b latest or -b TAG." >&2; exit 2; }
    echo "==> matching installed $PKG = $inst"
    git checkout -q "$inst" 2>/dev/null \
      || { echo "ERROR: tag '$inst' not in $PKG repo (pruned?). Try -b latest." >&2; exit 2; }
    ;;
  latest) echo "==> building upstream HEAD (main)";;
  *) git checkout -q "$BASE" \
       || { echo "ERROR: cannot checkout '$BASE'." >&2; exit 2; }
     echo "==> checked out $BASE";;
esac

# ---- import the source-signing keys (idempotent; shipped in the repo) ----
gpg --quiet --import keys/pgp/*.asc 2>/dev/null || true

# ---- transforms (the manual steps, automated) ----
up_pkgrel="$(grep -Po '^pkgrel=\K.*' PKGBUILD)"
newrel="${up_pkgrel}.$(date +%m%d%H%M)"      # unique + sortable -> distinct uname per build
sed -i "s/^pkgbase=.*/pkgbase=${NEWBASE}/" PKGBUILD
sed -i "s/^pkgrel=.*/pkgrel=${newrel}/"    PKGBUILD

# copy patches in, then inject their filenames into source=() before its ')'
bns=(); for p in "${ABS[@]}"; do cp "$p" .; bns+=( "$(basename "$p")" ); done
awk -v list="$(printf '%s\n' "${bns[@]}")" '
  /^source=\(/ { print; insrc=1; next }
  insrc && /^[[:space:]]*\)/ {
    n = split(list, A, "\n")
    for (i = 1; i <= n; i++) if (A[i] != "") print "  " A[i]
    insrc = 0
  }
  { print }
' PKGBUILD > PKGBUILD.tmp && mv PKGBUILD.tmp PKGBUILD

echo "==> regenerating checksums (updpkgsums) ..."
updpkgsums

echo "==> PKGBUILD now:"; grep -nE '^(pkgbase|pkgver|pkgrel)=' PKGBUILD
echo "==> source[] patch lines:"; grep -nE "$(printf '%s|' "${bns[@]}")XXX_NONE" PKGBUILD || true
echo

run_make() {
  if ! makepkg "$@"; then
    {
      echo
      echo "!! makepkg FAILED."
      echo "!! If it died in prepare() with 'Hunk FAILED' / patch errors, the"
      echo "!! kernel moved and a patch no longer applies to this version."
      echo "!! Rebase the patch against the new source and retry."
    } >&2
    exit 1
  fi
}

if [ "$CHECK" -eq 1 ]; then
  echo "==> CHECK ONLY: download + extract + apply patches, no compile ..."
  run_make -o          # -o/--nobuild runs prepare() (where patches apply), then stops
  echo "==> OK: all patches apply cleanly."
  exit 0
fi

echo "==> building $NEWBASE $newrel  (the ~30-45 min part) ..."
run_make -s

echo "==> packages in $OUTDIR:"
ls -1 "$OUTDIR/$NEWBASE"-*.pkg.tar.zst

if [ "$INSTALL" -eq 1 ]; then
  main="$(ls -t "$OUTDIR/$NEWBASE"-*.pkg.tar.zst | grep -vE -- '-(headers|docs)-' | head -1)"
  echo "==> installing $main  (sudo; reboot afterwards) ..."
  sudo pacman -U "$main"
  echo "==> done. Reboot, pick '$NEWBASE', then: uname -r   (expect ...-${newrel}-zen-${SUFFIX})"
fi
