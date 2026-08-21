# -*- coding: utf-8 -*-
"""플러그인이 지켜야 할 계약 — 캐릭터와 세계관.

플러그인은 이 모듈만 임포트하면 된다. 게임 내부(db·economy 따위)는
건드리지 않는다.

    from nervterm.spec import Character, World

계약을 여기 적어 두는 이유는 하나다. 예전에는 Character 가 그냥
속성 주머니라, 필드 하나를 빠뜨리면 게임 도중에 AttributeError 가
났다. 이제는 로드하는 순간 어느 필드가 없는지 말해 준다.
"""


class SpecError(ValueError):
    """플러그인이 계약을 어겼다."""


# ═══════════════════════════════════════════════════════════════════════
#  캐릭터
# ═══════════════════════════════════════════════════════════════════════
#
# (필드, 설명) — 없으면 로드가 거부된다.
CHARACTER_REQUIRED = [
    ("id",       "영문 식별자. 저장소의 키가 된다"),
    ("name",     "짧은 호칭. 대사 앞에 붙는다"),
    ("full",     "전체 이름"),
    ("core",     "페르소나 본문. 시스템 프롬프트의 뿌리"),
    ("start",    "시작 수치 dict(affection/trust/interest/patience)"),
    ("theme",    "색 dict(main/stage/emotion)"),
    ("stages",   "[(하한, 이름, 태도 지침)] — 호감도 단계"),
    ("tone",     "{trust/interest/patience: [(하한, 지침)]}"),
    ("fallback", "{단계 인덱스: [(narration, line, emotion)]}"),
    ("cold",     "{no_patience/no_interest/no_trust/boring: [...]}"),
    ("gifts",    "{key: (이름, 가격, 최소호감, 기본호감, 의미)}"),
    ("dates",    "{key: (이름, 가격, 최소호감, 장면 설정)}"),
]

# 없어도 되는 것 — 기본값이 채워진다.
CHARACTER_OPTIONAL = {
    "ja": "",
    "en": "",
    "neglect_lines": ("…오랜만이네.",),
    "danger_lines": ("그건 위험해.",),
    "refusal": {},
    "refusal_trust": ("", "…아직은 아니야."),
    "refusal_default": ("", "…아니야."),
    "greet_narr": ("",),
}

EMOTIONS = ("neutral", "slight", "warm", "cold",
            "curious", "shaken", "annoyed", "distant")

AXES = ("affection", "trust", "interest", "patience")


class Character:
    """한 사람. 플러그인이 이걸 만들어 CHARACTERS 로 내놓는다."""

    def __init__(self, **kw):
        for key, default in CHARACTER_OPTIONAL.items():
            kw.setdefault(key, default)
        self.__dict__.update(kw)
        # 팩 로더가 채운다 — 어느 팩에서 왔는지
        self.pack = kw.get("pack", "")

    def __repr__(self):
        return f"<Character {getattr(self, 'id', '?')}>"

    # 화면에 쓸 이름들. 없으면 한글 이름으로 대신한다.
    @property
    def display_ja(self):
        return getattr(self, "ja", "") or self.full

    @property
    def display_en(self):
        return getattr(self, "en", "") or self.id.upper()


