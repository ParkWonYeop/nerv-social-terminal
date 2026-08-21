# -*- coding: utf-8 -*-
"""BaseUI — 기본 렌더러이자 UI 플러그인의 부모.

UI 플러그인은 이걸 상속해서 **바꾸고 싶은 것만** 덮어쓴다.
색만 바꾸려면 PALETTE 하나, 부팅 연출만 넣으려면 boot() 하나면 된다.
전부 구현하라고 하면 아무도 플러그인을 안 만든다.

여기 있는 구현은 '밋밋하지만 제대로 도는' 화면이다. 플러그인이
하나도 없어도 게임은 이 모습으로 돌아간다.
"""
import random
import sys
import time
from contextlib import contextmanager

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .. import term
from . import view as V

console = term.console


class BaseUI:
    # ── 팔레트 ─────────────────────────────────────────────────────────
    # tone 이름 → 색. 플러그인은 이 dict 만 갈아끼워도 된다.
    PALETTE = {
        "plain": "white",
        "info": "#8fa8bf",
        "good": "#8fbf9a",
        "warn": "#d8b45c",
        "danger": "#d2565a",
        "money": "#d8b45c",
        "dim": "#6f7783",
        "accent": "#9ec5e0",
    }

    # 감정 → 색. 캐릭터 테마가 있으면 그게 이긴다.
    EMOTION_FALLBACK = {
        "neutral": "#9ec5e0", "slight": "#b7d6ea", "warm": "#e4b7c4",
        "cold": "#7f9db8", "curious": "#a9c9b4", "shaken": "#d2565a",
        "annoyed": "#c99a8f", "distant": "#8ea6b8",
    }

    MOOD_KO = {
        "flat": "무표정", "calm": "평온", "cold": "차가움", "guarded": "경계",
        "curious": "궁금함", "unsettled": "동요", "annoyed": "불편",
        "tired": "지침", "warm": "누그러짐", "distant": "멀어짐",
        "quiet": "조용함", "empty": "텅 빔", "wary": "의심",
    }

    # 상태창이 차지하는 줄 수. frame() 이 채팅 높이를 계산할 때 쓴다.
    PANEL_ROWS = 6
    FRAME_EXTRA = 3          # 구분선 + 힌트 + 입력 줄

    def __init__(self, *, world=None):
        self.world = world
        self.char = None
        self.name = ""
        self.main = self.PALETTE["accent"]
        self.stage_colors = [self.PALETTE["dim"]]
        self.emotion = dict(self.EMOTION_FALLBACK)

    # ── 캐릭터·세계관 ──────────────────────────────────────────────────
    def set_character(self, char) -> None:
        """활성 캐릭터의 이름·팔레트를 화면 전체에 적용한다."""
        self.char = char
        self.name = char.name
        theme = getattr(char, "theme", None) or {}
        self.main = theme.get("main") or self.PALETTE["accent"]
        self.stage_colors = list(theme.get("stage") or [self.main])
        self.emotion = dict(self.EMOTION_FALLBACK)
        self.emotion.update(theme.get("emotion") or {})

    def set_world(self, world) -> None:
        self.world = world

    # ── 색 고르기 ──────────────────────────────────────────────────────
    def color(self, tone: str) -> str:
        return self.PALETTE.get(tone, self.PALETTE["plain"])

    @property
    def dim_color(self) -> str:
        return self.PALETTE["dim"]

    def emotion_color(self, emo: str) -> str:
        return self.emotion.get(emo) or self.emotion["neutral"]

    def stage_color(self, idx: int) -> str:
        if not self.stage_colors:
            return self.main
        return self.stage_colors[min(idx, len(self.stage_colors) - 1)]

    def mood_ko(self, mood) -> str:
        key = (mood or "").lower().strip()
        return self.MOOD_KO.get(key, mood or "무표정")

    # ── 원시 출력 ──────────────────────────────────────────────────────
    def clear(self) -> None:
        console.clear()

    def blank(self) -> None:
        console.print()

    def line(self, text, tone="plain", indent=2) -> None:
        console.print(Text(" " * indent + text, style=self.color(tone)))

    def notice(self, text, tone="warn") -> None:
        self.line(text, tone)

    def dim(self, text) -> None:
        console.print(Text("  " + text, style=self.dim_color))

    def rule(self) -> None:
        console.print(Rule(style=self.dim_color))

    def gauge(self, value, total=100, width=20, color=None):
        color = color or self.main
        filled = int(round(width * max(0, min(total, value)) / total))
        t = Text()
        t.append("█" * filled, style=color)
        t.append("░" * (width - filled), style=self.dim_color)
        return t

    # ── 대기 표시 ──────────────────────────────────────────────────────
    @contextmanager
    def thinking(self, name=""):
        """캐릭터가 응답을 만드는 동안. 미리 타이핑해도 안 깨지게 에코를 끈다."""
        who = name or self.name or "상대"
        with term.echo_off():
            with console.status(Text(f"{who}가 대답을 생각한다…",
                                     style=self.dim_color),
                                spinner="dots", spinner_style=self.dim_color):
                yield

    # ── 부팅 연출 ──────────────────────────────────────────────────────
    def boot(self, *, animate=True) -> None:
        """첫 화면. 기본은 아무것도 안 한다 — 플러그인이 연출을 넣는다."""
        console.clear()

    # ── 타이틀 ─────────────────────────────────────────────────────────
    def title_card(self, card: V.CharacterCard) -> None:
        console.clear()
        self.blank()
        console.print(Text(f"  {card.full}", style=f"bold {self.main}"))
        if card.ja:
            console.print(Text(f"  {card.ja}", style=self.dim_color))
        self.blank()
        if self.world is not None:
            console.print(Text(f"  {self.world.terminal_name or self.world.name}",
                               style=self.color("warn")))
        self.blank()

    # ── 시작 화면 ──────────────────────────────────────────────────────
    def select_character(self, sv: V.SelectView):
        """누구를 만나러 갈지 고른다.

        돌려주는 값:
            ("char", 캐릭터id)   그 사람에게 간다
            ("settings", None)   설정 화면
            ("quit", None)       나간다
        """
        console.clear()
        self.blank()
        if sv.terminal_name:
            console.print(Text(f"  {sv.terminal_name}", style=self.color("warn")))
        console.print(Text("  인증됨.", style=self.dim_color))
        self.blank()
        for tone, text in sv.notes:
            self.line(text, tone)
        if sv.notes:
            self.blank()

        if not sv.cards:
            self.line("만날 수 있는 사람이 없다.", "danger")
            self.dim("설정에서 캐릭터를 켜거나, 캐릭터 플러그인을 설치하라.")
        else:
            console.print(Text("  누구를 만나러 왔나.", style="white"))
            self.blank()
            for i, card in enumerate(sv.cards, 1):
                accent = card.color or self.main
                row = Text("    ")
                row.append(f"{i}. ", style=self.color("warn"))
                row.append(term.pad(card.full, 22), style=f"bold {accent}")
                row.append(term.pad(card.ja, 26), style=self.dim_color)
                row.append(f"호감 {card.affection:>3}", style="white")
                row.append(f"  [{card.stage}]", style=accent)
                console.print(row)

        self.blank()
        console.print(Text("    s. ", style=self.color("warn")) +
                      Text("설정", style="white") +
                      Text("      q. ", style=self.color("warn")) +
                      Text("나간다", style="white"))
        self.blank()

        while True:
            raw = term.ask_line("  고른다 > ", rgb=(111, 119, 131))
            if raw is None:
                return ("quit", None)
            raw = raw.strip().lower()
            if not raw:
                continue
            if raw in ("q", "quit", "exit", "나감"):
                return ("quit", None)
            if raw in ("s", "설정", "settings", "config"):
                return ("settings", None)
            if raw.isdigit() and 1 <= int(raw) <= len(sv.cards):
                return ("char", sv.cards[int(raw) - 1].id)
            for card in sv.cards:
                if raw in (card.id, card.name.lower(), card.full.lower()):
                    return ("char", card.id)
            self.dim("그런 사람은 여기 없다.")

    # ── 상태창 프레임 ──────────────────────────────────────────────────
    def header(self, st: V.Status):
        """상태창. 좁은 터미널에서는 스스로 줄인다."""
        color = self.stage_color(st.stage_idx)
        w = console.width
        roomy, mid = w >= 88, w >= 68
        gw = 22 if roomy else (14 if mid else 8)

        line1 = Text()
        line1.append(st.char_ja or st.char_name, style=f"bold {self.main}")
        if mid and st.char_full:
            line1.append("  ·  ", style=self.dim_color)
            line1.append(st.char_full, style=f"bold {self.main}")

        right1 = Text()
        right1.append(st.player, style=f"bold {self.color('warn')}")

        line2 = Text()
        line2.append("호감  ", style=self.dim_color)
        line2.append_text(self.gauge(st.affection, width=gw, color=color))
        line2.append(f"  {st.affection:>3}", style="white")
        line2.append(f"   [{st.stage}]", style=color)

        def num(label, value, warn):
            t = Text()
            t.append(f"{label} ", style=self.dim_color)
            t.append(f"{value:>3}",
                     style="white" if value >= warn else self.color("danger"))
            return t

        line3 = Text()
        line3.append("      " if roomy else "", style=self.dim_color)
        line3.append_text(num("신뢰", st.trust, 30))
        line3.append("   ", style=self.dim_color)
        line3.append_text(num("관심", st.interest, 20))
        line3.append("   ", style=self.dim_color)
        line3.append_text(num("인내", st.patience, 20))
        if mid:
            line3.append("     기분 ", style=self.dim_color)
            line3.append(self.mood_ko(st.mood), style=color)

        line4 = Text()
        line4.append(f"{st.currency_name}  ", style=self.dim_color)
        line4.append(st.money_text(), style=f"bold {self.color('money')}")
        line4.append("     도구 ", style=self.dim_color)
        line4.append(str(st.tools), style="white")
        line4.append(" · 커밋 ", style=self.dim_color)
        line4.append(str(st.commits), style="white")

        streak = (Text(f"연속 {st.streak}일", style=self.color("warn"))
                  if st.streak > 1 and mid else Text(""))

        grid = Table.grid(expand=True)
        grid.add_column(justify="left", no_wrap=True)
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(line1, right1)
        grid.add_row(line2, self.budget_text(st) if mid else Text(""))
        grid.add_row(line3, Text(""))
        grid.add_row(line4, streak)
        return grid

    def budget_text(self, st: V.Status):
        """오른쪽 위의 대사 예산 표시."""
        if st.offline:
            return Text("offline", style=self.dim_color)
        used, cap = st.llm_used, st.llm_cap
        label = f"대사 {used}/{cap}"
        if st.billable:
            label += " · 과금"
        if cap and used >= cap:
            return Text(label + " · 한도 소진", style=self.color("danger"))
        if st.billable:
            return Text(label, style=self.color("danger"))
        if st.llm_warn_at and used >= st.llm_warn_at:
            return Text(label, style=self.color("warn"))
        return Text(label, style=self.dim_color)

    def entry_text(self, e: V.LogEntry):
        """로그 항목 하나 → 스타일 입힌 Text."""
        dim = self.dim_color
        if e.role == "narr":
            return Text("  " + e.text, style=f"italic {dim}")
        if e.role == "user":
            t = Text()
            t.append("  > ", style=self.color("warn"))
            t.append(e.text, style="white")
            return t
        if e.role == "sys":
            return Text("  " + e.text, style=self.color("money"))
        if e.role == "inner":
            return Text("      (" + e.text + ")", style=f"italic {dim}")
        if e.role == "delta":
            t = Text("      ")
            for i, part in enumerate(e.text.split(" · ")):
                if i:
                    t.append("  ", style=dim)
                t.append(part, style=(self.color("danger") if "-" in part
                                      else self.main))
            return t
        if e.role == "opt":
            num, _, rest = e.text.partition(". ")
            t = Text("    ")
            t.append(num + ". ", style=self.color("warn"))
            t.append(rest, style="white")
            return t
        color = self.emotion_color(e.emotion)
        t = Text()
        t.append(f"  {self.name} ", style=f"bold {color}")
        t.append("「" + e.text + "」", style=color)
        return t

    def footer(self, hints):
        """폭에 맞춰 넣을 수 있는 것까지만. 줄바꿈되면 지저분하다."""
        avail = max(20, console.width - 2)

        def build(with_desc):
            t, used = Text(), 0
            for h in hints:
                piece = h.command + (" " + h.description
                                     if with_desc and h.description else "")
                need = term.width(piece) + (3 if used else 0)
                if used + need > avail:
                    break
                if used:
                    t.append("   ", style=self.dim_color)
                t.append(h.command, style=f"bold {self.main}")
                if with_desc and h.description:
                    t.append(" " + h.description, style=self.dim_color)
                used += need
            return t

        full = build(True)
        if term.width(full.plain) <= avail:
            return Group(Rule(style=self.dim_color), full)
        return Group(Rule(style=self.dim_color), build(False))

    def wrap_header(self, st):
        """상태창을 감싸는 방식. 플러그인이 Panel 로 바꿔 끼운다."""
        return self.header(st)

    def frame(self, st: V.Status, entries, hints, *, animate=False,
              delay=0.028) -> None:
        """하단 고정 상태창 프레임.

        화면을 [채팅(아래로 붙여 쌓임)] / [구분선·힌트] / [상태창] 순서로
        통째로 다시 그리고, 커서를 상태창 아래 입력 줄에 둔다.
        채팅이 길어져도 상태창은 항상 화면 하단에 남는다.

        animate=True 면 마지막 대사를 제자리에서 한 글자씩 찍는다.
        """
        w, h = console.width, console.height
        overhead = self.PANEL_ROWS + self.FRAME_EXTRA
        avail = max(3, h - overhead)

        rows, anim = [], None
        last_line = max((i for i, e in enumerate(entries)
                         if e.role == "rei"), default=None)
        for i, e in enumerate(entries):
            t = self.entry_text(e)
            wrapped = t.wrap(console, w) or [Text("")]
            if (animate and i == last_line and len(wrapped) == 1
                    and sys.stdout.isatty() and h - overhead >= 3):
                color = self.emotion_color(e.emotion)
                anim = [len(rows), "「" + e.text + "」", color]
                rows.append(Text(f"  {self.name} ", style=f"bold {color}"))
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
        console.print(self.footer(hints))
        console.print(self.wrap_header(st))

        if anim:
            idx, quoted, color = anim
            up = avail + 2 + self.PANEL_ROWS - idx
            term.cursor_up(up)
            console.print(Text(f"  {self.name} ", style=f"bold {color}"),
                          end="")
            term.type_inline(quoted, color, delay)
            term.cursor_down(up)

    def prompt_area(self, hints) -> None:
        console.print()
        console.print(self.footer(hints))

    # ── 목록 화면 ──────────────────────────────────────────────────────
    def shop(self, sv: V.ShopView) -> None:
        """선물·데이트 목록. 둘이 모양이 같아서 하나로 그린다."""
        self.blank()
        self.notice(sv.title)
        for row in sv.rows:
            price_color = (self.color("money") if row.affordable
                           else self.dim_color)
            mark = f"  (준 적 있음 ×{row.given})" if row.given else ""
            console.print(
                Text("    " + term.pad(row.key, 11), style=self.main) +
                Text(term.pad(row.name, 22), style="white") +
                Text(term.pad(f"{sv.currency_symbol} {row.price}", 8),
                     style=price_color) +
                Text(mark, style=self.dim_color))
        if sv.locked:
            self.blank()
            self.dim("잠김: " + ", ".join(
                f"{r.name}(호감도 {r.need})" for r in sv.locked[:4]))
        if sv.hint:
            self.blank()
            self.dim(sv.hint)

    def status(self, sv: V.StatusView) -> None:
        self.blank()
        self.notice(f"상대 — {sv.player}")
        self.blank()
        self.notice(f"{sv.char_name}가 이 사람을 어떻게 여기는가")
        for axis in sv.axes:
            good = axis.value >= axis.warn_below
            console.print(
                Text(f"    {axis.label}  ", style=self.dim_color) +
                self.gauge(axis.value, width=24,
                           color=self.main if good else self.color("danger")) +
                Text(f"  {axis.value:>3}", style="white"))
        self.blank()
        if sv.impression:
            console.print(Text(f"    {sv.char_name}의 판단  ",
                               style=self.dim_color) +
                          Text(f"「{sv.impression}」", style=self.main))
        else:
            self.dim("    아직 이 사람을 판단하지 않았다.")
        if sv.doubts:
            console.print(Text("    걸리는 것    ", style=self.dim_color) +
                          Text(f"「{sv.doubts}」", style=self.color("danger")))
        if sv.broken_promises:
            self.blank()
            self.notice("지키지 않은 약속", "danger")
            for text, days in sv.broken_promises[:5]:
                console.print(Text(f"    {text}  ", style="white") +
                              Text(f"({days}일 지났다)",
                                   style=self.color("danger")))
        self.blank()
        self.notice("근무 기록")
        for d in sv.work_days:
            console.print(
                Text(f"    {d.day}  ", style=self.dim_color) +
                Text(f"도구 {d.tools:>4}  수정 {d.edits:>3}  "
                     f"커밋 {d.commits:>3}  실패 {d.fails:>3}  ",
                     style="white") +
                Text(f"{sv.currency_symbol} {d.money:>5}",
                     style=self.color("money")))
        self.blank()
        self.notice("호감도 변화 (최근)")
        if not sv.ledger:
            self.dim("아직 없다.")
        for r in sv.ledger:
            style = self.main if r.delta > 0 else self.color("danger")
            sign = "+" if r.delta > 0 else ""
            console.print(
                Text(f"    {r.when}  ", style=self.dim_color) +
                Text(f"{sign}{r.delta:>3}  ", style=style) +
                Text(f"{r.kind} — {r.reason}", style="white"))
        self.blank()
        self.notice(
            f"총 획득 {sv.currency_symbol} {sv.total_earned:,}   ·   "
            f"보유 {sv.currency_symbol} {sv.money:,}   ·   "
            f"만난 횟수 {sv.met_count}")

    def memory(self, mv: V.MemoryView) -> None:
        self.blank()
        self.notice(f"{mv.char_name}가 기억하는 것")
        if not mv.rows:
            self.dim("아직 아무것도.")
        for m in mv.rows:
            console.print(
                Text(f"    {m.date}  ", style=self.dim_color) +
                Text("♥" * m.weight + "·" * max(0, 5 - m.weight) + "  ",
                     style=self.color("danger")) +
                Text(term.pad(f"[{m.kind}]", 10), style=self.dim_color) +
                Text(m.text, style="white"))
        if mv.pending:
            self.blank()
            self.dim(f"아직 정리되지 않은 대화 {mv.pending}줄")

    def worklog(self, wv: V.WorklogView) -> None:
        self.blank()
        self.notice(f"{wv.char_name}가 단말로 보고 있는 것 — 오늘")
        if wv.today:
            for line in wv.today:
                console.print(Text("  " + line, style="white"))
        else:
            self.dim("오늘은 아직 아무 기록도 없다.")
        if wv.past:
            self.blank()
            self.notice("지난 며칠")
            for day, text in wv.past:
                console.print(Text(f"    {day}  ", style=self.dim_color) +
                              Text(text, style="white"))

    def help(self, hv: V.HelpView) -> None:
        self.blank()
        self.notice("명령")
        for cmd, desc in hv.rows:
            console.print(Text("    " + term.pad(cmd, 16), style=self.main) +
                          Text(desc, style=self.dim_color))
        self.blank()
        for note in hv.notes:
            self.dim(note)

    # ── 메뉴 (설정 화면 전부) ──────────────────────────────────────────
    def menu(self, mv: V.MenuView):
        """메뉴 하나를 그리고 고른 항목의 key 를 돌려준다.

        돌려주는 값: 항목의 key / mv.back_key / "quit" / None(취소)
        """
        console.clear()
        self.blank()
        console.print(Text(f"  {mv.title}", style=f"bold {self.main}"))
        if mv.subtitle:
            console.print(Text(f"  {mv.subtitle}", style=self.dim_color))
        self.blank()

        for tone, text in mv.notes:
            self.line(text, tone)
        if mv.notes:
            self.blank()

        keyed = {}
        for item in mv.items:
            keyed[item.key.lower()] = item
            row = Text("    ")
            if item.disabled:
                row.append(f"{item.key}. ", style=self.dim_color)
                row.append(term.pad(item.label, 26), style=self.dim_color)
            else:
                row.append(f"{item.key}. ", style=self.color("warn"))
                row.append(term.pad(item.label, 26),
                           style=self.color(item.tone)
                           if item.tone != "plain" else "white")
            if item.value:
                row.append(item.value, style=self.main if not item.disabled
                           else self.dim_color)
            console.print(row)
            detail = item.disabled_reason if item.disabled else item.note
            if detail:
                console.print(Text("       " + detail, style=self.dim_color))

        self.blank()
        self.dim(mv.hint)
        self.blank()

        while True:
            raw = term.ask_line("  > ", rgb=(111, 119, 131))
            if raw is None:
                return None
            raw = raw.strip().lower()
            if not raw:
                return None
            if raw in ("q", "quit", "나감"):
                return "quit"
            if raw == mv.back_key:
                return mv.back_key
            item = keyed.get(raw)
            if item is None:
                self.dim("그런 항목은 없다.")
                continue
            if item.disabled:
                self.line(item.disabled_reason or "지금은 고를 수 없다.",
                          "danger")
                continue
            return item.key

    def confirm(self, prompt, phrase) -> bool:
        """되돌릴 수 없는 것의 확인. 정확히 phrase 를 쳐야 통과."""
        self.blank()
        self.line(prompt, "danger")
        self.dim(f"계속하려면  {phrase}  라고 입력한다. 아니면 엔터.")
        return term.confirm_phrase("  > ", phrase)

    def pause(self, text="엔터를 눌러 계속…") -> None:
        try:
            console.input(Text(f"\n  {text}", style=self.dim_color))
        except (EOFError, KeyboardInterrupt):
            console.print()
