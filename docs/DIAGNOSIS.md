# Diagnosis: intermittent suspend-then-hibernate that never hibernates

## Symptom

On suspend-then-hibernate the machine drops to s2idle, wakes at the configured
`HibernateDelaySec`, and then — intermittently, ~1 cycle in 4 on the reference
machine — simply resumes instead of hibernating. The failure is not tied to
load or uptime; it depends on the sub-second moment at which suspend is
triggered, which is what makes it look random.

## The systemd side

KDE → logind `SuspendThenHibernate` → `systemd-sleep`. On a machine with **no
battery**, `battery_trip_point_alarm_exists()` returns false, so `systemd-sleep`
takes the `custom_timer_suspend()` path: it arms a `CLOCK_BOOTTIME_ALARM`
timerfd for `HibernateDelaySec`, writes `mem` to `/sys/power/state` (s2idle),
and on resume decides what woke it with a single **zero-timeout** poll:

```
fd_wait_for_event(tfd, POLLIN, 0)
```

If `POLLIN` is set, the timer fired → proceed to hibernate. If not, systemd
assumes a user wake and stays up. There is no tolerance and no second look.

## The kernel side (root cause)

`kernel/time/alarmtimer.c::alarmtimer_suspend()` programs the wakeup alarm from
the current RTC time:

```c
rtc_read_time(rtc, &tm);
now = rtc_tm_to_ktime(tm);
...
now = ktime_add(now, min);
rtc_timer_start(rtc, &rtctimer, now, 0);
```

`rtc_read_time()` has one-second resolution, so `rtc_tm_to_ktime()` **floors**
the current time and discards its sub-second fraction. `rtc_timer_start()` later
converts the alarm back with `rtc_ktime_to_tm()`, which **rounds any non-zero
nanosecond part up** to the next whole second. That round-up compensates for the
fractional part of `min`, but *not* for the fraction already dropped from `now`.
The programmed alarm is therefore:

```
alarm = ceil( floor(now) + min )
```

which is up to one second earlier than the requested expiry whenever:

```
frac(now) + frac(min) > 1
```

## Why "early" breaks the poll

The RTC hardware alarm (which actually wakes the box) is the floored/ceiled one;
the `CLOCK_BOOTTIME_ALARM` timerfd systemd is watching tracks the *true*
deadline. When the RTC fires early, the CPU resumes and systemd polls — but the
boottime deadline the timerfd represents has not been reached yet, so `POLLIN`
is **not** set at the zero-timeout poll. `woken_by_timer = false` → systemd
abandons hibernation. The wake really *was* the timer; it was just classified as
spurious because it arrived a hair early.

## Empirical model

Driving suspend at a controlled sub-second phase (`tools/s2h-sweep.py`) and
measuring `offset = slept − N`:

```
offset ≈ (L_suspend + L_resume) − frac(now)
slope vs frac(now)   ≈ −1.00
fixed-latency intercept ≈ +0.77 s
crossover (offset turns negative) at frac ≈ 0.76
```

So once `frac(now)` exceeds ~0.76 the early-fire outruns the fixed
suspend/resume latency, the wake is net-early, and the poll misses — matching
the observed ~24 % failure rate (roughly the slice of each second above the
crossover).

## The fix

Recover the discarded fraction from the system clock and fold it back into
`now`, so the alarm is derived from the true current time. Inserted right after
`now = rtc_tm_to_ktime(tm);`:

```c
{
        u64 now_ns = ktime_get_real_ns();

        now = ktime_add_ns(now, do_div(now_ns, NSEC_PER_SEC));
}
```

`do_div(now_ns, NSEC_PER_SEC)` returns the remainder — the sub-second
nanoseconds — which is added back to `now`. Only the *fraction* of
`CLOCK_REALTIME` is used, never its absolute value, so the result is unaffected
by any skew between the system clock and the RTC. With the true fraction
restored, the existing `rtc_ktime_to_tm()` round-up guarantees the alarm is
**never** programmed earlier than the requested expiry — at worst it fires up to
one second *late*, which is harmless against a minutes-scale hibernate delay.

Full patch: `patches/0001-alarmtimer-preserve-sub-second-offset.patch`.

## Evidence (single-variable A/B)

Two kernels built from the same `linux-zen` base, byte-identical config,
differing *only* by this patch:

| build               | worst phase (0.88), 12 reps | offset        | woken_by_timer |
|---------------------|-----------------------------|---------------|----------------|
| control (unpatched) | **12 / 12 failed**          | −0.10 … −0.13 | False          |
| patched             | **0 / 12 failed**           | +0.86 … +0.90 | True           |

`--hunt` on the patched kernel reports *no failing phase in the band* — the whole
failure window is gone. Confirmed end-to-end under real KDE suspend-then-hibernate
at a 3-minute delay: s2idle standby for 3:01, timer wake, hibernation entry, S4,
resume.

## Open refinements (for upstream)

- The fix is a small nested block after the existing read; a maintainer may
  prefer to hoist the declaration. `checkpatch.pl --strict` and review will
  settle the style.
- The RTC read and the system-clock read are not atomic, so a second-boundary
  crossing between the two could mis-add ~1 s. In practice that can only ever err
  *late*, never early, so it does not reintroduce the bug — but it's worth a note
  in the changelog when submitting.

## References

- `kernel/time/alarmtimer.c` — original author John Stultz.
- systemd `src/sleep/sleep.c` — `custom_timer_suspend()` and the no-battery path.
- Related systemd reports: issues #35743 and #24279.
