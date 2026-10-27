"""Detached login worker; parent passes a held flock descriptor."""
import json
import os
import subprocess
import sys
from pathlib import Path
from . import actions


def main():
    lock_fd = int(sys.argv[1])
    state = {"state": "failed", "message": "Run abstract-gpt login in a terminal for diagnostics."}
    try:
        with open(actions.root() / "login-output.txt", "w") as output:
            process = subprocess.Popen([actions.binary(), "login", "--device-auth"],
                                       env=actions.env(), stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, cwd=Path.home())
            try:
                code = process.wait(timeout=900)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                state = {"state": "expired"}
            else:
                if code == 0:
                    state = {"state": "authenticated"}
    finally:
        actions.write(actions.root() / "login.json", json.dumps(state))
        if state["state"] == "authenticated":
            (actions.root() / "login-output.txt").unlink(missing_ok=True)
        os.close(lock_fd)


if __name__ == "__main__":
    main()
