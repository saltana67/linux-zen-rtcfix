#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# s2h-probe: reproduce systemd-sleep's suspend-then-hibernate wake-detection
# to confirm whether the CLOCK_BOOTTIME_ALARM timerfd is "fired" at the instant
# systemd polls it (zero timeout) after an s2idle resume.
#
# Mirrors systemd-sleep execute_s2h/custom_timer_suspend internals:
#   timerfd_create(CLOCK_BOOTTIME_ALARM) -> arm for N s -> write SuspendState to
#   /sys/power/state (s2idle) -> on resume, fd_wait_for_event(tfd, POLLIN, 0).
# Then it ALSO keeps polling to measure how long after resume POLLIN appears.
#
# Run as root at a console:  sudo python3 s2h-probe.py [seconds] [--state mem|freeze]
# Debug without suspending:  python3 s2h-probe.py 5 --dry-run
import ctypes, os, select, struct, sys, time

CLOCK_BOOTTIME       = 7
CLOCK_BOOTTIME_ALARM = 9
TFD_CLOEXEC          = 0o2000000
TFD_NONBLOCK         = 0o0004000

libc = ctypes.CDLL("libc.so.6", use_errno=True)

class timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_int64)]
class itimerspec(ctypes.Structure):
    _fields_ = [("it_interval", timespec), ("it_value", timespec)]

def boottime():
    ts = timespec()
    if libc.clock_gettime(CLOCK_BOOTTIME, ctypes.byref(ts)) != 0:
        raise OSError(ctypes.get_errno(), "clock_gettime")
    return ts.tv_sec + ts.tv_nsec / 1e9

def main():
    secs = 120
    state = "mem"
    dry = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--dry-run": dry = True
        elif a == "--state": i += 1; state = args[i]
        elif a.isdigit(): secs = int(a)
        else: print("usage: s2h-probe.py [seconds] [--state mem|freeze] [--dry-run]"); return 2
        i += 1

    tfd = libc.timerfd_create(CLOCK_BOOTTIME_ALARM, TFD_CLOEXEC | TFD_NONBLOCK)
    if tfd < 0:
        e = ctypes.get_errno(); print(f"timerfd_create failed: {os.strerror(e)} (need root / CAP_WAKE_ALARM)"); return 1

    spec = itimerspec(timespec(0, 0), timespec(secs, 0))
    if libc.timerfd_settime(tfd, 0, ctypes.byref(spec), None) != 0:
        e = ctypes.get_errno(); print(f"timerfd_settime failed: {os.strerror(e)}"); return 1

    print(f"[*] armed CLOCK_BOOTTIME_ALARM timerfd for {secs}s")
    t0 = boottime()
    print(f"[*] boottime before suspend: {t0:.3f}")
    print(f"[*] {'(dry-run: sleeping, NOT suspending)' if dry else f'writing {state!r} to /sys/power/state (s2idle suspend) ...'}")
    sys.stdout.flush()

    if dry:
        time.sleep(2)            # pretend; in real runs the box is asleep here
    else:
        with open("/sys/power/state", "w") as f:
            f.write(state + "\n")   # blocks until resume, exactly like systemd-sleep

    t1 = boottime()              # resumed
    elapsed = t1 - t0

    # THE systemd check: zero-timeout poll
    p = select.poll(); p.register(tfd, select.POLLIN)
    woken_by_timer = bool(p.poll(0))

    # how long after resume does POLLIN actually appear? (poll in small steps)
    appeared = None
    if not woken_by_timer:
        waited = 0.0
        while waited < 5.0:
            if p.poll(50):       # 50 ms steps
                appeared = waited + 0.05; break
            waited += 0.05

    # drain the expiration counter
    try:    expirations = struct.unpack("Q", os.read(tfd, 8))[0]
    except BlockingIOError: expirations = 0
    os.close(tfd)

    print(f"[*] boottime after resume:  {t1:.3f}")
    print(f"[=] slept (boottime elapsed): {elapsed:.3f}s   (armed for {secs}s)")
    early = secs - elapsed
    print(f"[=] woke EARLY by:            {early:+.3f}s   (positive = woke before timerfd expiry)")
    print(f"[=] woken_by_timer @ poll(0): {woken_by_timer}   <-- this is systemd's decision")
    if not woken_by_timer:
        print(f"[=] POLLIN appeared after:    {appeared if appeared is not None else '>5'}s post-resume")
    print(f"[=] timerfd expirations:      {expirations}")
    print()
    if not woken_by_timer and (appeared is not None or expirations >= 1 or early > 0):
        print("[!] REPRODUCED: timer was the wake cause but POLLIN was NOT set at the")
        print("    zero-timeout poll -> systemd would treat this as a manual wake and NOT hibernate.")
    elif woken_by_timer:
        print("[ok] timerfd was already fired at poll time -> systemd would hibernate this cycle.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
