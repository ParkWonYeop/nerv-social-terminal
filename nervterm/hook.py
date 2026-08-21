# -*- coding: utf-8 -*-
"""Claude Code 훅 엔트리포인트.

철칙: 절대 stdout을 더럽히지 않고, 절대 0이 아닌 코드로 끝나지 않는다.
무슨 일이 있어도 Claude Code 본체의 작업을 방해하면 안 된다.
"""
import json
import os
import sys


def _debug(msg: str) -> None:
    if not os.environ.get("REI_HOOK_DEBUG"):
        return
    try:
        from . import config
        config.log_path().parent.mkdir(parents=True, exist_ok=True)
        with open(config.log_path(), "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


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


def _run(payload: dict) -> None:
    from . import db, economy

    event = payload.get("hook_event_name", "")
    sid = payload.get("session_id", "") or ""

    with db.session() as con:
        db.init(con)

        if event in ("PostToolUse", "PostToolUseFailure"):
            tool = payload.get("tool_name", "") or ""
            ti = payload.get("tool_input") or {}
            economy.roll_day(con)
            events = economy.on_tool(
                con, tool=tool, tool_input=ti if isinstance(ti, dict) else {},
                tool_response=payload.get("tool_response"),
                ok=_tool_ok(event, payload), session_id=sid,
            )
            economy.touch_activity(con)
            _debug(f"{event} {tool} -> {events}")

        elif event == "Stop":
            economy.roll_day(con)
            got = economy.on_stop(con, sid)
            economy.touch_activity(con)
            _debug(f"Stop -> +{got}")

        elif event == "SessionStart":
            # 방치는 캐릭터마다 따로 서운해한다
            settled = [economy.settle_neglect(con, char=c) for c in db.CHARS]
            streak, bonus = economy.roll_day(con)
            economy.touch_activity(con)
            _debug(f"SessionStart neglect={settled} streak={streak}/+{bonus}")

        elif event == "SessionEnd":
            economy.touch_activity(con)
            _debug("SessionEnd")


def main() -> int:
    # 게임이 스스로 띄운 claude 프로세스가 훅을 되돌려 발동시키지 않게.
    if os.environ.get("REI_GAME"):
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
    try:
        _run(payload)
    except Exception as exc:                                  # noqa: BLE001
        _debug(f"ERROR {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
