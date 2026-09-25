#!/usr/bin/env python3
"""Crash-protected launcher for the QCL / URG bot."""

from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(HERE, "QCL2K.py")
LAUNCHER = os.path.join(HERE, "prefix_only.py")
LAST_GOOD = os.path.join(HERE, "QCL2K.last_good.py")


def compile_ok(path: str) -> tuple[bool, str]:
    try:
        py_compile.compile(path, doraise=True)
        py_compile.compile(os.path.join(HERE, "prefix_gateway.py"), doraise=True)
        py_compile.compile(os.path.join(HERE, "qcl_admin.py"), doraise=True)
        py_compile.compile(LAUNCHER, doraise=True)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def select_source() -> tuple[str | None, str | None]:
    good, error = compile_ok(BOT)
    if good:
        shutil.copy2(BOT, LAST_GOOD)
        return BOT, None
    if os.path.exists(LAST_GOOD):
        return LAST_GOOD, error
    return None, error


def main() -> None:
    print("QCL / URG supervisor")
    crashes: list[float] = []
    while True:
        target, warning = select_source()
        if target is None:
            print(f"[supervisor] Source does not compile: {warning}")
            time.sleep(5)
            continue
        if warning:
            print(f"[supervisor] Current source failed validation; using last good: {warning}")
        started = time.time()
        try:
            code = subprocess.run(
                [sys.executable, LAUNCHER],
                cwd=HERE,
                env={**os.environ, "QCL_SOURCE": target},
            ).returncode
        except KeyboardInterrupt:
            return
        elapsed = int(time.time() - started)
        print(f"[supervisor] Bot exited with {code} after {elapsed}s")
        now = time.time()
        crashes = [stamp for stamp in crashes if now - stamp < 60] + [now]
        time.sleep(30 if len(crashes) >= 5 else 5)


if __name__ == "__main__":
    main()