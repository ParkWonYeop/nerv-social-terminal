# -*- coding: utf-8 -*-
"""민무늬 단말 — UI 플러그인이 어디까지 바꿀 수 있는지 보여주는 예제.

세 단계로 되어 있다. 위에서부터 점점 깊이 들어간다.

  1. 팔레트만 바꾸기        PALETTE 를 갈아끼운다
  2. 연출 갈아끼우기        boot() 를 덮어쓴다
  3. 화면 레이아웃 바꾸기   shop() 을 덮어쓴다 — 표 대신 줄글

덮어쓰지 않은 것은 전부 BaseUI 의 것이 그대로 쓰인다. 그래서
색 하나만 바꾸고 싶은 사람은 PALETTE 한 줄만 적으면 된다.

실용적인 쓸모도 있다: 연출이 전혀 없어서 느린 SSH 나 좁은 창,
스크린리더에서 편하다.
"""
from rich.text import Text

from nervterm import term
from nervterm.ui.base import BaseUI, console


class UI(BaseUI):
    # ── 1. 팔레트 ──────────────────────────────────────────────────────
    PALETTE = {
        "plain": "white",
        "info": "cyan",
        "good": "green",
        "warn": "yellow",
        "danger": "red",
        "money": "yellow",
        "dim": "bright_black",
        "accent": "bright_white",
    }

    # 상태창을 테두리 없이 그린다 — 그래서 두 줄이 덜 든다.
    PANEL_ROWS = 4

    # ── 2. 연출 ────────────────────────────────────────────────────────
    def boot(self, *, animate=True):
        """연출 없음. 이게 이 플러그인의 존재 이유다."""
        console.clear()

    def title_card(self, card):
        console.clear()
        console.print()
        console.print(Text(f"  == {card.full} ==", style="bold white"))
        console.print()

    # 상태창을 Panel 로 감싸지 않는다 (BaseUI 기본이 이미 그렇다)

    # ── 3. 화면 레이아웃 ───────────────────────────────────────────────
    def shop(self, sv):
        """표 대신 줄글로. 폭이 좁아도 안 깨진다."""
        console.print()
        console.print(Text(f"  {sv.title}", style="bold white"))
        console.print()
        for row in sv.rows:
            mark = "" if row.affordable else "  (모자람)"
            given = f"  [준 적 있음 x{row.given}]" if row.given else ""
            console.print(Text(
                f"    {row.name} — {sv.currency_symbol}{row.price}"
                f"{mark}{given}",
                style="white" if row.affordable else "bright_black"))
            console.print(Text(f"      이름: {row.key}", style="bright_black"))
        if sv.locked:
            console.print()
            console.print(Text("  아직 못 여는 것: " + ", ".join(
                f"{r.name}(호감 {r.need})" for r in sv.locked[:5]),
                style="bright_black"))
        if sv.hint:
            console.print()
            console.print(Text("  " + sv.hint, style="bright_black"))

    def thinking(self, name=""):
        """스피너 대신 한 줄. 스크린리더가 스피너를 계속 읽으면 괴롭다."""
        from contextlib import contextmanager

        @contextmanager
        def quiet():
            console.print(Text(f"  ({name or self.name} 생각 중…)",
                               style="bright_black"))
            yield
        return quiet()
