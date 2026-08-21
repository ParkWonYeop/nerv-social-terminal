# -*- coding: utf-8 -*-
"""키 입력 시험 — 의사 터미널(pty)로 실제 방향키 바이트를 흘려 넣는다.

    python3 tests/keys.py

이게 필요한 이유: 방향키는 TTY 에서만 재현된다. 파이프로는 줄 입력
경로만 지나가서, 화살표를 눌렀을 때 튕기던 버그를 전혀 못 잡았다.

pty 를 열어 자식 프로세스의 stdin 에 물리면 실제 터미널처럼 동작한다.
"""
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PASS, FAIL = [], []

# 터미널이 실제로 보내는 바이트열
ESC_SEQ = {
    "up": b"\x1b[A", "down": b"\x1b[B",
    "right": b"\x1b[C", "left": b"\x1b[D",
    "up_app": b"\x1bOA", "down_app": b"\x1bOB",   # 애플리케이션 커서 모드
    "home": b"\x1b[H", "end": b"\x1b[F",
    "pgup": b"\x1b[5~", "pgdn": b"\x1b[6~",
    "enter": b"\r", "enter_nl": b"\n", "tab": b"\t",
    "esc": b"\x1b", "backspace": b"\x7f",
}


def drive(script: str, feed: bytes, *, wait=1.5) -> str:
    """pty 안에서 script 를 돌리고 feed 를 흘려 넣은 뒤 출력을 돌려준다."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=slave, stdout=slave, stderr=subprocess.PIPE,
        cwd=str(ROOT), close_fds=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"})
    os.close(slave)
    time.sleep(0.35)                     # 자식이 raw 모드에 들어갈 틈
    try:
        os.write(master, feed)
    except OSError:
        pass

    out = b""
    deadline = time.time() + wait
    while time.time() < deadline:
        import select
        if select.select([master], [], [], 0.1)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if proc.poll() is not None and not select.select([master], [], [], 0.05)[0]:
            break
    try:
        proc.kill()
    except OSError:
        pass
    proc.wait(timeout=3)
    os.close(master)
    return out.decode("utf-8", "replace")


READER = """
import sys
sys.path.insert(0, {root!r})
from nervterm import term
got = []
for _ in range({n}):
    k = term.read_key(timeout=2.0)
    if k is None:
        break
    got.append(k)
print("RESULT:" + "|".join(got))
"""


def check(name, fn):
    try:
        fn()
    except Exception as exc:                                  # noqa: BLE001
        import traceback
        FAIL.append((name, f"{type(exc).__name__}: {exc}",
                     traceback.format_exc()))
    else:
        PASS.append(name)


def keys_from(feed: bytes, n: int):
    out = drive(READER.format(root=str(ROOT), n=n), feed)
    for line in out.splitlines():
        if line.startswith("RESULT:"):
            body = line[len("RESULT:"):].strip()
            return body.split("|") if body else []
    raise AssertionError(f"결과를 못 받았다. 출력:\n{out[:600]}")


def eq(got, want, what=""):
    if got != want:
        raise AssertionError(f"{what}: {got!r} != {want!r}")


# ═══════════════════════════════════════════════════════════════════════
def t_arrows():
    got = keys_from(ESC_SEQ["up"] + ESC_SEQ["down"] +
                    ESC_SEQ["right"] + ESC_SEQ["left"], 4)
    eq(got, ["up", "down", "right", "left"], "방향키 4종")


def t_arrows_app_mode():
    """일부 터미널은 \\x1bOA 를 보낸다."""
    got = keys_from(ESC_SEQ["up_app"] + ESC_SEQ["down_app"], 2)
    eq(got, ["up", "down"], "애플리케이션 커서 모드")


def t_arrow_burst():
    """한 번에 몰아서 보내도 하나씩 정확히 끊어 읽는가.

    파이썬 버퍼가 3바이트를 통째로 삼키고 select 가 fd 만 보던 버그가
    바로 이 경우에 터졌다.
    """
    feed = ESC_SEQ["up"] * 3 + ESC_SEQ["down"] * 2
    got = keys_from(feed, 5)
    eq(got, ["up", "up", "up", "down", "down"], "연타")


def t_enter_and_esc():
    got = keys_from(ESC_SEQ["enter"] + ESC_SEQ["enter_nl"] +
                    ESC_SEQ["tab"] + ESC_SEQ["backspace"], 4)
    eq(got, ["enter", "enter", "tab", "backspace"], "엔터·탭·백스페이스")


def t_lone_esc():
    """ESC 단독은 ESC 여야 한다 — 방향키로 오인하면 안 된다."""
    got = keys_from(ESC_SEQ["esc"], 1)
    eq(got, ["esc"], "ESC 단독")


def t_nav_keys():
    got = keys_from(ESC_SEQ["home"] + ESC_SEQ["end"] +
                    ESC_SEQ["pgup"] + ESC_SEQ["pgdn"], 4)
    eq(got, ["home", "end", "pgup", "pgdn"], "Home/End/PgUp/PgDn")


def t_plain_chars():
    got = keys_from(b"jkq1", 4)
    eq(got, ["j", "k", "q", "1"], "일반 글자")


def t_hangul():
    """한글은 여러 바이트다. 조각으로 끊기면 안 된다."""
    got = keys_from("가".encode("utf-8"), 1)
    eq(got, ["가"], "한글 한 글자")


def t_mixed():
    """방향키와 글자를 섞어도 순서가 유지되는가."""
    got = keys_from(ESC_SEQ["up"] + b"j" + ESC_SEQ["down"] +
                    ESC_SEQ["enter"], 4)
    eq(got, ["up", "j", "down", "enter"], "혼합")


CHOOSER = """
import sys
sys.path.insert(0, {root!r})
from nervterm.ui.base import BaseUI
from nervterm.ui import view as V
ui = BaseUI()
items = [V.MenuItem(key=str(i), label="항목 %d" % i) for i in range(1, 6)]
got = ui.choose(V.MenuView(title="시험", items=items))
print("CHOSE:" + (got.key if got else "None"))
"""

TYPED = """
import sys
sys.path.insert(0, {root!r})
from nervterm.ui.base import BaseUI
from nervterm.ui import view as V
ui = BaseUI()
items = [V.MenuItem(key="a", label="가만히 있는다"),
         V.MenuItem(key="type", label="직접 입력…", input_mode=True)]
