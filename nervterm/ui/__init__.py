# -*- coding: utf-8 -*-
"""활성 UI 플러그인으로 가는 창구.

게임 코드는 `from . import ui` 하고 `ui.notice(...)` 처럼 쓴다.
그 호출은 전부 지금 켜져 있는 UI 플러그인 인스턴스로 넘어간다.

    ui.load(world)      설정에서 고른 UI 를 올린다
    ui.active()         인스턴스
    ui.notice(...)      active().notice(...) 와 같다

UI 플러그인은 BaseUI 를 상속한 클래스 `UI` 를 내놓거나,
`create(world) -> BaseUI` 를 내놓는다.
"""
from .. import plugins, settings
from .base import BaseUI, console            # noqa: F401
from . import view                            # noqa: F401

_active = None
LOAD_ERROR = ""


def _instantiate(plug, world):
    module = plug.module()
    if module is None:
        return None, plug.error

    factory = getattr(module, "create", None)
    if callable(factory):
        try:
            got = factory(world)
        except Exception as exc:                              # noqa: BLE001
            return None, f"create() 가 실패했다: {type(exc).__name__}: {exc}"
    else:
        klass = getattr(module, "UI", None)
        if klass is None:
            return None, "UI 클래스도 create() 도 없다"
        try:
            got = klass(world=world)
        except Exception as exc:                              # noqa: BLE001
            return None, f"UI() 가 실패했다: {type(exc).__name__}: {exc}"

    if not isinstance(got, BaseUI):
        return None, "BaseUI 를 상속하지 않았다"
    return got, ""


def load(world=None, *, refresh: bool = False):
    """설정에서 고른 UI 를 올린다. 못 올리면 기본 렌더러로 떨어진다."""
    global _active, LOAD_ERROR
    if _active is not None and not refresh:
        return _active

    wanted = settings.get("plugins.ui") or "nerv"
    plug, why = plugins.resolve("ui", wanted, fallback="nerv")
    LOAD_ERROR = why

    if plug is None:
        # UI 플러그인이 하나도 없다 — 밋밋해도 게임은 돌아야 한다.
        _active = BaseUI(world=world)
        return _active

    got, problem = _instantiate(plug, world)
    if got is None:
        LOAD_ERROR = f"{plug.id}: {problem}"
        _active = BaseUI(world=world)
        return _active

    _active = got
    return _active


def active():
    return _active if _active is not None else load()


def available():
    return plugins.by_kind("ui")


def current_id() -> str:
    return settings.get("plugins.ui") or "nerv"


def __getattr__(name):
    """ui.<무엇이든> → active().<무엇이든>

    이게 없으면 호출부마다 ui.active().notice(...) 라고 써야 한다.
    """
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(active(), name)
