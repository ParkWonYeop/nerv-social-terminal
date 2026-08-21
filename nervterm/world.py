# -*- coding: utf-8 -*-
"""세계관 — 재화의 이름과 플레이어의 역할.

캐릭터는 '누구'고, 세계관은 '어디서 무엇을 하는 사람인가' 다.
레이가 에반게리온의 인물이라는 건 캐릭터 쪽 사실이지만,
플레이어가 'NERV 제1지부 기술부 오퍼레이터'라는 건 세계 쪽 사실이다.
그래서 나눴다 — 다른 캐릭터 팩이 자기 세계를 데려올 수 있게.

    world.active()      지금 세계관
    world.money(120)    "¤ 120"
"""
from . import plugins, settings, spec

_active = None
LOAD_ERROR = ""

# 세계관 플러그인이 하나도 없을 때 쓰는 최소 세계.
# 게임이 안 켜지는 것보다는 밋밋하게라도 도는 게 낫다.
FALLBACK = spec.World(
    id="plain",
    name="이름 없는 곳",
    currency_name="크레딧",
    currency_symbol="¤",
    player_role="이 단말 앞에서 일하는 사람",
    setting="",
    terminal_name="단말",
)


def _load(plug):
    module = plug.module()
    if module is None:
        return None, plug.error
    found = getattr(module, "WORLD", None)
    if found is None:
        return None, "WORLD 가 없다"
    try:
        spec.validate_world(found)
    except spec.SpecError as exc:
        return None, str(exc)
    return found, ""


def load(*, refresh: bool = False):
    """설정에서 고른 세계관을 읽는다. 못 읽으면 대체품으로 떨어진다."""
    global _active, LOAD_ERROR
    if _active is not None and not refresh:
        return _active

    from . import characters
    wanted = settings.get("plugins.world") or characters.default_world()
    plug, why = plugins.resolve("world", wanted, fallback="nerv")
    LOAD_ERROR = why

    if plug is None:
        _active = FALLBACK
        return _active

    got, problem = _load(plug)
    if got is None:
        LOAD_ERROR = f"{plug.id}: {problem}"
        _active = FALLBACK
        return _active

    _active = got
    return _active


def active():
    return _active if _active is not None else load()


def money(amount) -> str:
    return active().money(amount)


def currency_name() -> str:
    return active().currency_name


def currency_symbol() -> str:
    return active().currency_symbol


def use(world_id: str):
    """세계관을 바꾼다(설정 화면용).

    화면에도 새 세계를 알려 줘야 한다. UI 는 시작할 때 받은 세계관
    객체를 들고 있어서, 여기서 갈아 끼우지 않으면 상태창의 재화는
    바뀌었는데 타이틀 카드만 옛 세계 이름으로 남는다.
    """
    global _active
    _active = None
    settings.put("plugins.world", world_id)
    got = load(refresh=True)
    try:
        from . import ui
        ui.set_world(got)
    except Exception:                                         # noqa: BLE001
        pass          # 화면이 아직 안 올라왔을 수도 있다
    return got


def available():
    """고를 수 있는 세계관 목록."""
    return plugins.by_kind("world")


def mismatches():
    """지금 세계관과 안 맞는 캐릭터들. [(캐릭터, 팩이 전제한 세계관)]

    캐릭터 팩은 자기가 전제하는 세계를 선언한다. 다른 세계를 골라 두면
    프롬프트에 엉뚱한 설정이 들어간다 — 에밀리아가 자기더러 제3신동경시에
    있다고 듣는 식이다. 막지는 않는다(섞어 쓰고 싶을 수도 있다).
    다만 모르고 그러는 일은 없어야 한다.
    """
    from . import characters
    here = active().id
    out = []
    for cid in characters.ENABLED:
        plug = plugins.get("character", characters.pack_of(cid))
        want = plug.world if plug is not None else ""
        if want and want != here:
            out.append((cid, want))
    return out
