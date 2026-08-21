# -*- coding: utf-8 -*-
"""플러그인 — 캐릭터 · UI · 세계관.

코어는 '관계가 어떻게 움직이는가'만 안다. 누구를 만나는지(character),
어떤 세상인지(world), 어떻게 보이는지(ui)는 전부 플러그인이 정한다.

    plugins/<이름>/plugin.toml     선언
    plugins/<이름>/<entry>.py      구현

찾는 곳은 세 군데다. 뒤에서 찾은 것이 앞을 덮는다(같은 id 면).

    1. <설치 폴더>/plugins/                     기본 동봉
    2. ~/.local/share/nerv-social-terminal/plugins/   사용자 설치
    3. $NERV_PLUGIN_PATH (: 로 구분)                  개발용

플러그인 하나가 깨져도 게임은 켜져야 한다. 로드 실패는 예외를 던지지
않고 `Plugin.error` 에 남기며, 설정 화면에서 사유를 보여준다.
"""
import importlib.util
import os
import re
import sys
import traceback
from pathlib import Path

from . import identity

API_VERSION = 1
KINDS = ("character", "ui", "world")

ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════
#  plugin.toml 읽기
# ═══════════════════════════════════════════════════════════════════════
try:
    import tomllib as _toml            # 파이썬 3.11+
except ImportError:
    _toml = None

_TOML_TABLE = re.compile(r"^\[([A-Za-z0-9_.-]+)\]\s*$")
_TOML_PAIR = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$")


def _parse_toml_mini(text: str) -> dict:
    """plugin.toml 이 쓰는 만큼만 읽는 최소 파서.

    tomllib 이 없는 파이썬(3.10 이하)에서도 플러그인이 돌아야 한다.
    지원: [테이블], 문자열, 정수, 참/거짓, 문자열 배열. 그거면 된다.
    """
    out, cur = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _TOML_TABLE.match(line)
        if m:
            cur = out.setdefault(m.group(1), {})
            continue
        m = _TOML_PAIR.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if "#" in val and not val.lstrip().startswith(('"', "'")):
            val = val.split("#", 1)[0].strip()
        target = out if cur is None else cur
        target[key] = _parse_toml_value(val)
    return out


def _parse_toml_value(val: str):
    val = val.strip()
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_toml_value(p) for p in _split_top(inner)]
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    if val in ("true", "false"):
        return val == "true"
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def _split_top(s: str):
    """따옴표 안의 쉼표는 무시하고 쪼갠다."""
    out, buf, quote = [], [], None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
            buf.append(ch)
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [p for p in out if p]


def read_manifest(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    if _toml is not None:
        try:
            return _toml.loads(text)
        except Exception:
            pass
    return _parse_toml_mini(text)


# ═══════════════════════════════════════════════════════════════════════
#  플러그인 한 개
# ═══════════════════════════════════════════════════════════════════════
class Plugin:
    def __init__(self, *, path: Path, meta: dict, source: str):
        p = meta.get("plugin") or {}
        self.path = path
        self.source = source                 # bundled / user / env
        self.meta = meta
        self.id = str(p.get("id") or path.name)
        self.kind = str(p.get("kind") or "")
        self.name = str(p.get("name") or self.id)
        self.version = str(p.get("version") or "0")
        self.api = int(p.get("api") or 0)
        self.entry = str(p.get("entry") or "")
        self.description = str(p.get("description") or "")
        self.author = str(p.get("author") or "")
        # 캐릭터 팩이 전제하는 세계관
        self.world = str((meta.get("character") or {}).get("world") or "")
        self.error = ""
        self._module = None

    def __repr__(self):
        return f"<Plugin {self.kind}:{self.id}{' !' if self.error else ''}>"

    @property
    def ok(self) -> bool:
        return not self.error

    def check(self) -> str:
        """선언만 보고 알 수 있는 문제. 사유 문자열, 없으면 빈 문자열."""
        if self.kind not in KINDS:
            return f"알 수 없는 종류: {self.kind or '(없음)'}"
        if self.api != API_VERSION:
            return f"API {self.api} — 이 버전은 {API_VERSION} 만 안다"
        if not self.entry:
            return "entry 가 없다"
        if not (self.path / self.entry).is_file():
            return f"{self.entry} 파일이 없다"
        return ""

    def module(self):
        """엔트리 모듈을 임포트한다. 실패하면 None 이고 error 에 사유가 남는다."""
        if self._module is not None or self.error:
            return self._module
        problem = self.check()
        if problem:
            self.error = problem
            return None

        target = self.path / self.entry
        mod_name = f"_nervplugin_{self.kind}_{self.id}".replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, target)
        if spec is None or spec.loader is None:
            self.error = "임포트할 수 없다"
            return None
        module = importlib.util.module_from_spec(spec)

        # 플러그인이 자기 폴더의 다른 파일을 import 할 수 있게 해 준다.
        sys.path.insert(0, str(self.path))
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:                              # noqa: BLE001
            sys.modules.pop(mod_name, None)
            self.error = f"{type(exc).__name__}: {exc}"
            if os.environ.get("NERV_DEBUG") or os.environ.get("REI_DEBUG"):
                traceback.print_exc()
            return None
        finally:
            try:
                sys.path.remove(str(self.path))
            except ValueError:
                pass
        self._module = module
        return module


# ═══════════════════════════════════════════════════════════════════════
#  찾기
# ═══════════════════════════════════════════════════════════════════════
def search_paths():
    """(경로, 출처) 목록. 뒤가 앞을 덮는다."""
    out = [(ROOT / "plugins", "bundled"),
           (identity.data_dir() / "plugins", "user")]
    extra = os.environ.get("NERV_PLUGIN_PATH", "")
    for chunk in extra.split(os.pathsep):
        chunk = chunk.strip()
        if chunk:
            out.append((Path(chunk).expanduser(), "env"))
    return out


_registry = None


def discover(*, refresh: bool = False) -> dict:
    """{(kind, id): Plugin}. 한 번 훑고 캐시한다."""
    global _registry
    if _registry is not None and not refresh:
        return _registry
    found = {}
    for base, source in search_paths():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            manifest = child / "plugin.toml"
            if not (child.is_dir() and manifest.is_file()):
                continue
            try:
                meta = read_manifest(manifest)
            except Exception as exc:                          # noqa: BLE001
                continue
            plug = Plugin(path=child, meta=meta, source=source)
            plug.error = plug.check()
            found[(plug.kind, plug.id)] = plug          # 뒤가 앞을 덮는다
    _registry = found
    return found


def by_kind(kind: str):
    """그 종류의 플러그인 목록. id 순."""
    return sorted((p for (k, _), p in discover().items() if k == kind),
                  key=lambda p: p.id)


def get(kind: str, plugin_id: str):
    return discover().get((kind, plugin_id))


def resolve(kind: str, wanted: str, fallback: str = ""):
    """원하는 플러그인. 없거나 깨졌으면 대체품을 찾는다.

    (플러그인, 사유) 를 돌려준다. 사유가 있으면 원하는 것을 못 쓴 이유다.
    """
    plug = get(kind, wanted)
    if plug is not None and plug.ok:
        return plug, ""
    why = (f"'{wanted}' 를 쓸 수 없다: {plug.error}" if plug is not None
           else f"'{wanted}' 플러그인이 없다")
    if fallback and fallback != wanted:
        alt = get(kind, fallback)
        if alt is not None and alt.ok:
            return alt, why
    for alt in by_kind(kind):
        if alt.ok:
            return alt, why
    return None, why
