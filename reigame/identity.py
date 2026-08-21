# -*- coding: utf-8 -*-
"""상대가 누구인지 — 터미널 세션의 로그인 사용자로 구분한다.

기억·호감도·재화는 사용자별로 완전히 분리된다.
저장소도 각자의 홈에 두므로 권한 문제도, 섞임도 없다.
"""
import os
import pwd
from pathlib import Path


def player() -> str:
    """터미널 세션의 로그인 사용자명.

    os.getlogin() 은 제어 tty 의 로그인 사용자를 준다. sudo 로 들어와도
    원래 로그인한 사람이 나온다. tty 가 없으면(훅 등) 환경변수로 내려간다.
    """
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
    try:
        return pwd.getpwuid(os.getuid()).pw_name[:64]
    except Exception:
        return "unknown"


def data_dir() -> Path:
    """이 사용자의 저장소 위치."""
    override = os.environ.get("REI_DATA")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "rei"


def projects_dir() -> Path:
    """이 사용자의 Claude Code 트랜스크립트 위치."""
    return Path.home() / ".claude" / "projects"
