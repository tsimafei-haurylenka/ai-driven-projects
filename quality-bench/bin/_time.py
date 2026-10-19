import sys, time
# usage: _time.py <test> <start_epoch>  (container) -> /work/time_<test>.txt
test, t0 = sys.argv[1], float(sys.argv[2])
elapsed = time.time() - t0
open(f"/work/time_{test}.txt", "w").write(f"{elapsed:.1f}")
print(f"{test}: {elapsed:.1f}s")
