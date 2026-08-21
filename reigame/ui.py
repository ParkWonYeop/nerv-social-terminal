# -*- coding: utf-8 -*-
"""rich 기반 TUI 렌더링."""
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
# 그런 터미널에서는 REI_PLAIN_INPUT=1 로 readline 없이 실행한다.
if os.environ.get("REI_PLAIN_INPUT", "") not in ("", "0"):
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

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import config

console = Console(highlight=False)

EYE = "#d2565a"          # 붉은 눈 — 경고·감소 색 (전역)
NERV = "#c98a2b"
DIM = "#6f7783"
GOLD = "#d8b45c"

# 활성 캐릭터 테마. set_character()가 채운다. 기본값은 레이 팔레트.
# 대사 색은 어떤 감정이든 속마음(DIM 회색)보다 확실히 밝아야 한다.
# persona 가 허용하는 감정 8가지 전부 있어야 한다 — 빠지면 neutral 로
# 떨어져 감정 표현이 사라진다.
MAIN = "#9ec5e0"
NAME = "레이"
NAME_JA = "綾波レイ"
NAME_EN = "AYANAMI REI"
NAME_FULL = "아야나미 레이"
STAGE_COLOR = ["#6f7783", "#7f97a8", "#9ec5e0", "#b7d6ea", "#e4b7c4", "#f0c9d4"]
EMOTION = {
    "neutral": "#9ec5e0", "slight": "#b7d6ea", "warm": "#e4b7c4",
    "cold": "#7f9db8", "curious": "#a9c9b4", "shaken": EYE,
    "annoyed": "#c99a8f", "distant": "#8ea6b8",
}


def set_character(char) -> None:
    """활성 캐릭터의 이름·팔레트를 화면 전체에 적용한다."""
    global MAIN, NAME, NAME_JA, NAME_EN, NAME_FULL, STAGE_COLOR, EMOTION
    NAME, NAME_FULL = char.name, char.full
    NAME_JA, NAME_EN = char.ja, char.en
    MAIN = char.theme["main"]
    STAGE_COLOR = list(char.theme["stage"])
    EMOTION = dict(char.theme["emotion"])


