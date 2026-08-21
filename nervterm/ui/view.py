# -*- coding: utf-8 -*-
"""뷰 모델 — game.py 가 만들고, UI 플러그인이 그린다.

경계를 여기 둔 이유: game.py 가 rich 의 Text 를 직접 조립하면
UI 플러그인은 색만 바꾸는 스킨이 된다. 게임이 '무엇을 보여줄지'만
정하고 '어떻게 보일지'는 전부 플러그인이 정하게 하려면, 그 사이에
색도 좌표도 없는 순수한 데이터가 하나 있어야 한다.

그래서 여기에는 색 이름이 없다. tone("info"/"warn"/"danger"/"good")
같은 의미만 있고, 그게 무슨 색인지는 플러그인이 정한다.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# 말의 톤. 플러그인이 각자의 팔레트로 옮긴다.
TONES = ("plain", "info", "good", "warn", "danger", "money")


# ═══════════════════════════════════════════════════════════════════════
#  대화 화면
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class Status:
    """상태창에 뜨는 것 전부."""
    player: str = "?"
    char_name: str = ""
    char_full: str = ""
    char_ja: str = ""
    char_en: str = ""

    affection: int = 0
    trust: int = 0
    interest: int = 0
    patience: int = 0
    stage: str = ""
    stage_idx: int = 0
    stage_guide: str = ""
    mood: str = "flat"

    money: int = 0
    currency_name: str = "LCL"
    currency_symbol: str = "¤"

    tools: int = 0
    edits: int = 0
    commits: int = 0
    streak: int = 0

    llm_used: int = 0
    llm_cap: int = 0
    llm_warn_at: int = 0
    provider_label: str = ""       # "claude-cli" 등. 헤더에 뜬다
    offline: bool = False
    billable: bool = False         # 유료 API 를 쓰는 중인가

    terminal_name: str = ""

    def money_text(self) -> str:
        return f"{self.currency_symbol} {self.money:,}"


@dataclass
class LogEntry:
    """대화 로그 한 줄."""
    role: str          # narr | user | rei | sys | inner | delta | opt
    text: str = ""
    emotion: str = ""


@dataclass
class Hint:
    """입력창 위 명령 힌트."""
    command: str
    description: str = ""


# ═══════════════════════════════════════════════════════════════════════
#  시작 화면
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class CharacterCard:
    id: str
    name: str
    full: str
    ja: str = ""
    en: str = ""
    affection: int = 0
    stage: str = ""
    color: str = ""            # 캐릭터 테마의 main 색 (플러그인이 참고)
    pack: str = ""


@dataclass
class SelectView:
    cards: list = field(default_factory=list)
    terminal_name: str = ""
    world_name: str = ""
    notes: list = field(default_factory=list)   # (tone, text)


# ═══════════════════════════════════════════════════════════════════════
#  목록 화면
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class ShopRow:
    key: str
    name: str
    price: int
    need: int = 0
    affordable: bool = True
    given: int = 0             # 선물만 — 준 횟수
    locked: bool = False


@dataclass
class ShopView:
    title: str
    rows: list = field(default_factory=list)
    locked: list = field(default_factory=list)      # ShopRow
    money: int = 0
    currency_symbol: str = "¤"
    hint: str = ""


@dataclass
class Axis:
    label: str
    value: int
    warn_below: int = 0


@dataclass
class WorkDay:
    day: str
    tools: int = 0
    edits: int = 0
    commits: int = 0
    fails: int = 0
    money: int = 0


@dataclass
class LedgerRow:
    when: str
    kind: str
    delta: int
    reason: str = ""


@dataclass
class StatusView:
    player: str
    char_name: str
    axes: list = field(default_factory=list)
    impression: str = ""
    doubts: str = ""
    broken_promises: list = field(default_factory=list)   # (text, days)
    work_days: list = field(default_factory=list)         # WorkDay
    ledger: list = field(default_factory=list)            # LedgerRow
    total_earned: int = 0
    money: int = 0
    met_count: int = 0
    currency_symbol: str = "¤"


@dataclass
class MemoryRow:
    date: str
    kind: str
    text: str
    weight: int = 1


@dataclass
class MemoryView:
    char_name: str
    rows: list = field(default_factory=list)
    pending: int = 0


@dataclass
class WorklogView:
    char_name: str
    today: list = field(default_factory=list)     # 문자열 줄
    past: list = field(default_factory=list)      # (day, text)


@dataclass
class HelpView:
    rows: list = field(default_factory=list)      # (명령, 설명)
    notes: list = field(default_factory=list)     # 문자열


# ═══════════════════════════════════════════════════════════════════════
#  메뉴 — 설정 화면 전부가 이거 하나를 쓴다
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class MenuItem:
    key: str                       # 사용자가 입력할 것 ("1", "back")
    label: str
    value: str = ""                # 오른쪽에 뜨는 현재 값
    note: str = ""                 # 아래 줄 설명
    tone: str = "plain"
    disabled: bool = False
    disabled_reason: str = ""
    action: Optional[Callable] = None
    payload: Any = None


@dataclass
class MenuView:
    title: str
    items: list = field(default_factory=list)
    subtitle: str = ""
    notes: list = field(default_factory=list)      # (tone, text)
    hint: str = "번호를 고른다.  b 뒤로  ·  q 나감"
    back_key: str = "b"
