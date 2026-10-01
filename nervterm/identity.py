# -*- coding: utf-8 -*-
"""상대가 누구인지 — 터미널 세션의 로그인 사용자로 구분한다.

기억·호감도·재화는 사용자별로 완전히 분리된다.
저장소도 각자의 홈에 두므로 권한 문제도, 섞임도 없다.
"""
import os
import pwd
from pathlib import Path


def player() -> str:
    """이 프로세스를 돌리는 사람.

    **실행 계정(uid)이 먼저다.** 예전에는 os.getlogin() 을 먼저 믿었다 —
    제어 tty 의 로그인 이름이라 sudo 로 들어와도 원래 사람이 나온다는
    이유였다. 그런데 tty 없이 데몬 아래에서 도는 프로세스(Codex 데스크톱이
    띄운 훅)에서는 macOS 가 'root' 를 돌려준다. 프로세스는 분명 내 계정으로
    도는데. 그래서 8월 말부터 Codex 로 한 일이 전부 'root' 라는 유령
    플레이어에게 적립됐다 — 실측 40,720 LCL, 작업 실적의 3분의 2.

    sudo 는 SUDO_USER 로 따로 본다(uid 가 0 일 때만).
    """
    if os.environ.get("REI_PLAYER"):
        return os.environ["REI_PLAYER"].strip()[:64]
    uid = os.getuid()
    if uid == 0 and os.environ.get("SUDO_USER"):
        return os.environ["SUDO_USER"].strip()[:64]
    try:
        name = pwd.getpwuid(uid).pw_name
        if name:
            return name[:64]
    except (KeyError, OSError):
        pass
    for key in ("LOGNAME", "USER", "USERNAME"):
        v = os.environ.get(key)
        if v:
            return v.strip()[:64]
    try:
        return os.getlogin()[:64]
    except OSError:
        return "unknown"


def data_dir() -> Path:
    """이 사용자의 저장소 위치.

    프로젝트가 rei 에서 nerv-social-terminal 로 개명되면서 저장소 폴더도
    바뀌었다. 옛 폴더가 있고 새 폴더가 없으면 자동으로 옮긴다 —
    기존 플레이 데이터(레이·아스카·미사토)가 그대로 유지된다.
    """
    override = os.environ.get("NERV_DATA") or os.environ.get("REI_DATA")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    new, legacy = root / "nerv-social-terminal", root / "rei"
    if not new.exists() and legacy.is_dir():
        try:
            legacy.rename(new)          # 같은 파일시스템 — 원자적
        except OSError:
            return legacy               # 못 옮기면 옛 자리를 계속 쓴다
    return new


def projects_dir() -> Path:
    """이 사용자의 Claude Code 트랜스크립트 위치."""
    return Path.home() / ".claude" / "projects"