def width(text: str) -> int:
    """터미널 표시 폭. 한글·한자는 2칸."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, n: int) -> str:
    """표시 폭 기준 좌측 정렬 패딩."""
    return text + " " * max(0, n - width(text))


MOOD_KO = {
    "flat": "무표정", "calm": "평온", "cold": "차가움", "guarded": "경계",
    "curious": "궁금함", "unsettled": "동요", "annoyed": "불편",
    "tired": "지침", "warm": "누그러짐", "distant": "멀어짐",
    "quiet": "조용함", "empty": "텅 빔", "wary": "의심",
}


def mood_ko(m):
    return MOOD_KO.get((m or "").lower().strip(), m or "무표정")


def gauge(value, total=100, width=20, color=None):
    color = color or MAIN
    filled = int(round(width * max(0, min(total, value)) / total))
    t = Text()
    t.append("█" * filled, style=color)
    t.append("░" * (width - filled), style=DIM)
    return t


def header(st):
    """st: dict(affection, trust, interest, patience, mood, stage, stage_idx,
               lcl, tools, commits, streak, llm_used, llm_cap, offline, player)

    터미널이 좁으면 게이지를 줄이고 부가 정보를 떼어낸다.
    잘린 글자가 보이는 것보다 없는 게 낫다.
    """
    color = STAGE_COLOR[min(st["stage_idx"], len(STAGE_COLOR) - 1)]
    w = console.width
    roomy, mid = w >= 88, w >= 68
    gw = 22 if roomy else (14 if mid else 8)

    line1 = Text()
    line1.append(NAME_JA, style=f"bold {MAIN}")
    if mid:
        line1.append("  ·  ", style=DIM)
        line1.append(NAME_FULL, style=f"bold {MAIN}")

    right1 = Text()
    right1.append(st.get("player", "?"), style=f"bold {NERV}")
    if roomy:
        right1.append(" · NERV", style=NERV)

    line2 = Text()
    line2.append("호감  ", style=DIM)
    line2.append_text(gauge(st["affection"], width=gw, color=color))
    line2.append(f"  {st['affection']:>3}", style="white")
    line2.append(f"   [{st['stage']}]", style=color)

    def num(label, value, warn):
        t = Text()
        t.append(f"{label} ", style=DIM)
        t.append(f"{value:>3}", style="white" if value >= warn else EYE)
        return t

    line3 = Text()
    line3.append("      " if roomy else "", style=DIM)
    line3.append_text(num("신뢰", st["trust"], 30))
    line3.append("   ", style=DIM)
    line3.append_text(num("관심", st["interest"], 20))
    line3.append("   ", style=DIM)
    line3.append_text(num("인내", st["patience"], 20))
    if mid:
        line3.append("     기분 ", style=DIM)
        line3.append(mood_ko(st.get("mood")), style=color)

    line4 = Text()
    line4.append("LCL  ", style=DIM)
    line4.append(f"¤ {st['lcl']:,}", style=f"bold {GOLD}")
    line4.append("     도구 ", style=DIM)
    line4.append(str(st["tools"]), style="white")
    line4.append(" · 커밋 ", style=DIM)
    line4.append(str(st["commits"]), style="white")

    streak = (Text(f"연속 {st['streak']}일", style=NERV)
              if st.get("streak", 0) > 1 and mid else Text(""))

    grid = Table.grid(expand=True)
    grid.add_column(justify="left", no_wrap=True)
    grid.add_column(justify="right", no_wrap=True)
    grid.add_row(line1, right1)
    grid.add_row(line2, _budget_text(st) if mid else Text(""))
    grid.add_row(line3, Text(""))
    grid.add_row(line4, streak)
    return Panel(grid, border_style=MAIN, padding=(0, 1))


def _budget_text(st):
    if st.get("offline"):
        return Text("offline", style=DIM)
    used, cap = st.get("llm_used", 0), st.get("llm_cap", 0)
    if used >= cap:
        return Text(f"대사 {used}/{cap} · 한도 소진", style=EYE)
    if used >= config.LLM_WARN_AT:
        return Text(f"대사 {used}/{cap}", style=NERV)
    return Text(f"대사 {used}/{cap}", style=DIM)


def _entry_text(role, text, emo):
    """로그 항목 하나 → 스타일 입힌 Text."""
    if role == "narr":
        return Text("  " + text, style=f"italic {DIM}")
    if role == "user":
        t = Text()
        t.append("  > ", style=NERV)
        t.append(text, style="white")
        return t
    if role == "sys":
        return Text("  " + text, style=GOLD)
    if role == "inner":
        return Text("      (" + text + ")", style=f"italic {DIM}")
    if role == "delta":
        # "호감 +2 · 신뢰 -1" — 항목별로 증감 색을 입힌다
        t = Text("      ")
        for i, part in enumerate(text.split(" · ")):
            if i:
                t.append("  ", style=DIM)
            t.append(part, style=EYE if "-" in part else MAIN)
        return t
    if role == "opt":
        num, _, rest = text.partition(". ")
        t = Text("    ")
        t.append(num + ". ", style=NERV)
        t.append(rest, style="white")
        return t
    color = EMOTION.get(emo) or EMOTION["neutral"]
    t = Text()
    t.append(f"  {NAME} ", style=f"bold {color}")
    t.append("「" + text + "」", style=color)
    return t


def footer(hint):
    """터미널 폭에 맞춰 넣을 수 있는 것까지만 넣는다.

    좁은 창에서 줄바꿈되면 지저분하다. 폭이 부족하면 먼저 설명을 떼고,
    그래도 모자라면 뒤쪽 항목을 버린다.
    """
    avail = max(20, console.width - 2)

    def build(items, with_desc):
        t, used = Text(), 0
        for cmd, desc in items:
            piece = cmd + (" " + desc if with_desc and desc else "")
            need = width(piece) + (3 if used else 0)
            if used + need > avail:
                break
            if used:
                t.append("   ", style=DIM)
            t.append(cmd, style=f"bold {MAIN}")
            if with_desc and desc:
                t.append(" " + desc, style=DIM)
            used += need
        return t, used

    full, _ = build(hint, True)
    # 전부 들어갔는지 확인
    if width(full.plain) <= avail and len(full.plain.split("   ")) >= len(hint):
        return Group(Rule(style=DIM), full)
    bare, _ = build(hint, False)
    return Group(Rule(style=DIM), bare)


PANEL_ROWS = 6                    # header() 패널: 내용 4줄 + 테두리 2줄
FRAME_OVERHEAD = PANEL_ROWS + 3   # 구분선 + 힌트(2줄) + 입력 줄(1줄)


def frame(st, entries, hint, *, animate=False, delay=0.028):
    """하단 고정 상태창 프레임.

    화면을 [채팅(아래로 붙여 쌓임)] / [구분선·힌트] / [상태창] 순서로
    통째로 다시 그리고, 커서를 상태창 아래 입력 줄에 둔다.
    채팅이 길어져도 상태창은 항상 화면 하단에 남는다.

    animate=True 면 마지막 대사를 제자리에서 한 글자씩 찍는다.
    (커서를 해당 줄로 올렸다가 내려온다. 줄바꿈되는 긴 대사는 즉시 출력.)
    """
    w, h = console.width, console.height
    avail = max(3, h - FRAME_OVERHEAD)

    # 항목 → 터미널 줄 단위로 평탄화 (항목 사이 빈 줄 포함)
    rows, anim = [], None
    last_rei = max((i for i, e in enumerate(entries) if e[0] == "rei"),
                   default=None)
    for i, (role, text, emo) in enumerate(entries):
        t = _entry_text(role, text, emo)
        wrapped = t.wrap(console, w) or [Text("")]
        if (animate and i == last_rei and len(wrapped) == 1
                and sys.stdout.isatty() and h - FRAME_OVERHEAD >= 3):
            color = EMOTION.get(emo) or EMOTION["neutral"]
            anim = [len(rows), "「" + text + "」", color]
            rows.append(Text(f"  {NAME} ", style=f"bold {color}"))
        else:
            rows.extend(wrapped)
        rows.append(Text(""))

    if len(rows) > avail:
        drop = len(rows) - avail
        rows = rows[drop:]
        if anim:
            anim[0] -= drop
            if anim[0] < 0:
                anim = None
    else:
        pad = avail - len(rows)
        rows = [Text("")] * pad + rows
        if anim:
            anim[0] += pad

    console.clear()
    for r in rows:
        console.print(r)
    console.print(footer(hint))
    console.print(header(st))
    # 여기까지 avail + 2 + PANEL_ROWS 줄. 커서는 그 다음 줄 = 입력 줄.

    if anim:
        idx, quoted, color = anim
        up = avail + 2 + PANEL_ROWS - idx    # 입력 줄에서 대사 줄까지
        sys.stdout.write(f"\x1b[{up}A\r")
        sys.stdout.flush()
        console.print(Text(f"  {NAME} ", style=f"bold {color}"), end="")
        _type_inline(quoted, color, delay)
        sys.stdout.write(f"\x1b[{up}B\r")
        sys.stdout.flush()


def _type_inline(text, style, delay):
    """줄바꿈 없이 제자리에서 한 글자씩. Ctrl+C 면 남은 글자 즉시 출력."""
    if delay <= 0 or not sys.stdout.isatty():
        console.print(Text(text, style=style), end="")
        return
    done = 0
    with _echo_off():
        try:
            for done, ch in enumerate(text, 1):
                console.print(Text(ch, style=style), end="")
                time.sleep(delay)
        except KeyboardInterrupt:
            console.print(Text(text[done:], style=style), end="")


def prompt_area(hint):
    """입력창 위에 붙는 구분선 + 명령 힌트."""
    console.print()
    console.print(footer(hint))


def read_input(prompt="  > ", rgb=(201, 138, 43)):
    """편집해도 프롬프트가 지워지지 않는 입력.

    readline 이 프롬프트 폭을 셀 수 있도록 ANSI 색을 \001..\002 로
    감싼다. 이걸 안 하면 긴 입력·백스페이스에서 커서 위치가 어긋난다.
    readline 이 없으면(REI_PLAIN_INPUT=1) 프롬프트를 직접 찍고
    커널 줄 편집으로 읽는다 — 예전 방식.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
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
    return input(f"\001{color}\002{prompt}\001{reset}\002")


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
def _echo_off():
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


