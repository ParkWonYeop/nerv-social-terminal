# -*- coding: utf-8 -*-
"""터미널 배관 — 어떤 UI 플러그인을 쓰든 똑같이 필요한 것들.

여기 있는 것은 '보이는 방식'이 아니라 '터미널을 다루는 방식'이다.
줄 편집, 한글 폭 계산, 에코 제어, 콘솔 핸들. UI 플러그인이 바뀌어도
이 층은 바뀌지 않는다.

색·레이아웃·애니메이션은 전부 UI 플러그인 쪽(nervterm/ui/)에 있다.
"""
import os
import sys
import time
import unicodedata
from contextlib import contextmanager

# 줄 편집(백스페이스·화살표·히스토리)을 켠다. 이게 없으면 화살표 키가
# 이스케이프 문자를 찍고, 백스페이스가 프롬프트까지 지운다.
#
# 단, readline의 화면 갱신이 일부 터미널(특히 Windows 콘솔 계열)의
# 한글 IME 조합 표시와 충돌해 조합 중인 글자가 지워져 보일 수 있다.
# 그런 터미널에서는 NERV_PLAIN_INPUT=1 로 readline 없이 실행한다.
if (os.environ.get("NERV_PLAIN_INPUT") or
        os.environ.get("REI_PLAIN_INPUT", "")) not in ("", "0"):
    readline = None
else:
    try:
        import readline  # noqa: F401
    except ImportError:
        readline = None
    else:
        # readline은 프롬프트마다 bracketed paste 모드(\x1b[?2004h/l)를
        # 켰다 끈다. 이 토글이 IME 조합 표시를 깨는 터미널이 있고,
        # 이 게임에는 어차피 필요 없는 기능이라 끈다.
        readline.parse_and_bind("set enable-bracketed-paste off")

from rich.console import Console
from rich.text import Text

console = Console(highlight=False)