got = ui.choose(V.MenuView(title="시험", items=items))
print("TYPED:" + (got.typed if got else "None"))
"""


def chose(feed: bytes, script=CHOOSER, mark="CHOSE:"):
    out = drive(script.format(root=str(ROOT)), feed, wait=2.5)
    for line in out.splitlines():
        if mark in line:
            return line.split(mark, 1)[1].strip()
    raise AssertionError(f"결과를 못 받았다. 출력 끝:\n{out[-700:]}")


def t_chooser_moves():
    """아래 두 번 + Enter → 3번 항목."""
    eq(chose(ESC_SEQ["down"] * 2 + ESC_SEQ["enter"]), "3", "아래 2회")


def t_chooser_wraps():
    """맨 위에서 위로 가면 맨 아래로 돈다."""
    eq(chose(ESC_SEQ["up"] + ESC_SEQ["enter"]), "5", "위로 감기")


def t_chooser_esc_cancels():
    eq(chose(ESC_SEQ["esc"]), "None", "ESC 취소")


def t_chooser_does_not_exit_on_arrow():
    """화살표만 눌렀을 때 선택이 끝나 버리면 안 된다 — 바로 그 버그였다."""
    out = drive(CHOOSER.format(root=str(ROOT)),
                ESC_SEQ["up"] + ESC_SEQ["down"], wait=1.2)
    if "CHOSE:" in out:
        raise AssertionError("화살표만 눌렀는데 선택이 끝났다(튕김)")


def t_chooser_typed_input():
    """직접 입력 항목 → 글자 입력 → Enter 로 전송."""
    feed = ESC_SEQ["down"] + ESC_SEQ["enter"] + "옆에 앉는다".encode() + b"\r"
    eq(chose(feed, TYPED, "TYPED:"), "옆에 앉는다", "직접 입력")


TESTS = [
    ("방향키 4종", t_arrows),
    ("애플리케이션 커서 모드", t_arrows_app_mode),
    ("방향키 연타 — 버퍼 경계", t_arrow_burst),
    ("엔터·탭·백스페이스", t_enter_and_esc),
    ("ESC 단독", t_lone_esc),
    ("Home/End/PgUp/PgDn", t_nav_keys),
    ("일반 글자", t_plain_chars),
    ("한글 멀티바이트", t_hangul),
    ("방향키와 글자 혼합", t_mixed),
    ("선택기 — 커서 이동", t_chooser_moves),
    ("선택기 — 위아래 감김", t_chooser_wraps),
    ("선택기 — ESC 취소", t_chooser_esc_cancels),
    ("선택기 — 화살표에 튕기지 않음", t_chooser_does_not_exit_on_arrow),
    ("선택기 — 직접 입력", t_chooser_typed_input),
]


def main() -> int:
    for name, fn in TESTS:
        check(name, fn)
    for name in PASS:
        print(f"  \033[32m✓\033[0m {name}")
    for name, why, tb in FAIL:
        print(f"  \033[31m✗\033[0m {name}")
        print(f"      {why}")
    print()
    print(f"{len(PASS)} 통과, {len(FAIL)} 실패")
    if FAIL and os.environ.get("NERV_DEBUG"):
        for name, _, tb in FAIL:
            print(f"\n=== {name} ===\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