@contextmanager
def thinking():
    """캐릭터가 응답을 만드는 동안의 대기 표시.

    미리 타이핑해도 된다 — 화면에는 안 보이지만 입력창이 뜰 때
    그대로 나타난다.
    """
    with _echo_off():
        with console.status(Text(f"{NAME}가 대답을 생각한다…", style=DIM),
                            spinner="dots", spinner_style=DIM):
            yield


def notice(text, style=None):
    console.print(Text("  " + text, style=style or GOLD))


def dim(text):
    console.print(Text("  " + text, style=DIM))


def title_card():
    console.clear()
    ja = "  ".join(NAME_JA.replace("・", " "))
    en = " ".join(NAME_EN)
    art = Text()
    art.append("\n\n")
    art.append(f"        {ja}\n", style=f"bold {MAIN}")
    art.append(f"        {en}\n\n", style=DIM)
    art.append("        ─────────────────────────\n\n", style=DIM)
    art.append("        NERV 제1지부 기술부 단말\n", style=NERV)
    art.append("        인증됨. 근무 기록 동기화 중…\n", style=DIM)
    console.print(Align.center(art))


_BLUE = "#5b8fd6"

_BOOT_MODULES = [
    "nerv_core.sys", "geofront_mount.ko", "at_field.ko", "lcl_pump.sys",
    "entry_plug.ko", "sync_graph.sys", "umbilical.ko", "bakelite_valve.sys",
    "pattern_scope.ko", "harmonics.sys", "duty_record.sys", "dialogue.ko",
    "memory_bank.sys", "sigil_render.ko", "cage_lock.sys", "dogma_gate.ko",
    "hexfield_hud.ko", "penpen_feeder.ko",
]