def width(text: str) -> int:
    """터미널 표시 폭. 한글·한자는 2칸."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


def pad(text: str, n: int) -> str:
    """표시 폭 기준 좌측 정렬 패딩."""
    return text + " " * max(0, n - width(text))


def truncate(text: str, n: int, ellipsis: str = "…") -> str:
    """표시 폭 기준으로 자른다. 잘렸으면 말줄임표를 붙인다."""
    if width(text) <= n:
        return text
    room = max(0, n - width(ellipsis))
    out, used = [], 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + w > room:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def read_input(prompt="  > ", rgb=(201, 138, 43)):
    """편집해도 프롬프트가 지워지지 않는 입력.

    readline 이 프롬프트 폭을 셀 수 있도록 ANSI 색을 \001..\002 로
    감싼다. 이걸 안 하면 긴 입력·백스페이스에서 커서 위치가 어긋난다.
    readline 이 없으면(NERV_PLAIN_INPUT=1) 프롬프트를 직접 찍고
    커널 줄 편집으로 읽는다 — 예전 방식.
    """
    if not is_tty():
        return input(prompt)
    r, g, b = rgb
    color, reset = f"\x1b[1;38;2;{r};{g};{b}m", "\x1b[0m"
    if readline is None:
        # 커널 줄 편집은 IUTF8 이 없으면 백스페이스가 한글을 바이트
        # 단위로 깨서 UnicodeDecodeError 를 낸다. 켜 주고, 그래도
        # 깨진 입력이 오면 빈 줄로 처리한다.
        _ensure_iutf8()
        sys.stdout.write(color + prompt + reset)
        sys.stdout.flush()
        try:
            return input()
        except UnicodeDecodeError:
            return ""
    _prefill()
    try:
        return input(f"\001{color}\002{prompt}\001{reset}\002")
    finally:
        if readline is not None:
            readline.set_startup_hook(None)


# ── 미리 친 글자 ───────────────────────────────────────────────────────
#
# 연출(부팅·타이핑)을 아무 키로 건너뛸 수 있게 하면, 그 키가 답장의 첫
# 글자였을 수도 있다. 버리지 않고 모아 뒀다가 다음 입력 줄에 미리 채운다.
_pending = []


def stash(text: str) -> None:
    """건너뛰기에 쓰인 키가 글자였다 — 다음 입력 줄에 넣어 준다."""
    if text and text.isprintable():
        _pending.append(text)


def _prefill() -> None:
    if not _pending or readline is None:
        _pending.clear()
        return
    text = "".join(_pending)
    _pending.clear()
    readline.set_startup_hook(lambda: readline.insert_text(text))


# ── Tab 자동완성 ───────────────────────────────────────────────────────
_completer = None


def set_completer(fn) -> None:
    """fn(지금까지 친 줄) -> [후보]. 줄 전체를 받아 마지막 낱말의 후보를 준다.

    readline 이 없으면 아무것도 안 한다.
    """
    global _completer
    _completer = fn
    if readline is None:
        return
    readline.set_completer_delims(" ")
    readline.set_completer(_complete if fn else None)
    # macOS 의 기본 readline 은 libedit 이라 문법이 다르다
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def _complete(text, state):
    try:
        line = readline.get_line_buffer()
        options = [c for c in (_completer(line) or []) if c.startswith(text)]
    except Exception:                                         # noqa: BLE001
        return None
    return options[state] if state < len(options) else None


def _ensure_iutf8():
    try:
        import termios
        iutf8 = getattr(termios, "IUTF8", 0x4000)  # 리눅스 값
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
        if not attrs[0] & iutf8:
            attrs[0] |= iutf8
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except Exception:
        pass


@contextmanager
def echo_off():
    """대기·연출 중 키 에코를 끈다.

    스피너(rich Live)가 줄을 계속 다시 그리는 동안 사용자가 미리
    타이핑하면, 에코된 글자가 반쯤 찍혔다 지워지기를 반복해서
    '글자가 지워진다'고 느끼게 된다. 에코만 끄면 입력은 버퍼에
    남아 있다가 프롬프트가 뜰 때 온전히 나타난다.
    """
    if not sys.stdin.isatty():
        yield
        return
    try:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
    except Exception:
        yield
        return
    try:
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


def type_inline(text, style, delay):
    """줄바꿈 없이 제자리에서 한 글자씩. **아무 키나** 누르면 남은 글자를
    한 번에 찍는다(누른 키가 글자면 다음 입력 줄에 넣어 준다)."""
    if delay <= 0 or not sys.stdout.isatty():
        console.print(Text(text, style=style), end="")
        return
    done = 0
    with cbreak():
        try:
            for done, ch in enumerate(text, 1):
                console.print(Text(ch, style=style), end="")
                if skip_pressed(delay):
                    console.print(Text(text[done:], style=style), end="")
                    return
        except KeyboardInterrupt:
            console.print(Text(text[done:], style=style), end="")


@contextmanager
def cbreak():
    """키 하나하나를 바로 읽을 수 있게(에코 없이). 연출을 건너뛸 때 쓴다.

    줄 단위(canonical) 모드에서는 엔터를 치기 전까지 키가 안 보인다.
    Ctrl+C 는 그대로 신호로 간다.
    """
    if not is_tty():
        yield
        return
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd, termios.TCSANOW)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, new)
    except Exception:                                         # noqa: BLE001
        yield
        return
    try:
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:                                     # noqa: BLE001
            pass


def skip_pressed(wait: float = 0.0) -> bool:
    """wait 초 안에 키가 눌렸는가. 눌렸으면 그 키를 먹고 True.

    cbreak() 안에서 쓴다. 글자 키면 다음 입력 줄로 넘긴다(stash).
    """
    if not is_tty():
        if wait:
            time.sleep(wait)
        return False
    fd = sys.stdin.fileno()
    if not _waiting(fd, wait):
        return False
    try:
        raw = os.read(fd, 64)
    except OSError:
        return True
    text = raw.decode("utf-8", "ignore")
    if text and not text.startswith("\x1b") and text not in ("\r", "\n"):
        stash(text)
    return True


def pause(seconds: float) -> bool:
    """연출용 대기. 아무 키나 누르면 바로 True (나머지 연출을 건너뛴다)."""
    return skip_pressed(seconds)


def cursor_up(n: int) -> None:
    sys.stdout.write(f"\x1b[{n}A\r")
    sys.stdout.flush()


def cursor_down(n: int) -> None:
    sys.stdout.write(f"\x1b[{n}B\r")
    sys.stdout.flush()


def ask_line(prompt, *, rgb=(111, 119, 131), default=""):
    """한 줄 입력. 취소(EOF/Ctrl+C)면 None."""
    try:
        raw = read_input(prompt, rgb=rgb).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    return raw or default


# ── 한 키 읽기 ─────────────────────────────────────────────────────────
#
# 목록에서 화살표로 고르려면 줄 단위가 아니라 키 단위로 읽어야 한다.
# 읽는 동안만 raw 모드로 바꾸고 곧바로 되돌린다 — 계속 raw 로 두면
# Ctrl+C 나 화면 갱신이 이상해진다.
KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = "up", "down", "left", "right"
KEY_ENTER, KEY_ESC, KEY_TAB = "enter", "esc", "tab"
KEY_HOME, KEY_END, KEY_PGUP, KEY_PGDN = "home", "end", "pgup", "pgdn"
KEY_BACKSPACE = "backspace"

_CSI = {
    "A": KEY_UP, "B": KEY_DOWN, "C": KEY_RIGHT, "D": KEY_LEFT,
    "H": KEY_HOME, "F": KEY_END,
}
_CSI_TILDE = {"1": KEY_HOME, "4": KEY_END, "5": KEY_PGUP, "6": KEY_PGDN,
              "7": KEY_HOME, "8": KEY_END}


def _waiting(fd, timeout) -> bool:
    import select
    try:
        return bool(select.select([fd], [], [], timeout)[0])
    except (OSError, ValueError):
        return False


def read_key(timeout=None):
    """키 하나. 방향키·엔터·ESC 는 이름으로, 나머지는 글자 그대로.

    TTY 가 아니면 None — 호출부가 줄 입력으로 떨어진다.
    Ctrl+C 는 KeyboardInterrupt 로 올린다(raw 모드에서는 신호가 안 온다).

    **os.read 로 파일 디스크립터에서 직접 읽는다.** sys.stdin.read(1) 을
    쓰면 안 된다 — 파이썬의 버퍼가 방향키 3바이트(\\x1b[A)를 통째로
    당겨 놓고 \\x1b 만 돌려주는데, 그 다음 select 는 fd 만 보므로
    "읽을 것 없음" 이라 답한다. 그래서 방향키가 ESC 단독으로 판정돼
    선택이 취소되고 게임이 튕겼다.
    """
    if not is_tty():
        return None
    import os
    import termios
    import tty

    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return None

    def grab(n=1):
        try:
            return os.read(fd, n)
        except OSError:
            return b""

    try:
        # TCSANOW 로 즉시 전환한다. tty.setraw() 의 기본값은 TCSAFLUSH 라
        # 이미 들어와 있는 입력을 **버린다** — 빠르게 연타하거나 화면이
        # 그려지기 전에 누른 키가 통째로 사라진다.
        tty.setraw(fd, termios.TCSANOW)
        if timeout is not None and not _waiting(fd, timeout):
            return None
        ch = grab()
        if not ch:
            return KEY_ESC
        b = ch[0]

        if b == 3:
            raise KeyboardInterrupt
        if b in (13, 10):
            return KEY_ENTER
        if b == 9:
            return KEY_TAB
        if b in (127, 8):
            return KEY_BACKSPACE
        if b == 4:
            return KEY_ESC              # Ctrl+D 도 취소로 본다

        if b != 27:
            # 한글 등 멀티바이트는 이어지는 바이트까지 모아야 글자가 된다
            need = 3 if b >= 0xF0 else 2 if b >= 0xE0 else 1 if b >= 0xC0 else 0
            for _ in range(need):
                if not _waiting(fd, 0.05):
                    break
                ch += grab()
            return ch.decode("utf-8", "replace")

        # ESC — 뒤에 아무것도 없으면 ESC 단독, 있으면 이스케이프 시퀀스.
        if not _waiting(fd, 0.06):
            return KEY_ESC
        second = grab().decode("latin-1")
        if second not in ("[", "O"):
            return KEY_ESC
        third = grab().decode("latin-1")
        if third in _CSI:
            return _CSI[third]
        if third.isdigit():
            # \x1b[5~ 같은 것. ~ 까지 읽어 버린다.
            digits = third
            while _waiting(fd, 0.05):
                nxt = grab().decode("latin-1")
                if not nxt or nxt == "~" or not nxt.isdigit():
                    break
                digits += nxt
            return _CSI_TILDE.get(digits[0], KEY_ESC)
        return KEY_ESC
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except termios.error:
            pass


def clear_lines(n: int) -> None:
    """커서를 n 줄 올리고 그 아래를 지운다. 제자리 갱신용."""
    if n <= 0:
        return
    sys.stdout.write(f"\x1b[{n}A\r\x1b[J")
    sys.stdout.flush()


def hide_cursor() -> None:
    if is_tty():
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()


def show_cursor() -> None:
    if is_tty():
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


@contextmanager
def cursor_hidden():
    hide_cursor()
    try:
        yield
    finally:
        show_cursor()


def confirm_phrase(prompt, phrase, *, rgb=(210, 86, 90)):
    """되돌릴 수 없는 작업의 확인. 정확히 phrase 를 타이핑해야 True.

    y/n 은 손가락이 먼저 움직인다. 지우는 것에는 문장을 치게 한다.
    """
    got = ask_line(prompt, rgb=rgb)
    return got is not None and got.strip() == phrase
