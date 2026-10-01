# -*- coding: utf-8 -*-
"""Claude Code 훅 엔트리포인트.

철칙: 절대 stdout을 더럽히지 않고, 절대 0이 아닌 코드로 끝나지 않는다.
무슨 일이 있어도 Claude Code 본체의 작업을 방해하면 안 된다.
"""
import json
import os
import sys


def _log(msg: str, *, always: bool = False) -> None:
    if not always and not os.environ.get("REI_HOOK_DEBUG"):
        return
    try:
        from . import config
        config.log_path().parent.mkdir(parents=True, exist_ok=True)
        with open(config.log_path(), "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
        os.chmod(config.log_path(), 0o600)
    except Exception:
        pass


def _debug(msg: str) -> None:
    _log(msg)


def _note(msg: str) -> None:
    """디버그 플래그와 무관하게 남긴다 — 락 실패·느린 훅처럼 '조용한
    유실' 이 되기 쉬운 사건은 항상 관측 가능해야 한다."""
    import datetime
    _log(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}",
         always=True)


def _locked(exc) -> bool:
    import sqlite3
    return (isinstance(exc, sqlite3.OperationalError)
            and "locked" in str(exc).lower())


def _tool_ok(event: str, payload: dict) -> bool:
    if event == "PostToolUseFailure":
        return False
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("isError"):
            return False
        if resp.get("interrupted"):
            return False
    return True


# 도구 호출에서 생긴 일 → 상태줄 한 마디의 종류. 앞에 있는 것이 이긴다.
_QUIP_ORDER = (
    (("danger", None), "danger"),
    (("event", "recovered"), "recovered"),
    (("event", "new_project"), "new_project"),
    (("event", "late_night"), "late"),
    (("commit", None), "commit"),
    (("fail", None), "fail"),
)


def _quip_kind(results) -> str:
    for (kind, value), quip in _QUIP_ORDER:
        for k, v in results:
            if k == kind and (value is None or v == value):
                return quip
    return ""


def _run(payload: dict) -> None:
    from . import db, economy, widget

    event = payload.get("hook_event_name", "")
    sid = payload.get("session_id", "") or ""

    with db.session(write=True) as con:
        # 캐릭터 데이터는 안 쓴다 — 플러그인을 읽지 않는다.
        db.init(con, with_characters=False)

        if event in ("PostToolUse", "PostToolUseFailure"):
            tool = payload.get("tool_name", "") or ""
            ti = payload.get("tool_input") or {}
            economy.roll_day(con)
            results = economy.on_tool(
                con, tool=tool, tool_input=ti if isinstance(ti, dict) else {},
                tool_response=payload.get("tool_response"),
                ok=_tool_ok(event, payload), session_id=sid,
                cwd=str(payload.get("cwd") or ""),
            )
            economy.touch_activity(con)
            kind = _quip_kind(results)
            if kind:
                widget.quip(con, kind, force=(kind == "danger"))
            _debug(f"{event} {tool} -> {results}")

        elif event == "Stop":
            economy.roll_day(con)
            got = economy.on_stop(con, sid)
            economy.touch_activity(con)
            widget.quip(con, "stop")
            _debug(f"Stop -> +{got}")

        elif event == "SessionStart":
            # 방치는 여기서 매기지 않는다 — 그 캐릭터를 찾아갔을 때
            # 게임이 '마지막으로 만난 뒤' 를 기준으로 매긴다.
            streak, bonus = economy.roll_day(con)
            economy.touch_activity(con)
            _debug(f"SessionStart streak={streak}/+{bonus}")

        elif event == "SessionEnd":
            economy.touch_activity(con)
            _debug("SessionEnd")

        elif event == "Notification":
            # 에이전트가 사람을 기다린다(권한 요청 등). 작업 실적은 아니다.
            widget.quip(con, "notify")
            _debug("Notification")


def main() -> int:
    # 게임이 스스로 띄운 에이전트가 훅을 되돌려 발동시키지 않게.
    # 이게 없으면 대사 한 줄 만들 때마다 재화가 쌓이고, 캐릭터가
    # 자기 대사를 근무 실적으로 착각한다.
    # REI_GAME 은 옛 이름 — 이미 설치된 훅들이 아직 이걸 본다.
    if os.environ.get("NERV_GAME") or os.environ.get("REI_GAME"):
        return 0
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    import time
    t0 = time.monotonic()
    try:
        _run(payload)
    except Exception as exc:                                  # noqa: BLE001
        if _locked(exc):
            # 게임이 잠깐 쓰기 락을 쥔 순간과 겹쳤다 — 한 번만 더.
            # 그래도 안 되면 이번 적립은 버리되, 흔적은 남긴다.
            time.sleep(0.3)
            try:
                _run(payload)
                _note("LOCKED -> retry ok")
            except Exception as exc2:                         # noqa: BLE001
                _note(f"LOCKED retry failed: {exc2}")
        else:
            _note(f"ERROR {type(exc).__name__}: {exc}")
    took = time.monotonic() - t0
    if took > 2.0:
        # 훅이 이렇게 오래 걸리면 에이전트가 그만큼 멈춘 것이다.
        _note(f"SLOW {took:.1f}s {payload.get('hook_event_name', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