_BOOT_DEVICES = [
    "magi-bus0", "magi-bus1", "neural-io0", "neural-io1", "lcl-pump0",
    "plug-if0", "atf-gen0", "atf-gen1", "cage-lock0", "cage-lock1",
    "cage-lock2", "umbilical0", "bakelite0", "harmonics0", "hud-hex0",
    "thermal-pen0",
]

_BOOT_MOUNTS = [
    "/dev/geofront/central", "/dev/geofront/cage-07",
    "/dev/geofront/pribnow-box", "/dev/geofront/archive",
    "/dev/geofront/terminal-dogma",
]

_BOOT_SERVICES = [
    "sync_graphd", "duty_recordd", "dialogue_engined", "memory_bankd",
    "pattern_scoped", "stage_controld", "hex_hudd", "bakelite_valved",
    "coffee_heaterd", "penpen_feederd",
]

_BOOT_WARNS = [
    "WARN  제3케이지 습도 +2.1% — 허용치 이내",
    "WARN  심층부 격벽 D-17 응답 지연 — 재시도 OK",
    "WARN  bakelite 밸브 응답 12ms 지연 — 무시됨",
]

_NERV_LOGO = [
    "███╗   ██╗ ███████╗ ██████╗  ██╗   ██╗",
    "████╗  ██║ ██╔════╝ ██╔══██╗ ██║   ██║",
    "██╔██╗ ██║ █████╗   ██████╔╝ ██║   ██║",
    "██║╚██╗██║ ██╔══╝   ██╔══██╗ ╚██╗ ██╔╝",
    "██║ ╚████║ ███████╗ ██║  ██║  ╚████╔╝ ",
    "╚═╝  ╚═══╝ ╚══════╝ ╚═╝  ╚═╝   ╚═══╝  ",
]

_GATE_GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉ0123456789░▒▓·:+"


