#!/usr/bin/env python3
"""Runs OUTDIR/command.txt in an interactive bash inside a pseudo-terminal, as
a student typing it into GNOME Terminal would, and answers questions.

    pty-run.py OUTDIR [ANSWER]

Every question that ends in "[Y/n]" or "[y/N]" gets ANSWER (default "y")
followed by Enter. Writes OUTDIR/screen.txt (what the terminal showed) and exits with the
command's exit code (start-bootstrap.sh records it in OUTDIR/rc).
"""
import os
import pty
import re
import select
import sys
import time

out = sys.argv[1]
answer = (sys.argv[2] if len(sys.argv) > 2 else "y").encode() + b"\r"
cmd = open(os.path.join(out, "command.txt")).read().strip()

pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.execvp("bash", ["bash", "-i", "-c", cmd])

buf = b""
answered = 0
pending = b""
while True:
    try:
        r, _, _ = select.select([fd], [], [], 1.0)
    except InterruptedError:
        continue
    if r:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        buf += data
        pending += data
        if re.search(rb"\[(Y/n|y/N)\]\s*$", pending):
            time.sleep(0.3)
            os.write(fd, answer)
            answered += 1
            pending = b""
_, status = os.waitpid(pid, 0)
rc = os.waitstatus_to_exitcode(status)
text = buf.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
with open(os.path.join(out, "screen.txt"), "w") as fh:
    fh.write(text)
    fh.write("\n[pty-run: answered %d question(s) with %r]\n" % (answered, answer.decode().strip()))
sys.exit(rc)
