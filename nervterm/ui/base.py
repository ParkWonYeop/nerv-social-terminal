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
from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .. import term
from ..hangul import josa
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
        # Padding 으로 들여 쓴다 — 앞에 공백을 붙이면 좁은 창에서 줄바꿈된
        # 둘째 줄이 맨 왼쪽에서 시작했다.
        self.row(indent, (text, self.color(tone)))

    def notice(self, text, tone="warn") -> None:
        self.line(text, tone)

    def dim(self, text) -> None:
        self.row(2, (text, self.dim_color))

    def row(self, indent, *parts) -> None:
        """(글, 스타일) 조각들을 한 줄로 — 줄바꿈돼도 들여쓰기를 지킨다."""
        t = Text()
        for part in parts:
            if isinstance(part, Text):
                t.append_text(part)
            else:
                text, style = part
                t.append(str(text), style=style)
        console.print(Padding(t, (0, 0, 0, indent)))

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
        """캐릭터가 응답을 만드는 동안. 미리 타이핑해도 안 깨지게 에코를 끈다.

        몇 초가 넘으면 경과 시간과 그만두는 법을 보여 준다 — 로컬 모델은
        한 턴에 수십 초가 걸린다. 기다리는 사람은 지금 멈춘 건지 도는 건지
        알아야 한다.
        """
        who = name or self.name or "상대"
        start = time.monotonic()
        dim = self.dim_color

        class _Elapsed:
            def __rich__(self_):
                secs = int(time.monotonic() - start)
                t = Text(f"{josa(who, '이/가')} 대답을 생각한다…", style=dim)
                if secs >= 3:
                    t.append(f"   {secs}초 · Ctrl+C 그만두기", style=dim)
                return t

        with term.echo_off():
            with console.status(_Elapsed(), spinner="dots",
                                spinner_style=dim):
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
        items = []
        name_w = max([term.width(c.full) for c in sv.cards] + [8]) + 2
        ja_w = max([term.width(c.ja) for c in sv.cards] + [0]) + 2
        # 일본어 이름은 자리가 있을 때만 — 좁은 창에서 잘리면 호감 값과
        # 붙어 버렸다
        show_ja = console.width >= name_w + ja_w + 30
        for card in sv.cards:
            label = term.pad(card.full, name_w)
            if show_ja and card.ja:
                label += term.pad(card.ja, ja_w)
            mark = "  ●" if getattr(card, "attention", False) else ""
            items.append(V.MenuItem(
                key=card.id, label=label,
                value=f"호감 {card.affection:>3} · ◆ {card.stage}{mark}",
                note=getattr(card, "summary", "") or "",
                payload=("char", card.id)))
        if not items:
            items.append(V.MenuItem(
                key="-", label="만날 수 있는 사람이 없다", disabled=True,
                disabled_reason="설정에서 캐릭터를 켜거나 플러그인을 설치하라"))
        items.append(V.MenuItem(key="settings", label="설정",
                                tone="warn", payload=("settings", None)))
        items.append(V.MenuItem(key="quit", label="나간다",
                                tone="dim", payload=("quit", None)))

        got = self.choose(V.MenuView(
            title=sv.terminal_name or "단말",
            subtitle="인증됨.  누구를 만나러 왔나.",
            items=items, notes=list(sv.notes),
            hint="↑↓ 고르고 Enter.  Esc/q 나감",
            max_rows=14))
        if got is None or got.payload is None:
            return ("quit", None)
        return got.payload

    # ── 상점 고르기 ────────────────────────────────────────────────────
    def choose_shop(self, sv: V.ShopView):
        """선물·데이트를 골라서 ShopRow 를 돌려준다. 취소면 None."""
        items = []
        for row in sv.rows:
            mark = f"  (준 적 있음 ×{row.given})" if row.given else ""
            reason = getattr(row, "reason", "")
            items.append(V.MenuItem(
                key=row.key, label=row.name + mark,
                value=f"{sv.currency_symbol} {row.price}",
                tone="plain" if row.affordable and not reason else "dim",
                disabled=bool(reason) or not row.affordable,
                disabled_reason=reason or (
                    f"{sv.currency_symbol} {row.price} 필요 — "
                    f"보유 {sv.currency_symbol} {sv.money}"),
                note=" · ".join(x for x in (
                    getattr(row, "hint", ""),
                    f"준 적 있음 ×{row.given}" if row.given else "",
                    f"바로: {row.key}") if x),
                payload=row))
        for row in sv.locked:
            items.append(V.MenuItem(
                key=row.key, label=row.name,
                value=f"호감 {row.need} 필요",
                disabled=True,
                disabled_reason=f"아직 열리지 않았다 (호감 {row.need} 필요)",
                payload=row))
        if not items:
            return None
        got = self.choose(V.MenuView(
            title=sv.title,
            subtitle=f"보유 {sv.currency_symbol} {sv.money:,}",
            items=items, hint="↑↓ 고르고 Enter.  Esc 취소", max_rows=12))
        return got.payload if got is not None else None

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
        # 단계는 ◆ 로 — 단계 이름('신뢰', '관심')이 아래 수치 이름과 같아
        # `[신뢰]  신뢰 40` 처럼 같은 말이 나란히 섰다
        line2.append(f"   ◆ {st.stage}", style=color)

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
        grid.add_row(line3, self.today_text(st) if roomy else Text(""))
        grid.add_row(line4, streak)
        return grid

    def today_text(self, st: V.Status):
        """오늘 — 대화로 오른 호감 / 하루 예산, 약속, 돌봄, 데이트.

        새 규칙(하루 예산·약속·돌봄)은 화면에 안 보이면 없는 것과 같다.
        """
        dim = self.dim_color
        t = Text()
        bits = []
        if getattr(st, "talk_max", 0):
            full = st.talk_today >= st.talk_max
            bits.append((f"오늘 대화 +{st.talk_today}/{st.talk_max}",
                         self.color("warn") if full else dim))
        if getattr(st, "promises_open", 0):
            bits.append((f"약속 {st.promises_open}", self.main))
        if getattr(st, "care_days", -1) >= 0:
            left = "오늘까지" if st.care_days == 0 else f"{st.care_days}일"
            bits.append((f"돌봄 {left}", self.main))
        if getattr(st, "dates_max", 0) and st.dates_today:
            bits.append((f"데이트 {st.dates_today}/{st.dates_max}", dim))
        for i, (text, style) in enumerate(bits):
            if i:
                t.append(" · ", style=dim)
            t.append(text, style=style)
        return t

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

    # 폭이 모자라도 이건 꼭 남긴다 — 예전에는 뒤에서부터 잘려서 정작
    # 도움말과 나가기가 먼저 사라졌다(100칸에서 /memory 까지만 보였다)
    ESSENTIAL_HINTS = ("/help", "/quit")

    def footer(self, hints):
        """폭에 맞춰 넣을 수 있는 것까지만. 줄바꿈되면 지저분하다."""
        avail = max(20, console.width - 2)
        must = [h for h in hints if h.command in self.ESSENTIAL_HINTS]
        rest = [h for h in hints if h.command not in self.ESSENTIAL_HINTS]

        def piece(h, with_desc):
            return h.command + (" " + h.description
                                if with_desc and h.description else "")

        def fit(with_desc):
            tail = sum(term.width(piece(h, with_desc)) + 3 for h in must)
            used, chosen = 0, []
            for h in rest:
                need = term.width(piece(h, with_desc)) + 3
                if used + need + tail + 2 > avail:
                    break
                chosen.append(h)
                used += need
            return chosen

        def build(chosen, with_desc):
            t = Text()
            shown = chosen + must
            for i, h in enumerate(shown):
                if i:
                    t.append("   ", style=self.dim_color)
                if i == len(chosen) and len(chosen) < len(rest):
                    t.append("…   ", style=self.dim_color)
                t.append(h.command, style=f"bold {self.main}")
                if with_desc and h.description:
                    t.append(" " + h.description, style=self.dim_color)
            return t

        with_desc = fit(True)
        if len(with_desc) == len(rest):
            return Group(Rule(style=self.dim_color), build(with_desc, True))
        bare = fit(False)
        return Group(Rule(style=self.dim_color), build(bare, False))

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
        dim, main = self.dim_color, self.main
        narrow = console.width < 72
        gauge_w = 12 if narrow else 24
        self.blank()
        self.notice(f"상대 — {sv.player}")
        self.blank()
        self.notice(f"{josa(sv.char_name, '이/가')} 이 사람을 어떻게 여기는가")
        for axis in sv.axes:
            good = axis.value >= axis.warn_below
            self.row(4, (f"{axis.label}  ", dim),
                     self.gauge(axis.value, width=gauge_w,
                                color=main if good else self.color("danger")),
                     (f"  {axis.value:>3}", "white"))
        self.blank()
        if sv.impression:
            self.row(4, (f"{sv.char_name}의 판단  ", dim),
                     (f"「{sv.impression}」", main))
        else:
            self.row(4, ("아직 이 사람을 판단하지 않았다.", dim))
        if sv.doubts:
            self.row(4, ("걸리는 것    ", dim),
                     (f"「{sv.doubts}」", self.color("danger")))
        if sv.open_promises:
            self.blank()
            self.notice("지키는 중인 약속")
            for text, how, days in sv.open_promises[:5]:
                when = "오늘" if not days else f"{days}일 전"
                self.row(4, (f"{text}  ", "white"),
                         (f"({when}" + (f" · {how}" if how else "") + ")", dim))
        if sv.kept_promises:
            self.blank()
            self.notice("지킨 약속", "good")
            for text in sv.kept_promises[:3]:
                self.row(4, (text, self.color("good")))
        if sv.broken_promises:
            self.blank()
            self.notice("지키지 않은 약속", "danger")
            for text, days in sv.broken_promises[:5]:
                self.row(4, (f"{text}  ", "white"),
                         (f"({days}일 지났다)", self.color("danger")))
        if sv.care:
            self.blank()
            self.notice("챙겨 주는 중")
            for line in sv.care:
                self.row(4, (line, main))
        self.blank()
        self.notice("근무 기록")
        for d in sv.work_days:
            day = d.day[5:] if narrow else d.day
            body = (f"도구 {d.tools:>4} 커밋 {d.commits:>3} " if narrow else
                    f"도구 {d.tools:>4}  수정 {d.edits:>3}  "
                    f"커밋 {d.commits:>3}  실패 {d.fails:>3}  ")
            self.row(4, (f"{day}  ", dim), (body, "white"),
                     (f"{sv.currency_symbol} {d.money:>5}", self.color("money")))
        self.blank()
        self.notice("호감도 변화 (최근)")
        if not sv.ledger:
            self.dim("아직 없다.")
        for r in sv.ledger:
            style = main if r.delta > 0 else self.color("danger")
            sign = "+" if r.delta > 0 else ""
            self.row(4, (f"{r.when}  ", dim), (f"{sign}{r.delta:>3}  ", style),
                     (f"{r.kind} — {r.reason}", "white"))
        self.blank()
        self.notice(
            f"총 획득 {sv.currency_symbol} {sv.total_earned:,}   ·   "
            f"보유 {sv.currency_symbol} {sv.money:,}   ·   "
            f"만난 횟수 {sv.met_count}")

    def memory(self, mv: V.MemoryView) -> None:
        self.blank()
        self.notice(f"{josa(mv.char_name, '이/가')} 기억하는 것")
        if not mv.rows:
            self.dim("아직 아무것도.")
        for m in mv.rows:
            self.row(4, (f"{m.date}  ", self.dim_color),
                     ("♥" * m.weight + "·" * max(0, 5 - m.weight) + "  ",
                      self.color("danger")),
                     (term.pad(f"[{m.kind}]", 10), self.dim_color),
                     (m.text, "white"))
        if mv.pending:
            self.blank()
            self.dim(f"아직 정리되지 않은 대화 {mv.pending}줄")

    def worklog(self, wv: V.WorklogView) -> None:
        self.blank()
        self.notice(f"{josa(wv.char_name, '이/가')} 단말로 보고 있는 것 — 오늘")
        if wv.today:
            for line in wv.today:
                self.row(2, (line.strip(), "white"))
        else:
            self.dim("오늘은 아직 아무 기록도 없다. 체크한 에이전트로 일하면 쌓인다.")
        if wv.events:
            self.blank()
            self.notice("눈에 띄는 일")
            for line in wv.events:
                self.row(4, (line, self.main))
        if wv.past:
            self.blank()
            self.notice("지난 며칠")
            for day, text in wv.past:
                self.row(4, (f"{day}  ", self.dim_color), (text, "white"))

    def help(self, hv: V.HelpView) -> None:
        self.blank()
        self.notice("명령")
        cmd_w = max([term.width(c) for c, _ in hv.rows] + [8]) + 2
        for cmd, desc in hv.rows:
            self.row(4, (term.pad(cmd, cmd_w), self.main),
                     (desc, self.dim_color))
        self.blank()
        for note in hv.notes:
            self.dim(note)

    def onboarding(self, hv: V.HelpView) -> None:
        """처음 켰을 때 한 번 — 이게 뭐고 어떻게 굴러가는지."""
        console.clear()
        self.blank()
        self.notice("처음 오셨다.", "info")
        self.blank()
        w = max([term.width(c) for c, _ in hv.rows] + [8]) + 2
        for cmd, desc in hv.rows:
            self.row(4, (term.pad(cmd, w), self.main), (desc, "white"))
        self.blank()
        for note in hv.notes:
            self.dim(note)
        self.page_footer("아무 키나 누르면 시작한다")
        term.read_key()

    def log(self, lv: V.LogView) -> None:
        """이번 접속에서 나눈 말 전부."""
        self.blank()
        self.notice(f"{josa(lv.char_name, '과/와')} 나눈 말 — 이번 접속")
        if not lv.entries:
            self.dim("아직 아무 말도 없다.")
        for e in lv.entries:
            console.print(self.entry_text(e))

    def page_footer(self, text="아무 키나 누르면 돌아간다") -> None:
        self.blank()
        console.print(Text("  " + text, style=self.dim_color))

    # ── 선택기 ─────────────────────────────────────────────────────────
    #
    # 화살표로 움직이고 Enter 로 고른다. 게임 안의 모든 선택이 이걸 쓴다 —
    # 캐릭터 선택, 설정, 선물, 데이트, 데이트 중의 행동 선택지까지.
    #
    # TTY 가 아니면(파이프·시험) 목록을 찍고 줄 입력으로 떨어진다.
    # 번호나 항목 이름을 받는다.
    CURSOR = "▸"
    LABEL_WIDTH = 26

    def choose(self, mv: V.MenuView):
        """항목 하나를 고른다.

        돌려주는 값: 고른 MenuItem / None(취소).
        input_mode 항목을 골랐으면 item.typed 에 입력한 글자가 담긴다.
        """
        if not mv.items:
            return None
        if not term.is_tty():
            return self._choose_by_line(mv)
        return self._choose_by_arrow(mv)

    # ── 화살표 ─────────────────────────────────────────────────────────
    @staticmethod
    def _step(mv, idx, step):
        """구분줄을 건너뛰며 커서를 옮긴다."""
        n = len(mv.items)
        for _ in range(n):
            idx = (idx + step) % n
            if not getattr(mv.items[idx], "separator", False):
                return idx
        return idx

    @staticmethod
    def _jump(mv, key):
        """숫자 키 → 그 번호를 단 항목. 없으면 위에서 n번째(구분줄 빼고).

        자리로만 셌을 때는 '9 초기화' 가 일곱째 줄이라 9를 눌러도 안 갔고,
        구분줄이 끼면 번호가 하나씩 밀렸다.
        """
        for i, item in enumerate(mv.items):
            if item.key == key and not getattr(item, "separator", False):
                return i
        picks = [i for i, item in enumerate(mv.items)
                 if not getattr(item, "separator", False)]
        n = int(key) - 1
        return picks[n] if 0 <= n < len(picks) else None

    def _label_width(self, mv, avail):
        """이름 칸 폭 — 가장 긴 이름에 맞춘다(값과 겹치지 않게)."""
        names = [term.width(i.label) for i in mv.items if i.value]
        values = [term.width(i.value) for i in mv.items if i.value]
        if not names:
            return self.LABEL_WIDTH
        want = max(names) + 2
        return max(8, min(want, avail - max(values) - 2))

    def _choose_by_arrow(self, mv: V.MenuView):
        idx = max(0, min(mv.start, len(mv.items) - 1))
        if getattr(mv.items[idx], "separator", False):
            idx = self._step(mv, idx, 1)
        top = 0
        window = max(1, min(mv.max_rows, len(mv.items)))
        drawn = 0

        if mv.clear:
            console.clear()
        self._chooser_head(mv)
        try:
            with term.cursor_hidden():
                while True:
                    top = self._window_top(idx, top, window, len(mv.items))
                    if drawn:
                        term.clear_lines(drawn)
                    drawn = self._chooser_body(mv, idx, top, window)

                    key = term.read_key()
                    if key is None:                  # TTY 를 잃었다
                        return self._choose_by_line(mv)
                    if key in (term.KEY_UP, "k"):
                        idx = self._step(mv, idx, -1)
                    elif key in (term.KEY_DOWN, "j"):
                        idx = self._step(mv, idx, 1)
                    elif key == term.KEY_PGUP:
                        idx = max(0, idx - window)
                    elif key == term.KEY_PGDN:
                        idx = min(len(mv.items) - 1, idx + window)
                    elif key == term.KEY_HOME:
                        idx = 0
                    elif key == term.KEY_END:
                        idx = len(mv.items) - 1
                    elif key in (term.KEY_ESC, term.KEY_LEFT):
                        return None
                    elif key == term.KEY_ENTER:
                        item = mv.items[idx]
                        if item.disabled or getattr(item, "separator", False):
                            continue          # 사유는 이미 화면에 떠 있다
                        if item.input_mode:
                            return self._ask_typed(item)
                        return item
                    elif isinstance(key, str) and key.isdigit():
                        got = self._jump(mv, key)
                        if got is not None:
                            idx = got
                    elif isinstance(key, str) and key.lower() in (
                            "q", mv.back_key):
                        return None
        except KeyboardInterrupt:
            console.print()
            return None

    def _window_top(self, idx, top, window, total):
        """커서가 창 밖으로 나가지 않게 창을 민다."""
        if idx < top:
            return idx
        if idx >= top + window:
            return idx - window + 1
        return max(0, min(top, max(0, total - window)))

    def _chooser_head(self, mv):
        """선택하는 동안 바뀌지 않는 부분. 한 번만 그린다."""
        self.blank()
        if mv.title:
            console.print(Text(f"  {mv.title}", style=f"bold {self.main}"))
        if mv.subtitle:
            console.print(Text(f"  {mv.subtitle}", style=self.dim_color))
        if mv.title or mv.subtitle:
            self.blank()
        for tone, text in mv.notes:
            self.line(text, tone)
        if mv.notes:
            self.blank()

    def _chooser_body(self, mv, idx, top, window):
        """커서에 따라 다시 그리는 부분. 그린 줄 수를 돌려준다."""
        rows = 0
        avail = max(20, console.width - 6)

        if top > 0:
            console.print(Text("    ⋯", style=self.dim_color))
            rows += 1

        label_w = self._label_width(mv, avail)
        for i in range(top, min(top + window, len(mv.items))):
            console.print(self._chooser_row(mv.items[i], i == idx, avail,
                                            label_w))
            rows += 1

        if top + window < len(mv.items):
            console.print(Text("    ⋯", style=self.dim_color))
            rows += 1

        # 커서가 놓인 항목의 설명 — 항상 한 줄을 차지해 높이를 고정한다
        cur = mv.items[idx]
        detail = cur.disabled_reason if cur.disabled else cur.note
        console.print(Text("    " + term.truncate(detail or "", avail),
                           style=self.color("danger") if cur.disabled
                           else self.dim_color))
        rows += 1

        self.blank()
        console.print(Text("  " + mv.hint, style=self.dim_color))
        rows += 2
        return rows

    def _chooser_row(self, item, selected, avail, label_w=None):
        """한 줄. 반드시 한 줄이어야 한다 — 넘치면 잘라 낸다."""
        t = Text()
        if getattr(item, "separator", False):
            t.append("    ── " + item.label + " ──", style=self.dim_color)
            return t
        if selected:
            t.append(f"  {self.CURSOR} ", style=f"bold {self.main}")
        else:
            t.append("    ", style=self.dim_color)

        if item.disabled:
            label_style = self.dim_color
        elif selected:
            label_style = (f"bold {self.color(item.tone)}"
                           if item.tone != "plain" else "bold white")
        else:
            label_style = (self.color(item.tone)
                           if item.tone != "plain" else "white")

        label = term.pad(item.label, label_w or self.LABEL_WIDTH) if item.value \
            else item.label
        room = avail - term.width(item.value) - 2
        t.append(term.truncate(label, max(8, room)), style=label_style)
        if item.value:
            t.append(item.value,
                     style=self.dim_color if item.disabled else self.main)
        return t

    def _ask_typed(self, item):
        """input_mode 항목 — 직접 입력을 받는다."""
        self.blank()
        got = term.ask_line(item.input_prompt, rgb=(201, 138, 43))
        if not got:
            return None
        item.typed = got.strip()
        return item if item.typed else None

    # ── 줄 입력 대체 (비 TTY) ──────────────────────────────────────────
    def _choose_by_line(self, mv: V.MenuView):
        if mv.clear:
            console.clear()
        self._chooser_head(mv)
        keyed = {}
        n = 0
        for item in mv.items:
            if getattr(item, "separator", False):
                console.print(Text(f"    ── {item.label} ──",
                                   style=self.dim_color))
                continue
            n += 1
            i = n
            keyed[str(i)] = item
            keyed[item.key.lower()] = item
            row = Text("    ")
            row.append(f"{i}. ", style=self.color("warn")
                       if not item.disabled else self.dim_color)
            row.append(term.pad(item.label, self.LABEL_WIDTH),
                       style="white" if not item.disabled else self.dim_color)
            if item.value:
                row.append(item.value, style=self.dim_color)
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
            if not raw or raw in ("q", "quit", "나감", mv.back_key):
                return None
            item = keyed.get(raw)
            if item is None:
                self.dim("그런 항목은 없다.")
                continue
            if item.disabled:
                self.line(item.disabled_reason or "지금은 고를 수 없다.",
                          "danger")
                continue
            if item.input_mode:
                return self._ask_typed(item)
            return item

    # ── 메뉴 (설정 화면 전부) ──────────────────────────────────────────
    def menu(self, mv: V.MenuView):
        """설정 메뉴. 고른 항목의 key 를 돌려준다.

        돌려주는 값: 항목의 key / mv.back_key / "quit" / None(취소)
        """
        got = self.choose(mv)
        return got.key if got is not None else None

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