def boot_sequence(animate=True):
    """기동 연출 — 시스템 로그 → MAGI 심의 → 다이브 → NERV → 경계 동기화.

    Ctrl+C 로 언제든 건너뛴다. 비 TTY / --no-anim 이면 그리지 않는다.
    """
    console.clear()
    if not (animate and sys.stdout.isatty()):
        return
    import random as _r
    w = console.width

    def line(text, style=DIM, pause=0.0):
        console.print(Text("  " + text, style=style))
        if pause:
            time.sleep(pause)

    def center(text, style, pause=0.0):
        console.print(Text(" " * max(0, (w - width(text)) // 2) + text,
                           style=style))
        if pause:
            time.sleep(pause)

    def inplace(text, style):
        console.print(Text("  " + text, style=style), end="\r")

    try:
        with _echo_off():
            # ── 1. 저수준 로그의 벽 — 파바박 ──────────────────────────
            console.print()
            line("MAGI BIOS v7.71  —  NERV Technical Bureau", NERV, 0.30)

            # 커널 로그 스타일: [타임스탬프] 본문 ... 상태.
            # 실제 부팅처럼 보이도록 순서는 그럴듯하게, 값은 매번 다르게.
            ts = 0.0
            wall = []          # (본문, 상태, 상태색)

            def k(body, status="OK", color=_BLUE):
                nonlocal ts
                ts += _r.uniform(0.0004, 0.0182)
                wall.append((f"[{ts:10.6f}] {body}", status, color))

            for i in range(8):
                k(f"cpu{i}: magi-arch core online, {_r.randint(880,1240)} TFLOPS")
            for i in range(16):
                k(f"memory bank {i:02d}/16 " + "." * 21, "4096 TB OK")
            k("ecc scrub pass 1/1 " + "." * 21, "0 errors")
            for dev in _BOOT_DEVICES:
                k(f"probe {pad(dev, 14)} irq {_r.randint(3, 63):02d} "
                  f"dma 0x{_r.randint(0x1000, 0xFFFF):04X}")
            for _ in range(18):
                k(f"scan sector 0x{_r.randint(0, 0xFFFF):04X} "
                  + "." * 24, "clean")
            for mod in _BOOT_MODULES:
                k(f"insmod {pad(mod, 18)} at 0x{_r.randint(0x10000, 0xFFFFF):06X}")
            for m in _BOOT_MOUNTS:
                k(f"mount {pad(m, 28)} " + "." * 8, "rw,sync")
            for i in range(4):
                k(f"umbilical link-{i} negotiated "
                  f"{_r.choice((10, 40, 100))}Gbps " + "." * 6, "UP")
            for magi in ("MELCHIOR", "BALTHASAR", "CASPER"):
                for _ in range(6):
                    n = _r.randint(1, 2048)
                    k(f"{magi.lower()}: cell {n:04d}/2048 integrity "
                      + "." * 12, "verified")
            for svc in _BOOT_SERVICES:
                k(f"starting {pad(svc, 18)} pid {_r.randint(200, 9999)}")
            k("A.T. field driver: self-test " + "." * 15, "PASS", GOLD)

            # WARN 몇 줄을 무작위 위치에 심는다 — 진짜 로그의 맛
            for warn in _r.sample(_BOOT_WARNS, len(_BOOT_WARNS)):
                wall.insert(_r.randint(20, len(wall) - 1), (warn, "", GOLD))

            for i, (body, status, color) in enumerate(wall):
                t = Text("  ")
                t.append(body, style=GOLD if body.startswith("WARN") else DIM)
                if status:
                    t.append(" " + status, style=color)
                console.print(t)
                time.sleep(0.004 if i % 23 else 0.05)   # 이따금 숨 고르기
            time.sleep(0.25)

            # ── 2. MAGI 심의 ──────────────────────────────────────────
            console.print()
            line("MAGI SYSTEM  기동", NERV, 0.35)
            for magi in ("MELCHIOR-1", "BALTHASAR-2", "CASPER-3"):
                line(f"{pad(magi, 14)}............ 사고 개시", DIM, 0.16)
            console.print()
            line("심의 안건: 오퍼레이터 단말 접속 허가", "white", 0.45)
            for _ in range(3):
                inplace("합의 형성 중" + "." * (_ + 1) + "  ", DIM)
                time.sleep(0.28)
            console.print()
            for magi in ("MELCHIOR-1", "BALTHASAR-2", "CASPER-3"):
                t = Text("  ")
                t.append(pad(magi, 14), style=DIM)
                t.append("▶  ", style=DIM)
                t.append("賛成", style=GOLD)
                console.print(t)
                time.sleep(0.30)
            line("결의 — 만장일치. 가결.", GOLD, 0.55)

            # ── 3. 다이브 — 엔트리 플러그 · LCL · 동조 ────────────────
            console.print()
            line("ENTRY PLUG 삽입", NERV, 0.25)
            line("고정 볼트 체결 ................ OK", DIM, 0.14)
            bar_w = min(28, w - 24)
            for i in range(bar_w + 1):
                bar = "█" * i + "░" * (bar_w - i)
                inplace(f"LCL 주입     {bar}", _BLUE)
                time.sleep(0.022)
            console.print()
            line("LCL 전기 분해 ................. 완료", DIM, 0.14)
            for step in ("제1단계", "제2단계", "제3단계"):
                line(f"신경 접속 {step} ............. 결합", DIM, 0.15)
            line("A.T. 필드 전개", EYE, 0.30)
            rate = 0.0
            while rate < 98.2:
                rate = min(98.2, rate + _r.uniform(2.5, 9.5))
                inplace(f"동조율  {rate:5.1f}%          ", "white")
                time.sleep(0.05)
            console.print()
            line("한계값 돌파 — 접속 유지", GOLD, 0.35)
            line("PATTERN 해석 .................. 청색(BLUE)", _BLUE, 0.40)

            # ── 4. NERV — 화면 정중앙에 단독으로 ─────────────────────
            time.sleep(0.35)
            console.clear()
            h = console.height
            block = len(_NERV_LOGO) + 2          # 로고 + 빈 줄 + 모토
            for _ in range(max(0, (h - block) // 2 - 1)):
                console.print()
            for row in _NERV_LOGO:
                center(row, EYE)
                time.sleep(0.07)
            console.print()
            center("God's in his heaven. All's right with the world.",
                   GOLD, 1.2)

            # ── 5. 경계 동기화 — 세계로 ───────────────────────────────
            console.print()
            center("― 단말과 의식의 경계를 동기화한다 ―", DIM, 0.5)
            gw = max(20, w - 6)
            for i in range(9):
                row = "".join(_r.choice(_GATE_GLYPHS) for _ in range(gw))
                console.print(Text("  " + row,
                                   style=_BLUE if i % 3 else DIM))
                time.sleep(0.035)
            time.sleep(0.30)
    except KeyboardInterrupt:
        pass
    console.clear()


def select_character(infos):
    """시작 화면 — 누구를 만나러 갈지 고른다.

    infos: [(char, aff, stage_name)] 목록.
    선택된 char 를 반환. 취소(EOF/Ctrl+C)면 None.
    """
    console.clear()
    console.print()
    console.print(Text("  NERV 제1지부 기술부 단말", style=NERV))
    console.print(Text("  인증됨.", style=DIM))
    console.print()
    console.print(Text("  누구를 만나러 왔나.", style="white"))
    console.print()
    for i, (ch, aff, stage) in enumerate(infos, 1):
        main = ch.theme["main"]
        line = Text("    ")
        line.append(f"{i}. ", style=NERV)
        line.append(pad(ch.full, 22), style=f"bold {main}")
        line.append(pad(ch.ja, 26), style=DIM)
        line.append(f"호감 {aff:>3}", style="white")
        line.append(f"  [{stage}]", style=main)
        console.print(line)
    console.print()
    while True:
        try:
            raw = read_input("  고른다 (번호 또는 이름) > ",
                             rgb=(111, 119, 131)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(infos):
            return infos[int(raw) - 1][0]
        for ch, _, _ in infos:
            if raw == ch.id or raw in (ch.name.lower(), ch.full.lower()):
                return ch
        dim("그런 사람은 여기 없다.")
