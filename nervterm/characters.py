# -*- coding: utf-8 -*-
"""캐릭터 명부 — 플러그인에서 불러온다.

예전에는 이 파일에 레이·아스카·미사토가 통째로 들어 있었다. 지금은
`plugins/eva-characters/` 로 나갔고, 여기 남은 것은 '누가 설치돼 있고
누가 켜져 있는가' 뿐이다.

바깥에서 쓰는 이름은 그대로다:

    characters.IDS          설치된 캐릭터 전부 (저장소·훅이 쓴다)
    characters.ENABLED      설정에서 켜 둔 것만 (선택 화면이 쓴다)
    characters.get(id)      캐릭터 하나
    characters.stage_of()   호감도 → 단계

IDS 와 ENABLED 를 나눈 이유: 캐릭터를 껐다고 그 사람과의 관계가
사라지면 안 된다. 훅은 설치된 전원에게 반영하고, 화면에는 켜진
사람만 보인다. 다시 켜면 그대로 이어진다.
"""
from . import plugins, settings, spec
from .spec import Character, stage_of        # noqa: F401  (재수출)

ALL = {}            # id → Character
IDS = ()            # 설치된 전부
ENABLED = ()        # 켜 둔 것만
PACKS = {}          # pack_id → [Character]
LOAD_ERRORS = []    # [(출처, 사유)] — 설정 화면이 보여준다


def _load_pack(plug):
    """캐릭터 팩 하나를 읽는다. (캐릭터 목록, 사유) 를 돌려준다."""
    module = plug.module()
    if module is None:
        return [], plug.error

    found = getattr(module, "CHARACTERS", None)
    if found is None:
        return [], "CHARACTERS 목록이 없다"
    if not isinstance(found, (list, tuple)):
        return [], "CHARACTERS 는 목록이어야 한다"

    out, problems = [], []
    for char in found:
        try:
            spec.validate_character(char)
        except spec.SpecError as exc:
            problems.append(str(exc))
            continue
        char.pack = plug.id
        out.append(char)
    return out, "; ".join(problems)


def load(*, refresh: bool = False):
    """설치된 캐릭터 팩을 전부 읽어 명부를 다시 만든다."""
    global ALL, IDS, ENABLED, PACKS, LOAD_ERRORS
    ALL, PACKS, LOAD_ERRORS = {}, {}, []
    enabled = []

    for plug in plugins.by_kind("character"):
        chars, why = _load_pack(plug)
        if why:
            LOAD_ERRORS.append((plug.id, why))
        if not chars:
            continue
        PACKS[plug.id] = chars
        for char in chars:
            if char.id in ALL:
                LOAD_ERRORS.append(
                    (plug.id, f"'{char.id}' 는 이미 다른 팩에 있다 — 건너뜀"))
                continue
            ALL[char.id] = char
            if settings.character_enabled(plug.id, char.id):
                enabled.append(char.id)

    IDS = tuple(ALL)
    ENABLED = tuple(enabled)
    return ALL


def get(char_id: str):
    """캐릭터 하나. 없으면 첫 번째 캐릭터로 떨어진다."""
    if char_id in ALL:
        return ALL[char_id]
    return next(iter(ALL.values()), None)


def first_enabled():
    for cid in ENABLED:
        return ALL[cid]
    return next(iter(ALL.values()), None)


def pack_of(char_id: str) -> str:
    char = ALL.get(char_id)
    return getattr(char, "pack", "") if char else ""


def default_world() -> str:
    """켜진 캐릭터들이 전제하는 세계관. 팩 선언에서 읽는다."""
    for cid in ENABLED or IDS:
        plug = plugins.get("character", pack_of(cid))
        if plug is not None and plug.world:
            return plug.world
    return ""


load()
