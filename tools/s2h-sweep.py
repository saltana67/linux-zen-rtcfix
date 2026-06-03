#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# s2h-sweep / provoke: characterize and (positive-control) FORCE the
# suspend-then-hibernate wake-detection failure by controlling the sub-second
# phase at which we trigger s2idle suspend.
#
# Modes:
#   (default)                 sweep phase 0..1, map offset vs frac
#   --hunt [--reps K]         find this box's deepest-failing phase, then hammer
#                             it K times to show ~100% failure
#   --phase P --reps K        lock to phase P, repeat K times
#
# Failure == woken_by_timer False (systemd's zero-timeout poll) == would NOT hibernate.
# Run as root at a console; DO NOT touch input while it sleeps:
#   sudo python3 s2h-sweep.py 8 --hunt --reps 12
import ctypes, os, select, sys, time

CLOCK_REALTIME=0; CLOCK_BOOTTIME=7; CLOCK_BOOTTIME_ALARM=9
TFD_CLOEXEC=0o2000000; TFD_NONBLOCK=0o0004000
libc=ctypes.CDLL("libc.so.6", use_errno=True)
class TS(ctypes.Structure):  _fields_=[("s",ctypes.c_int64),("ns",ctypes.c_int64)]
class ITS(ctypes.Structure): _fields_=[("iv",TS),("val",TS)]
def gett(clk):
    t=TS()
    if libc.clock_gettime(clk, ctypes.byref(t))!=0: raise OSError(ctypes.get_errno(),"clock_gettime")
    return t.s + t.ns/1e9
def wait_phase(frac):
    while True:
        d=(frac-(gett(CLOCK_REALTIME)%1.0))%1.0
        if d<0.004: return
        if d>0.02: time.sleep(min(d-0.015,0.05))
def one_cycle(N, state, phase, dry):
    wait_phase(phase)
    tfd=libc.timerfd_create(CLOCK_BOOTTIME_ALARM, TFD_CLOEXEC|TFD_NONBLOCK)
    if tfd<0: print("timerfd_create:", os.strerror(ctypes.get_errno()),"(need root)"); sys.exit(1)
    libc.timerfd_settime(tfd,0,ctypes.byref(ITS(TS(0,0),TS(N,0))),None)
    frac=gett(CLOCK_REALTIME)%1.0
    t0=gett(CLOCK_BOOTTIME)
    if dry: time.sleep(0.3)
    else:
        with open("/sys/power/state","w") as f: f.write(state+"\n")
    t1=gett(CLOCK_BOOTTIME)
    p=select.poll(); p.register(tfd, select.POLLIN)
    wbt=bool(p.poll(0))
    app=None
    if not wbt:
        w=0.0
        while w<5.0:
            if p.poll(50): app=w+0.05; break
            w+=0.05
    try: os.read(tfd,8)
    except BlockingIOError: pass
    os.close(tfd)
    return frac, t1-t0, wbt, app

def line(tag, frac, slept, N, wbt, app):
    late=(f"{app:.2f}s" if app is not None else ("-" if wbt else ">5s"))
    print(f"{tag} frac={frac:6.3f} slept={slept:7.3f} offset={slept-N:+7.3f} woken_by_timer={str(wbt):>5} POLLIN_after={late:>6}")
    sys.stdout.flush()

def hunt(N,state,dry):
    print("# hunting deepest-failing phase in [0.70,0.94] ...")
    best=None; ph=0.70
    while ph<=0.9401:
        frac,slept,wbt,app=one_cycle(N,state,ph,dry); line(f"  phase={ph:.2f}",frac,slept,N,wbt,app)
        if not wbt and (best is None or (slept-N)<best[1]): best=(ph,slept-N)
        ph+=0.03
    return best

def reps_at(N,state,phase,reps,dry):
    fails=0
    for r in range(reps):
        frac,slept,wbt,app=one_cycle(N,state,phase,dry); line(f"  rep {r+1:2d}",frac,slept,N,wbt,app)
        if not wbt: fails+=1
    return fails

def main():
    N=8; steps=12; reps=12; state="mem"; dry=False; mode="sweep"; phase=None
    a=sys.argv[1:]; i=0
    while i<len(a):
        if a[i]=="--dry-run": dry=True
        elif a[i]=="--hunt": mode="hunt"
        elif a[i]=="--phase": i+=1; phase=float(a[i]); mode="phase"
        elif a[i]=="--reps": i+=1; reps=int(a[i])
        elif a[i]=="--steps": i+=1; steps=int(a[i])
        elif a[i]=="--state": i+=1; state=a[i]
        elif a[i].isdigit(): N=int(a[i])
        i+=1
    print(f"# delay={N}s state={state!r} mode={mode}  (offset=slept-{N}; negative & False = failure)")
    if mode=="hunt":
        best=hunt(N,state,dry)
        if not best: print("# no failing phase found in band -> mechanism may not bite this box"); return
        print(f"# deepest-failing phase = {best[0]:.2f} (offset {best[1]:+.3f}); hammering it {reps}x ...")
        f=reps_at(N,state,best[0],reps,dry)
        print(f"# RESULT: {f}/{reps} cycles failed to hibernate at the worst phase")
    elif mode=="phase":
        f=reps_at(N,state,phase,reps,dry)
        print(f"# RESULT: {f}/{reps} cycles failed to hibernate at phase {phase:.2f}")
    else:
        fails=0
        for k in range(steps):
            frac,slept,wbt,app=one_cycle(N,state,k/steps,dry); line(f"phase={k/steps:.2f}",frac,slept,N,wbt,app)
            if not wbt: fails+=1
        print(f"# {fails}/{steps} failed")

if __name__=="__main__": main()
