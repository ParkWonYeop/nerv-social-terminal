# -*- coding: utf-8 -*-
"""상태줄 위젯 — Claude Code 터미널 하단에 붙는 한두 줄.

TUI 를 띄우지 않고도 지금 상대가 어떤 상태인지 보인다.

**속도가 전부다.** 이 스크립트는 Claude Code 가 화면을 갱신할 때마다
돈다(300ms 디바운스). 훅은 도구 호출마다지만 이건 그보다 잦다.
그래서 여기서는 아무것도 무겁게 임포트하지 않는다 —
rich, 플러그인, 캐릭터 데이터, 설정 전부 건드리지 않고
sqlite 에서 필요한 값만 한 번에 읽는다.

세계관·캐릭터의 표시 이름은 DB 에 캐시해 둔 것을 쓴다. 게임이 켜질 때
넣어 두므로, 한 번도 안 켰으면 위젯도 조용히 아무것도 안 그린다.
"""
import os
import sqlite3
import sys

# ANSI. rich 를 부르지 않는다 — 그것만으로 30ms 가 든다.
RESET = "\x1b[0m"
DIM = "\x1b[38;5;244m"
WHITE = "\x1b[97m"


def _rgb(hex_color: str) -> str:
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return WHITE
    try:
        return f"\x1b[38;2;{int(h[0:2],16)};{int(h[2:4],16)};{int(h[4:6],16)}m"
    except ValueError:
        return WHITE


def _data_dir():
    override = os.environ.get("NERV_DATA") or os.environ.get("REI_DATA")
    if override:
        return os.path.expanduser(override)
    base = os.environ.get("XDG_DATA_HOME")
    root = os.path.expanduser(base) if base else os.path.expanduser(
        "~/.local/share")
    new = os.path.join(root, "nerv-social-terminal")
    return new if os.path.isdir(new) else os.path.join(root, "rei")


def _player():
    if os.environ.get("REI_PLAYER"):
        return os.environ["REI_PLAYER"].strip()[:64]
    try:
        name = os.getlogin()
        if name:
            return name[:64]
    except OSError:
        pass
    for key in ("SUDO_USER", "LOGNAME", "USER", "USERNAME"):
        v = os.environ.get(key)
        if v:
            return v.strip()[:64]
    return "unknown"


def _gauge(value, width=10, color=WHITE):
    filled = int(round(width * max(0, min(100, value)) / 100))
    return f"{color}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"


def read(con, player, char):
    """이 캐릭터의 상태를 한 번에 읽는다. 쿼리 두 번."""
    rows = dict(con.execute(
        "SELECT key,value FROM state WHERE player=? AND char IN ('',?)",
        (player, char)).fetchall())

    def num(key, default=0):
        try:
            return int(rows.get(key, default))
        except (TypeError, ValueError):
            return default

    last = con.execute(
        "SELECT text,emotion FROM dialogue WHERE player=? AND char=? "
        "AND role='rei' ORDER BY id DESC LIMIT 1", (player, char)).fetchone()
    return rows, num, last


def render(payload=None):
    """상태줄 문자열. 보여줄 게 없으면 빈 문자열."""
    db_path = os.path.join(_data_dir(), "rei.db")
    if not os.path.isfile(db_path):
        return ""
    player = _player()

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=0", uri=True,
                              timeout=0.4)
    except sqlite3.Error:
        return ""
    try:
        con.execute("PRAGMA query_only=ON")
        meta = dict(con.execute(
            "SELECT key,value FROM state WHERE player=? AND char=''",
            (player,)).fetchall())
        char = meta.get("widget_char") or ""
        if not char:
            return ""
        rows, num, last = read(con, player, char)
    except sqlite3.Error:
        return ""
    finally:
        con.close()

    name = rows.get("widget_name") or char
    color = _rgb(rows.get("widget_color") or "#9ec5e0")
    cur = rows.get("widget_currency") or "LCL"
    sym = rows.get("widget_symbol") or "¤"
    stage = rows.get("widget_stage") or ""

    aff = num("affection")
    out = []
    line1 = (
        f"{color}{name}{RESET}  {_gauge(aff, 10, color)} {WHITE}{aff:>3}{RESET}"
        f" {DIM}{stage}{RESET}"
        f"   {DIM}신뢰{RESET} {num('trust'):>3}"
        f" {DIM}관심{RESET} {num('interest'):>3}"
        f" {DIM}인내{RESET} {num('patience'):>3}"
        f"   {DIM}{cur}{RESET} {sym} {num('lcl'):,}"
    )
    out.append(line1)

    if last and last[0]:
        text = last[0].replace("\n", " ").strip()
        if len(text) > 58:
            text = text[:57] + "…"
        out.append(f"{color}「{text}」{RESET}")

    return "\n".join(out)


# ── 게임 쪽에서 캐시를 채운다 ──────────────────────────────────────────
#
# 위젯은 플러그인을 읽지 않는다(느리니까). 그래서 표시에 필요한 것들 —
# 이름·색·단계·재화 이름 — 을 게임이 돌 때 DB 에 적어 둔다.
# 위젯은 그걸 그대로 읽어 쓴다.
def remember(con, char, world, stage: str) -> None:
    """지금 누구를 만나고 있는지, 어떻게 표시할지 적어 둔다."""
    from . import db
    theme = getattr(char, "theme", None) or {}
    db.put(con, "widget_char", char.id, char="")
    for key, value in (
        ("widget_name", char.name),
        ("widget_color", theme.get("main", "")),
        ("widget_stage", stage),
        ("widget_currency", world.currency_name),
        ("widget_symbol", world.currency_symbol),
    ):
        db.put(con, key, value, char=char.id)


def main() -> int:
    # Claude Code 가 stdin 으로 세션 JSON 을 준다. 지금은 안 쓰지만
    # 읽어서 버려야 파이프가 막히지 않는다.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:                                         # noqa: BLE001
        pass
    try:
        got = render()
    except Exception:                                         # noqa: BLE001
        return 0          # 위젯 때문에 Claude Code 가 시끄러우면 안 된다
    if got:
        sys.stdout.write(got + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
