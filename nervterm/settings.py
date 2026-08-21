# -*- coding: utf-8 -*-
"""사용자 설정 — 설정 메뉴가 쓰고, config 가 읽는다.

저장 위치는 각자의 데이터 폴더(`~/.local/share/nerv-social-terminal/settings.json`).
플레이 데이터와 같은 곳에 있으니 사용자별로 자동 분리된다.

우선순위는 언제나 이 순서다:

    환경변수  >  이 설정 파일  >  config.py 의 기본값

환경변수를 위에 둔 이유는 테스트·일회성 실행 때문이다.
`NERV_OFFLINE=1 eva` 한 번 돌린 것이 저장된 설정을 덮어쓰면 곤란하다.
"""
import copy
import json
import os
import tempfile
from pathlib import Path

from . import identity

SCHEMA_VERSION = 2

DEFAULTS = {
    "version": SCHEMA_VERSION,

    # ── 대화 ───────────────────────────────────────────────────────────
    # 하루 LLM 호출 상한. 넘으면 사전 작성 대사로 자동 전환된다.
    "daily_llm_calls": 200,
    "animation": True,          # 타이핑 연출·부팅 연출
    "typing_speed": 0.028,      # 초/글자. 0 이면 즉시 출력

    # ── LLM 연결 ───────────────────────────────────────────────────────
    "llm": {
        # provider id — nervterm/llm/ 의 프로바이더 등록명
        "provider": "claude-cli",
        "model": "",            # 빈 문자열이면 프로바이더 기본값
        "base_url": "",         # 로컬/호환 서버용
        "api_key_env": "",      # 키를 읽을 환경변수 이름
        # 0 이면 프로바이더별 기본값 (로컬 모델은 훨씬 길다)
        "timeout": 0,

        # 과금 안전장치.
        #
        # True 인 동안에는 토큰당 과금이 발생하는 프로바이더(API 키를
        # 쓰는 것들)를 아예 고를 수 없다. 구독 좌석(claude-cli /
        # codex-cli)과 로컬 모델은 이 가드와 무관하게 쓸 수 있다 —
        # 계정 한도를 쓰는 것과 API 과금은 다른 것이다.
        "billing_guard": True,

        # 가드를 끈 뒤에도 남는 상한. 유료 프로바이더의 하루 호출 수.
        # 넘으면 과금을 더 내지 않고 사전 작성 대사로 떨어진다.
        "api_daily_call_cap": 50,
    },

    # ── 플러그인 ───────────────────────────────────────────────────────
    "plugins": {
        "ui": "nerv",           # 한 번에 하나만
        "world": "nerv",        # 세계관(재화 이름·플레이어 역할)
        # 캐릭터 활성 여부. {"pack_id": {"char_id": true/false}}
        # 여기 없는 캐릭터는 기본 활성으로 본다.
        "characters": {},
    },

    # ── 재화를 적립할 에이전트 ─────────────────────────────────────────
    # 훅이 설치된 에이전트에서 작업량을 가져온다. install-hooks.py 가
    # 실제 설치를 하고, 여기는 '세션 기록을 읽을 대상' 목록이다.
    "agents": {
        "claude": True,
        "codex": False,
    },
}

# 환경변수 → 설정 경로. 있으면 저장된 값을 이긴다.
ENV_OVERRIDES = {
    "NERV_DAILY_LLM_CALLS": ("daily_llm_calls", int),
    "REI_DAILY_LLM_CALLS": ("daily_llm_calls", int),      # 옛 이름
    "NERV_LLM_PROVIDER": ("llm.provider", str),
    "NERV_LLM_MODEL": ("llm.model", str),
    "NERV_LLM_BASE_URL": ("llm.base_url", str),
    "NERV_UI": ("plugins.ui", str),
    "NERV_WORLD": ("plugins.world", str),
}

_cache = None

# 서버 공통 설정. 관리자가 두면 모든 사용자의 기본값이 된다.
# 여기 `locked` 에 적힌 키는 사용자가 바꿀 수 없다.
SITE_PATH = Path("/etc/nerv-social-terminal/settings.json")


def _slug(name: str) -> str:
    """사용자 이름을 파일 이름으로 쓸 수 있게. 경로 문자를 막는다."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_"
                   for c in (name or "unknown"))
    return safe.strip("._")[:64] or "unknown"


def path() -> Path:
    """이 사용자의 설정 파일.

    저장소를 공유해도 섞이지 않도록 사용자 이름을 파일에 박는다.
    플레이 데이터는 모든 행에 player 가 함께 들어가 이미 분리돼 있었는데
    설정만 한 파일을 같이 쓰고 있었다 — 홈을 공유하면 남이 정한 상한과
    프로바이더가 그대로 적용됐다.
    """
    return identity.data_dir() / f"settings-{_slug(identity.player())}.json"


def site_path() -> Path:
    override = os.environ.get("NERV_SITE_SETTINGS")
    return Path(override).expanduser() if override else SITE_PATH


def _read(p: Path) -> dict:
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}          # 깨진 설정 때문에 게임이 안 켜지면 안 된다


def site() -> dict:
    """서버 공통 설정. 없으면 빈 dict."""
    return _read(site_path())


def locked_keys() -> list:
    """관리자가 잠근 설정 경로 목록.

    공용 서버에서 필요하다. 예를 들어 좌석 하나를 여럿이 나눠 쓰는데
    누군가 하루 상한을 5000 으로 올려 버리면 나머지가 굶는다.
    """
    got = site().get("locked")
    return [str(k) for k in got] if isinstance(got, list) else []


def is_locked(dotted: str) -> bool:
    return any(dotted == k or dotted.startswith(k + ".")
               for k in locked_keys())


def _migrate_shared_file() -> None:
    """옛 공용 settings.json 을 이 사용자 것으로 옮긴다.

    한 번만 일어난다. 이미 내 파일이 있으면 건드리지 않는다.
    공용 파일은 지우지 않는다 — 같은 저장소를 쓰던 다른 사용자도
    각자 처음 켤 때 자기 몫으로 가져가야 한다.
    """
    mine = path()
    if mine.exists():
        return
    old = identity.data_dir() / "settings.json"
    if not old.is_file():
        return
    try:
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


# ── 점 표기 경로 ───────────────────────────────────────────────────────
def _dig(tree: dict, dotted: str, default=None):
    cur = tree
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _plant(tree: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = tree
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _merge(base: dict, over: dict) -> dict:
    """over 를 base 위에 깊이 병합. 기본값에 없는 키도 보존한다."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