def validate_character(char) -> None:
    """계약 위반이면 SpecError. 통과하면 조용히 돌아온다."""
    who = getattr(char, "id", None) or "(id 없음)"

    for field, why in CHARACTER_REQUIRED:
        if not hasattr(char, field) or getattr(char, field) in (None, ""):
            raise SpecError(f"{who}: '{field}' 가 없다 — {why}")

    if not isinstance(char.start, dict):
        raise SpecError(f"{who}: start 는 dict 여야 한다")
    for axis in AXES:
        if axis not in char.start:
            raise SpecError(f"{who}: start 에 '{axis}' 가 없다")

    theme = char.theme
    if not isinstance(theme, dict) or "main" not in theme:
        raise SpecError(f"{who}: theme 에 'main' 색이 없다")
    stage_colors = theme.get("stage") or []
    if len(stage_colors) < len(char.stages):
        raise SpecError(
            f"{who}: theme['stage'] 색이 {len(stage_colors)}개인데 "
            f"단계는 {len(char.stages)}개다")
    emo = theme.get("emotion") or {}
    missing = [e for e in EMOTIONS if e not in emo]
    if missing:
        # 빠진 감정은 조용히 neutral 로 떨어져 표현이 사라진다.
        # 그건 버그처럼 보이니 로드할 때 잡는다.
        raise SpecError(f"{who}: theme['emotion'] 에 {', '.join(missing)} 가 없다")

    if not char.stages or char.stages[0][0] != 0:
        raise SpecError(f"{who}: stages 는 하한 0 에서 시작해야 한다")
    last = -1
    for lo, name, guide in char.stages:
        if lo <= last:
            raise SpecError(f"{who}: stages 의 하한이 오름차순이 아니다 ({lo})")
        last = lo

    for axis in ("trust", "interest", "patience"):
        if axis not in (char.tone or {}):
            raise SpecError(f"{who}: tone 에 '{axis}' 가 없다")

    for key in ("no_patience", "no_interest", "no_trust", "boring"):
        if not (char.cold or {}).get(key):
            raise SpecError(f"{who}: cold['{key}'] 대사가 없다")

    if 0 not in (char.fallback or {}):
        raise SpecError(f"{who}: fallback[0] (첫 단계 대사) 이 없다")

    for key, item in (char.gifts or {}).items():
        if len(item) != 5:
            raise SpecError(
                f"{who}: gifts['{key}'] 는 (이름,가격,최소호감,기본호감,의미) "
                f"5개여야 한다 — 지금 {len(item)}개")
    for key, item in (char.dates or {}).items():
        if len(item) != 4:
            raise SpecError(
                f"{who}: dates['{key}'] 는 (이름,가격,최소호감,장면) "
                f"4개여야 한다 — 지금 {len(item)}개")


def stage_of(char, aff: int):
    """호감도 → (이름, 태도 지침, 단계 인덱스)"""
    idx = 0
    for i, (lo, _, _) in enumerate(char.stages):
        if aff >= lo:
            idx = i
    _, name, guide = char.stages[idx]
    return name, guide, idx


# ═══════════════════════════════════════════════════════════════════════
#  세계관
# ═══════════════════════════════════════════════════════════════════════
#
# 캐릭터는 '누구'고, 세계관은 '어디서 무엇을 하는 사람인가' 다.
# 재화 이름(LCL)과 플레이어의 역할(NERV 기술부 오퍼레이터)이 여기 있다.
# 이게 분리돼 있어야 다른 캐릭터 팩이 자기 세계를 데려올 수 있다.
WORLD_REQUIRED = [
    ("id",            "영문 식별자"),
    ("name",          "세계관 이름"),
    ("currency_name", "재화 이름 (예: LCL)"),
    ("player_role",   "플레이어가 누구인지 한 문장"),
]


class World:
    def __init__(self, **kw):
        kw.setdefault("currency_symbol", "¤")
        kw.setdefault("setting", "")
        kw.setdefault("terminal_name", "")
        kw.setdefault("work_framing", "")
        self.__dict__.update(kw)

    def __repr__(self):
        return f"<World {getattr(self, 'id', '?')}>"

    def prompt_block(self, char_name: str) -> str:
        """캐릭터 페르소나 뒤에 붙는 세계관 설명."""
        out = ["[세계]"]
        if self.setting:
            out.append(self.setting.strip())
        out.append(f"- 상대는 {self.player_role}.")
        if self.work_framing:
            out.append(f"- {self.work_framing}")
        else:
            out.append(f"- {char_name}는 상대의 근무 기록을 알고 있다. "
                       f"굳이 언급하지는 않지만, 물어보면 알고 있다고 한다.")
        return "\n".join(out)

    def money(self, amount) -> str:
        return f"{self.currency_symbol} {amount:,}"


def validate_world(world) -> None:
    who = getattr(world, "id", None) or "(id 없음)"
    for field, why in WORLD_REQUIRED:
        if not getattr(world, field, ""):
            raise SpecError(f"세계관 {who}: '{field}' 가 없다 — {why}")
