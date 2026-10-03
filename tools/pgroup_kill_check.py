"""s8 spike: does killing the process group end a host agent's children?

host replacement for `docker rm -f`: start the agent with start_new_session=True, stop with
os.killpg(pid, SIGTERM). a plain terminate() only reaches the direct child.
"""

import os
import signal
import subprocess
import time


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


for label, new_session in (("plain terminate", False), ("killpg", True)):
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 301 & sleep 302 & wait"], start_new_session=new_session
    )
    time.sleep(0.5)
    out = subprocess.run(
        ["pgrep", "-P", str(proc.pid)], capture_output=True, text=True, check=False
    )
    kids = [int(pid) for pid in out.stdout.split()]
    if new_session:
        os.killpg(proc.pid, signal.SIGTERM)
    else:
        proc.terminate()
    proc.wait()
    time.sleep(0.3)
    print(label, "children", kids, "alive after:", [k for k in kids if alive(k)])
    for kid in kids:
        if alive(kid):
            os.kill(kid, signal.SIGKILL)
