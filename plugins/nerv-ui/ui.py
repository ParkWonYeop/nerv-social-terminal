# -*- coding: utf-8 -*-
"""NERV 단말 — 기본 UI 플러그인.

BaseUI 를 상속해서 NERV 의 얼굴을 씌운다.
바꾸는 것은 세 가지다:

  1. 팔레트          붉은 눈, NERV 주황, 지오프론트 청색
  2. 부팅 연출       MAGI 심의 → 엔트리 플러그 다이브 → 역십자
  3. 상태창 테두리   Panel 로 감싼다

목록·설정 화면은 BaseUI 의 것을 그대로 쓴다. 색만 바뀌어도
충분히 NERV 처럼 보인다.
"""
import random
import sys
import time

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from nervterm import term
from nervterm.ui.base import BaseUI, console

EYE = "#d2565a"          # 붉은 눈 — 경고·감소
NERV_ORANGE = "#c98a2b"
GEOFRONT = "#5b8fd6"
GOLD = "#d8b45c"
DIM = "#6f7783"


class UI(BaseUI):
    PALETTE = {
        "plain": "white",
        "info": GEOFRONT,
        "good": "#a9c9b4",
        "warn": NERV_ORANGE,
        "danger": EYE,
        "money": GOLD,
        "dim": DIM,
        "accent": "#9ec5e0",
    }

    # header() 를 Panel 로 감싸면 위아래 테두리로 2줄이 더 든다.
    PANEL_ROWS = 6

    # ── 상태창을 테두리로 감싼다 ───────────────────────────────────────
    def wrap_header(self, st):
        return Panel(self.header(st), border_style=self.main, padding=(0, 1))

    # ── 타이틀 ─────────────────────────────────────────────────────────
    def title_card(self, card):
        console.clear()
        ja = "  ".join((card.ja or card.full).replace("・", " "))
        en = " ".join(card.en or card.id.upper())
        art = Text()
        art.append("\n\n")
        art.append(f"        {ja}\n", style=f"bold {self.main}")
        art.append(f"        {en}\n\n", style=DIM)
        art.append("        ─────────────────────────\n\n", style=DIM)
        terminal = (self.world.terminal_name if self.world
                    else "NERV 제1지부 기술부 단말")
        art.append(f"        {terminal}\n", style=NERV_ORANGE)
        art.append("        인증됨. 근무 기록 동기화 중…\n", style=DIM)
        console.print(Align.center(art))

    # ── 부팅 연출 ──────────────────────────────────────────────────────
    def boot(self, *, animate=True):
        """기동 연출 — 시스템 로그 → MAGI 심의 → 다이브 → NERV → 경계 동기화.

        Ctrl+C 로 언제든 건너뛴다. 비 TTY / --no-anim 이면 그리지 않는다.
        """
        console.clear()
        if not (animate and sys.stdout.isatty()):
            return
        w = console.width

        def line(text, style=DIM, pause=0.0):
            console.print(Text("  " + text, style=style))
            if pause:
                time.sleep(pause)

        def center(text, style, pause=0.0):
            console.print(Text(" " * max(0, (w - term.width(text)) // 2) + text,
                               style=style))
            if pause:
                time.sleep(pause)

        def inplace(text, style):
            console.print(Text("  " + text, style=style), end="\r")

        try:
            with term.echo_off():
                self._boot_log(line, w)
                self._boot_magi(line, inplace)
                self._boot_dive(line, inplace, w)
                self._boot_nerv(center)
                self._boot_gate(center, w)
        except KeyboardInterrupt:
            pass
        console.clear()

    # ── 1. 저수준 로그의 벽 ────────────────────────────────────────────
    def _boot_log(self, line, w):
        console.print()
        line("MAGI BIOS v7.71  —  NERV Technical Bureau", NERV_ORANGE, 0.30)

        # 커널 로그 스타일: [타임스탬프] 본문 ... 상태.
        # 실제 부팅처럼 보이도록 순서는 그럴듯하게, 값은 매번 다르게.
        ts = 0.0
        wall = []

        def k(body, status="OK", color=GEOFRONT):
            nonlocal ts
            ts += random.uniform(0.0004, 0.0182)
            wall.append((f"[{ts:10.6f}] {body}", status, color))

        for i in range(8):
            k(f"cpu{i}: magi-arch core online, "
              f"{random.randint(880, 1240)} TFLOPS")
        for i in range(16):
            k(f"memory bank {i:02d}/16 " + "." * 21, "4096 TB OK")
        k("ecc scrub pass 1/1 " + "." * 21, "0 errors")
        for dev in _BOOT_DEVICES:
            k(f"probe {term.pad(dev, 14)} irq {random.randint(3, 63):02d} "
              f"dma 0x{random.randint(0x1000, 0xFFFF):04X}")
        for _ in range(18):
            k(f"scan sector 0x{random.randint(0, 0xFFFF):04X} "
              + "." * 24, "clean")
        for mod in _BOOT_MODULES:
            k(f"insmod {term.pad(mod, 18)} at "
              f"0x{random.randint(0x10000, 0xFFFFF):06X}")
        for m in _BOOT_MOUNTS:
            k(f"mount {term.pad(m, 28)} " + "." * 8, "rw,sync")
        for i in range(4):
            k(f"umbilical link-{i} negotiated "
              f"{random.choice((10, 40, 100))}Gbps " + "." * 6, "UP")
        for magi in ("MELCHIOR", "BALTHASAR", "CASPER"):
            for _ in range(6):
                n = random.randint(1, 2048)
                k(f"{magi.lower()}: cell {n:04d}/2048 integrity "
                  + "." * 12, "verified")
        for svc in _BOOT_SERVICES:
            k(f"starting {term.pad(svc, 18)} pid {random.randint(200, 9999)}")
        k("A.T. field driver: self-test " + "." * 15, "PASS", GOLD)

        # WARN 몇 줄을 무작위 위치에 심는다 — 진짜 로그의 맛
        for warn in random.sample(_BOOT_WARNS, len(_BOOT_WARNS)):
            wall.insert(random.randint(20, len(wall) - 1), (warn, "", GOLD))

        for i, (body, status, color) in enumerate(wall):
            t = Text("  ")
            t.append(body, style=GOLD if body.startswith("WARN") else DIM)
            if status:
                t.append(" " + status, style=color)
            console.print(t)
            time.sleep(0.004 if i % 23 else 0.05)      # 이따금 숨 고르기
        time.sleep(0.25)

    # ── 2. MAGI 심의 ───────────────────────────────────────────────────
    def _boot_magi(self, line, inplace):
        console.print()
        line("MAGI SYSTEM  기동", NERV_ORANGE, 0.35)
        for magi in ("MELCHIOR-1", "BALTHASAR-2", "CASPER-3"):
            line(f"{term.pad(magi, 14)}............ 사고 개시", DIM, 0.16)
        console.print()
        line("심의 안건: 오퍼레이터 단말 접속 허가", "white", 0.45)
        for i in range(3):
            inplace("합의 형성 중" + "." * (i + 1) + "  ", DIM)
            time.sleep(0.28)
        console.print()
        for magi in ("MELCHIOR-1", "BALTHASAR-2", "CASPER-3"):
            t = Text("  ")
            t.append(term.pad(magi, 14), style=DIM)
            t.append("▶  ", style=DIM)
            t.append("賛成", style=GOLD)
            console.print(t)
            time.sleep(0.30)
        line("결의 — 만장일치. 가결.", GOLD, 0.55)

    # ── 3. 다이브 — 엔트리 플러그 · LCL · 동조 ─────────────────────────
    def _boot_dive(self, line, inplace, w):
        console.print()
        line("ENTRY PLUG 삽입", NERV_ORANGE, 0.25)
        line("고정 볼트 체결 ................ OK", DIM, 0.14)
        bar_w = min(28, w - 24)
        for i in range(bar_w + 1):
            bar = "█" * i + "░" * (bar_w - i)
            inplace(f"LCL 주입     {bar}", GEOFRONT)
            time.sleep(0.022)
        console.print()
        line("LCL 전기 분해 ................. 완료", DIM, 0.14)
        for step in ("제1단계", "제2단계", "제3단계"):
            line(f"신경 접속 {step} ............. 결합", DIM, 0.15)
        line("A.T. 필드 전개", EYE, 0.30)
        rate = 0.0
        while rate < 98.2:
            rate = min(98.2, rate + random.uniform(2.5, 9.5))
            inplace(f"동조율  {rate:5.1f}%          ", "white")
            time.sleep(0.05)
        console.print()
        line("한계값 돌파 — 접속 유지", GOLD, 0.35)
        line("PATTERN 해석 .................. 청색(BLUE)", GEOFRONT, 0.40)

    # ── 4. NERV — 화면 정중앙에 단독으로 ───────────────────────────────
    def _boot_nerv(self, center):
        time.sleep(0.35)
        console.clear()
        h = console.height
        block = len(_NERV_LOGO) + 2
        for _ in range(max(0, (h - block) // 2 - 1)):
            console.print()
        for row in _NERV_LOGO:
            center(row, EYE)
            time.sleep(0.07)
        console.print()
        center("God's in his heaven. All's right with the world.", GOLD, 1.2)

    # ── 5. 경계 동기화 — 세계로 ────────────────────────────────────────
    def _boot_gate(self, center, w):
        console.print()
        center("― 단말과 의식의 경계를 동기화한다 ―", DIM, 0.5)
        gw = max(20, w - 6)
        for i in range(9):
            row = "".join(random.choice(_GATE_GLYPHS) for _ in range(gw))
            console.print(Text("  " + row,
                               style=GEOFRONT if i % 3 else DIM))
            time.sleep(0.035)
        time.sleep(0.30)


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