# ── 읽기 / 쓰기 ────────────────────────────────────────────────────────
def load(*, refresh: bool = False) -> dict:
    """지금 적용되는 설정 전체.

    네 층이 겹친다. 아래가 위를 덮는다.

        1. DEFAULTS          코드의 기본값
        2. 서버 공통 설정     관리자가 /etc 에 둔 것
        3. 이 사용자의 설정   설정 화면이 쓰는 것
        4. 잠긴 키           관리자가 못 바꾸게 한 것 — 사용자를 다시 덮는다

    4번이 3번 위에 오는 게 핵심이다. 잠근 값은 사용자가 설정 파일을
    손으로 고쳐도 적용되지 않는다.
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache

    _migrate_shared_file()
    common = site()
    stored = _migrate(_read(path()))

    tree = _merge(DEFAULTS, {k: v for k, v in common.items() if k != "locked"})
    tree = _merge(tree, stored)
    for key in locked_keys():
        got = _dig(common, key, None)
        if got is not None:
            _plant(tree, key, got)
    _cache = tree
    return _cache


def _migrate(raw: dict) -> dict:
    """옛 스키마를 올린다. 알 수 없는 미래 버전은 그대로 둔다."""
    v = raw.get("version", 1)
    if v < 2:
        # v1 에는 plugins 섹션이 없었다 — 기본 플러그인으로 채워진다.
        raw["version"] = 2
    return raw


def save_user(tree: dict) -> None:
    """이 사용자의 설정만 원자적으로 저장한다.

    **합쳐진 결과가 아니라 사용자 층만 쓴다.** 합쳐진 걸 쓰면 서버 공통
    설정이 사용자 파일에 박제돼서, 관리자가 나중에 공통값을 바꿔도
    이미 한 번 설정을 만진 사람에게는 반영되지 않는다.
    """
    global _cache
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(tmp, 0o600)      # 남이 읽을 이유가 없다
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _cache = None                 # 다음 load 에서 층을 다시 쌓는다


# 옛 이름. 사용자 층을 쓴다.
save = save_user


def get(dotted: str, default=None):
    """환경변수 > 저장된 설정 > 기본값."""
    for env, (target, cast) in ENV_OVERRIDES.items():
        if target != dotted:
            continue
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        try:
            return cast(raw)
        except (TypeError, ValueError):
            pass
    found = _dig(load(), dotted, None)
    if found is None:
        return _dig(DEFAULTS, dotted, default)
    return found


def put(dotted: str, value) -> bool:
    """설정 하나를 바꾼다. 관리자가 잠근 키면 아무것도 안 하고 False."""
    if is_locked(dotted):
        return False
    stored = _migrate(_read(path()))
    _plant(stored, dotted, value)
    stored["version"] = SCHEMA_VERSION
    save_user(stored)
    return True


def reset_to_defaults() -> None:
    """내 설정만 지운다. 서버 공통 설정은 그대로 다시 깔린다."""
    save_user({"version": SCHEMA_VERSION})


def overridden_by_env(dotted: str) -> str:
    """이 설정을 지금 환경변수가 덮고 있으면 그 변수 이름."""
    for env, (target, _) in ENV_OVERRIDES.items():
        if target == dotted and os.environ.get(env):
            return env
    return ""


# ── 캐릭터 활성 여부 ───────────────────────────────────────────────────
def character_enabled(pack_id: str, char_id: str) -> bool:
    """설정에 없으면 활성으로 본다 — 새로 설치한 팩이 바로 보이도록."""
    table = get("plugins.characters", {}) or {}
    return bool(table.get(pack_id, {}).get(char_id, True))


def set_character_enabled(pack_id: str, char_id: str, on: bool) -> bool:
    if is_locked("plugins.characters"):
        return False
    stored = _migrate(_read(path()))
    table = stored.setdefault("plugins", {}).setdefault("characters", {})
    table.setdefault(pack_id, {})[char_id] = bool(on)
    stored["version"] = SCHEMA_VERSION
    save_user(stored)
    return True


def enabled_agents() -> list:
    table = get("agents", {}) or {}
    return [name for name, on in table.items() if on]
