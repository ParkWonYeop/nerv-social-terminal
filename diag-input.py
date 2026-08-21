#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""한글 입력 진단 — 터미널이 실제로 보내는 바이트를 기록한다.

사용법:
    python3 /opt/rei/diag-input.py

안내에 따라 한글("안녕" 등)을 입력하면, 터미널이 서버로 보낸 원시
바이트가 화면과 /opt/rei/data/input-capture.log 에 기록된다.
1단계(raw)는 에코가 없어서 화면에 글자가 안 보이는 게 정상이다.
"""
import datetime
import os
import sys
import termios
import tty

LOG = "/opt/rei/data/input-capture.log"


def log(f, text):
    f.write(text.encode() if isinstance(text, str) else text)
    f.flush()


def hexdump(b):
    return " ".join(f"{x:02x}" for x in b)


def phase_raw(f):
    print("\n[1/2] raw 캡처 — 지금 '안녕' 을 입력하고 Enter (화면에 안 보여도 정상)")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    chunks = []
    try:
        tty.setraw(fd)
        while True:
            b = os.read(fd, 256)
            chunks.append(b)
            if b"\r" in b or b"\n" in b or b"\x03" in b:
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print()
    log(f, "-- raw chunks --\n")
    for c in chunks:
        line = f"chunk {hexdump(c)}   {c!r}\n"
        print("  " + line, end="")
        log(f, line)


def phase_readline(f):
    print("\n[2/2] readline 입력 — 다시 '안녕' 을 입력하고 Enter")
    try:
        import readline  # noqa: F401
    except ImportError:
        pass
    s = input("  > ")
    line = f"-- readline result -- {s!r}\n"
    print("  결과:", repr(s))
    log(f, line)


def main():
    if not sys.stdin.isatty():
        print("터미널에서 직접 실행해야 한다.")
        return 1
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "ab") as f:
        log(f, f"\n=== {datetime.datetime.now().isoformat()} "
               f"TERM={os.environ.get('TERM', '?')} "
               f"user={os.environ.get('USER', '?')} ===\n")
        phase_raw(f)
        phase_readline(f)
    print(f"\n기록됨: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
