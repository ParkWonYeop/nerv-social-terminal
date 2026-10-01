# -*- coding: utf-8 -*-
"""스모크 테스트 — 플러그인 구조가 무너지지 않았는지.

    python3 tests/smoke.py

의존성 없이 돈다(pytest 불필요). 임시 저장소를 쓰므로 실제 플레이
데이터는 건드리지 않는다.

이 게임은 화면이 전부라 자동 테스트가 어렵다. 그래서 여기서는
'그려지는 모양'이 아니라 **계약**을 확인한다: 플러그인이 로드되는가,
뷰 모델이 만들어지는가, 안전장치가 실제로 막는가, 초기화가 남의
데이터를 지우지 않는가.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 실제 저장소를 건드리지 않게 — nervterm 을 임포트하기 전에 잡아야 한다.
_TMP = tempfile.mkdtemp(prefix="nerv-smoke-")
os.environ["NERV_DATA"] = _TMP
os.environ["REI_PLAYER"] = "smoketest"
# 실제 에이전트 세션 기록도 읽지 않는다 — Game 을 만들면 근무 일지 스캔이
# 돈다. 그게 ~/.claude · ~/.codex 의 진짜 기록을 시험 저장소로 끌고 왔다.
os.environ["CODEX_HOME"] = str(Path(_TMP) / "codex-home")
from nervterm import agents as _agents                         # noqa: E402
_agents.ClaudeAgent.sessions_dir = (
    lambda self: Path(_TMP) / "claude-projects")

PASS, FAIL = [], []


def check(name):
    def wrap(fn):
        try:
            fn()
        except Exception as exc:                              # noqa: BLE001
            FAIL.append((name, f"{type(exc).__name__}: {exc}",
                         traceback.format_exc()))
        else:
            PASS.append(name)
        return fn
    return wrap


def eq(got, want, what=""):
    if got != want:
        raise AssertionError(f"{what}: {got!r} != {want!r}")


def true(cond, what=""):
    if not cond:
        raise AssertionError(what or "참이어야 한다")


# ═══════════════════════════════════════════════════════════════════════
@check("플러그인 발견 — 캐릭터·UI·세계관이 다 보인다")
def _():
    from nervterm import plugins
    found = plugins.discover(refresh=True)
    kinds = {k for k, _ in found}
    for want in ("character", "ui", "world"):
        true(want in kinds, f"{want} 플러그인이 없다")
    for key, plug in found.items():
        true(plug.ok, f"{key} 가 깨졌다: {plug.error}")


@check("캐릭터 계약 검증 — 세 사람이 통과한다")
def _():
    from nervterm import characters
    characters.load(refresh=True)
    eq(len(characters.LOAD_ERRORS), 0, "로드 오류")
    true(len(characters.IDS) >= 3, "캐릭터가 3명 미만")
    true("rei" in characters.IDS, "레이가 없다")


@check("캐릭터 계약 검증 — 필드가 빠지면 거부한다")
def _():
    from nervterm import spec
    broken = spec.Character(id="x", name="x", full="x", core="x")
    try:
        spec.validate_character(broken)
    except spec.SpecError:
        return
    raise AssertionError("빠진 필드를 잡지 못했다")


@check("캐릭터 계약 — 감정 색이 빠지면 거부한다")
def _():
    from nervterm import characters, spec
    import copy
    rei = characters.get("rei")
    clone = spec.Character(**{k: v for k, v in rei.__dict__.items()})
    clone.theme = copy.deepcopy(rei.theme)
    clone.theme["emotion"].pop("shaken")
    try:
        spec.validate_character(clone)
    except spec.SpecError as exc:
        true("shaken" in str(exc), f"사유에 감정 이름이 없다: {exc}")
        return
    raise AssertionError("빠진 감정 색을 잡지 못했다")


@check("동봉 캐릭터 팩이 전부 계약을 지킨다")
def _():
    from nervterm import characters, plugins, spec
    characters.load(refresh=True)
    eq(characters.LOAD_ERRORS, [], "로드 오류")
    for cid in characters.IDS:
        spec.validate_character(characters.get(cid))
    # 팩이 전제하는 세계관이 실제로 설치돼 있어야 한다
    for pack_id in characters.PACKS:
        plug = plugins.get("character", pack_id)
        if plug is not None and plug.world:
            true(plugins.get("world", plug.world) is not None,
                 f"{pack_id} 가 전제한 세계관 '{plug.world}' 가 없다")


@check("에밀리아 — 팩이 제대로 실린다")
def _():
    from nervterm import characters
    characters.load(refresh=True)
    true("emilia" in characters.IDS, "에밀리아가 없다")
    e = characters.get("emilia")
    eq(characters.pack_of("emilia"), "rezero-characters", "팩")
    eq(len(e.gifts), 12, "선물 수")
    eq(len(e.dates), 10, "데이트 수")
    # 세계관 텍스트가 CORE 에 섞이면 안 된다 — 세계는 world 플러그인 몫이다
    for word in ("NERV", "제3신동경시", "오퍼레이터"):
        true(word not in e.core, f"CORE 에 다른 세계 텍스트가 있다: {word}")
    # 리제로 고유 설정이 실제로 들어 있는지
    for word in ("하프엘프", "팩", "왕선", "사테라"):
        true(word in e.core, f"CORE 에 '{word}' 가 없다")


@check("세계관 — 리제로 세계가 실린다")
def _():
    from nervterm import settings, world
    settings.put("plugins.world", "rezero")
    try:
        w = world.load(refresh=True)
        eq(w.id, "rezero", "세계관 id")
        eq(w.currency_name, "동화", "재화 이름")
        true("로즈월" in w.player_role, "플레이어 역할")
        block = w.prompt_block("에밀리아")
        true("루그니카" in block, "세계 설명")
        true("NERV" not in block, "다른 세계가 섞였다")
    finally:
        settings.put("plugins.world", "nerv")
        world.load(refresh=True)


@check("세계관 — 바꾸면 화면도 따라 바뀐다")
def _():
    from nervterm import characters, settings, ui, world
    characters.load(refresh=True)
    settings.put("plugins.world", "nerv")
    w = world.load(refresh=True)
    u = ui.load(w, refresh=True)
    eq(u.world.id, "nerv", "시작 세계")
    try:
        world.use("rezero")
        # UI 가 시작할 때 받은 세계관 객체를 계속 들고 있으면, 상태창의
        # 재화는 바뀌었는데 타이틀 카드만 옛 이름으로 남는다.
        eq(ui.active().world.id, "rezero", "화면이 옛 세계를 붙들고 있다")
        eq(ui.active().world.currency_name, "동화", "재화 이름")
    finally:
        world.use("nerv")
        eq(ui.active().world.id, "nerv", "되돌리기")


@check("세계관 — 캐릭터와 안 맞으면 알려 준다")
def _():
    from nervterm import characters, settings, world
    settings.put("plugins.world", "nerv")
    characters.load(refresh=True)
    world.load(refresh=True)
    bad = dict(world.mismatches())
    true("emilia" in bad, "에밀리아 불일치를 못 잡았다")
    eq(bad["emilia"], "rezero", "전제 세계관")

    settings.put("plugins.world", "rezero")
    world.load(refresh=True)
    bad2 = dict(world.mismatches())
    true("emilia" not in bad2, "맞는데도 불일치라고 한다")
    true("rei" in bad2, "이번엔 레이가 불일치여야 한다")

    settings.put("plugins.world", "nerv")
    world.load(refresh=True)


@check("세계관 — 재화 이름이 플러그인에서 온다")
def _():
    from nervterm import world
    w = world.load(refresh=True)
    eq(w.currency_name, "LCL", "재화 이름")
    true("NERV" in w.player_role, "플레이어 역할에 NERV 가 없다")
    block = w.prompt_block("레이")
    true("[세계]" in block, "프롬프트 블록 머리말")
    true(w.player_role.rstrip(".") in block, "역할이 프롬프트에 안 들어갔다")


@check("캐릭터 프롬프트에 세계관이 안 박혀 있다")
def _():
    from nervterm import characters
    for cid in characters.IDS:
        core = characters.get(cid).core
        true("제1지부 기술부 오퍼레이터" not in core,
             f"{cid}: 세계관 텍스트가 CORE 에 남아 있다")


@check("설정 — 저장하고 다시 읽으면 남아 있다")
def _():
    from nervterm import settings
    settings.put("daily_llm_calls", 77)
    eq(settings.load(refresh=True)["daily_llm_calls"], 77, "저장된 값")
    eq(settings.get("daily_llm_calls"), 77, "읽은 값")
    settings.put("daily_llm_calls", 200)


@check("설정 — 환경변수가 저장된 값을 이긴다")
def _():
    from nervterm import settings
    settings.put("daily_llm_calls", 111)
    os.environ["NERV_DAILY_LLM_CALLS"] = "5"
    try:
        eq(settings.get("daily_llm_calls"), 5, "환경변수 우선")
        eq(settings.overridden_by_env("daily_llm_calls"),
           "NERV_DAILY_LLM_CALLS", "덮은 변수 이름")
    finally:
        del os.environ["NERV_DAILY_LLM_CALLS"]
        settings.put("daily_llm_calls", 200)


@check("설정 — 사용자별로 분리된다 (저장소를 공유해도)")
def _():
    from nervterm import identity, settings
    import importlib

    def as_user(name, fn):
        os.environ["REI_PLAYER"] = name
        settings._cache = None
        importlib.reload(identity)
        importlib.reload(settings)
        try:
            return fn()
        finally:
            os.environ["REI_PLAYER"] = "smoketest"
            settings._cache = None
            importlib.reload(identity)
            importlib.reload(settings)

    as_user("alice", lambda: settings.put("daily_llm_calls", 50))
    got = as_user("bob", lambda: settings.get("daily_llm_calls"))
    eq(got, 200, "bob 이 alice 의 설정을 봤다")
    back = as_user("alice", lambda: settings.get("daily_llm_calls"))
    eq(back, 50, "alice 의 설정이 사라졌다")
    true(as_user("alice", lambda: settings.path().name) !=
         as_user("bob", lambda: settings.path().name), "파일이 같다")


@check("설정 — 서버 공통값이 기본이 되고, 잠근 것은 못 바꾼다")
def _():
    import importlib
    from nervterm import settings
    site = Path(_TMP) / "site.json"
    site.write_text(json.dumps({
        "daily_llm_calls": 80,
        "llm": {"billing_guard": True, "api_daily_call_cap": 10},
        "locked": ["daily_llm_calls", "llm.billing_guard"],
    }), encoding="utf-8")
    os.environ["NERV_SITE_SETTINGS"] = str(site)
    settings._cache = None
    try:
        eq(settings.get("daily_llm_calls"), 80, "공통 기본값")
        eq(settings.get("llm.api_daily_call_cap"), 10, "공통값(안 잠김)")
        true(settings.is_locked("daily_llm_calls"), "잠금 판별")
        true(settings.is_locked("llm.billing_guard"), "점 표기 잠금")
        true(not settings.is_locked("typing_speed"), "안 잠긴 키")

        eq(settings.put("daily_llm_calls", 5000), False, "잠긴 키가 바뀌었다")
        eq(settings.get("daily_llm_calls"), 80, "잠긴 값이 밀렸다")

        eq(settings.put("typing_speed", 0.05), True, "안 잠긴 키를 못 바꿨다")
        eq(settings.get("typing_speed"), 0.05, "변경이 반영 안 됐다")

        # 안 잠긴 공통값은 사용자가 덮을 수 있어야 한다
        settings.put("llm.api_daily_call_cap", 3)
        eq(settings.get("llm.api_daily_call_cap"), 3, "공통값을 못 덮었다")
    finally:
        del os.environ["NERV_SITE_SETTINGS"]
        settings._cache = None


@check("설정 — 파일을 손으로 고쳐도 잠금을 못 넘는다")
def _():
    from nervterm import settings
    site = Path(_TMP) / "site2.json"
    site.write_text(json.dumps({
        "daily_llm_calls": 80, "locked": ["daily_llm_calls"]}),
        encoding="utf-8")
    p = settings.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 2, "daily_llm_calls": 9999}),
                 encoding="utf-8")
    os.environ["NERV_SITE_SETTINGS"] = str(site)
    settings._cache = None
    try:
        eq(settings.get("daily_llm_calls"), 80, "손으로 고쳐 잠금을 넘었다")
    finally:
        del os.environ["NERV_SITE_SETTINGS"]
        p.write_text(json.dumps({"version": 2}), encoding="utf-8")
        settings._cache = None


@check("설정 — 사용자 파일에 공통값이 박제되지 않는다")
def _():
    from nervterm import settings
    site = Path(_TMP) / "site3.json"
    site.write_text(json.dumps({"typing_speed": 0.01}), encoding="utf-8")
    os.environ["NERV_SITE_SETTINGS"] = str(site)
    settings._cache = None
    try:
        settings.put("animation", False)          # 다른 키를 만진다
        stored = json.loads(settings.path().read_text(encoding="utf-8"))
        true("typing_speed" not in stored,
             "공통값이 사용자 파일에 박혔다 — 관리자가 바꿔도 반영 안 된다")
        eq(settings.get("typing_speed"), 0.01, "공통값이 적용돼야 한다")
    finally:
        del os.environ["NERV_SITE_SETTINGS"]
        settings.put("animation", True)
        settings._cache = None


@check("설정 — 깨진 파일이 게임을 막지 않는다")
def _():
    from nervterm import settings
    p = settings.path()
    backup = p.read_text(encoding="utf-8") if p.is_file() else None
    p.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")
    try:
        eq(settings.load(refresh=True)["daily_llm_calls"], 200, "기본값 복귀")
    finally:
        if backup is not None:
            p.write_text(backup, encoding="utf-8")
        settings.load(refresh=True)


@check("과금 가드 — 켜져 있으면 유료 프로바이더를 막는다")
def _():
    from nervterm import llm, settings
    from nervterm.llm import guard
    settings.put("llm.billing_guard", True)
    cfg = settings.get("llm", {})
    paid = llm.AnthropicAPI(cfg)
    true(paid.is_billable(), "Anthropic API 는 과금이어야 한다")
    true(bool(guard.blocked_reason(paid)), "가드가 막지 않았다")

    free = llm.ClaudeCLI(cfg)
    true(not free.is_billable(), "구독 좌석은 과금이 아니다")
    eq(guard.blocked_reason(free), "", "구독 좌석을 막으면 안 된다")


@check("과금 가드 — 설정을 손으로 고쳐도 우회할 수 없다")
def _():
    from nervterm import llm, settings
    settings.put("llm.billing_guard", True)
    settings.put("llm.provider", "anthropic-api")
    try:
        got = llm.current()
        eq(got.id, "claude-cli", "가드가 켜졌는데 유료가 선택됐다")
    finally:
        settings.put("llm.provider", "claude-cli")


@check("과금 가드 — 끄면 유료를 고를 수 있다")
def _():
    from nervterm import llm, settings
    from nervterm.llm import guard
    settings.put("llm.billing_guard", False)
    settings.put("llm.provider", "anthropic-api")
    try:
        eq(llm.current().id, "anthropic-api", "가드를 껐는데 못 고른다")
        eq(guard.blocked_reason(llm.current()), "", "가드가 꺼졌는데 막는다")
    finally:
        settings.put("llm.provider", "claude-cli")
        settings.put("llm.billing_guard", True)


@check("로컬 주소 판별 — 로컬은 과금으로 보지 않는다")
def _():
    from nervterm.llm.base import is_local_url
    for url in ("http://localhost:1234", "http://127.0.0.1:8000",
                "http://192.168.0.5:11434", "http://172.17.0.2:1234"):
        true(is_local_url(url), f"{url} 를 로컬로 못 봤다")
    for url in ("https://api.openai.com", "http://172.40.1.1:80",
                "https://example.com"):
        true(not is_local_url(url), f"{url} 를 로컬로 잘못 봤다")


@check("OpenAI 호환 — 주소로 과금 여부가 갈린다")
def _():
    from nervterm import llm
    local = llm.OpenAICompat(
        {"base_urls": {"openai-compat": "http://localhost:1234"}})
    true(not local.is_billable(), "로컬인데 과금으로 봤다")
    remote = llm.OpenAICompat(
        {"base_urls": {"openai-compat": "https://someone.example.com"}})
    true(remote.is_billable(), "바깥 주소인데 과금이 아니라고 봤다")
    # 주소를 안 정했으면 기본값(로컬)이라 과금이 아니다
    true(not llm.OpenAICompat({}).is_billable(), "기본값이 과금으로 잡혔다")


@check("프로바이더 목록 — 전부 계약을 지킨다")
def _():
    from nervterm import llm
    for klass in llm.CATALOG:
        p = klass({})
        true(bool(p.id), "id 가 없다")
        true(bool(p.label), f"{p.id}: label 이 없다")
        true(p.billing in ("none", "subscription", "api"),
             f"{p.id}: 과금 분류가 이상하다 — {p.billing}")
        true(callable(p.complete), f"{p.id}: complete 가 없다")
        ok, why = p.available()
        true(isinstance(ok, bool) and isinstance(why, str),
             f"{p.id}: available() 반환이 이상하다")


@check("JSON 건져내기 — 코드펜스와 잡담을 견딘다")
def _():
    from nervterm.llm import base
    want = {"line": "그래.", "emotion": "neutral"}
    for raw in ('{"line":"그래.","emotion":"neutral"}',
                '```json\n{"line":"그래.","emotion":"neutral"}\n```',
                '네 알겠습니다\n{"line":"그래.","emotion":"neutral"}\n끝'):
        got = base.extract_json(raw)
        eq(got, want, f"건져내기 실패: {raw[:30]}")
    eq(base.extract_json("아무 JSON 도 없다"), None, "없는데 만들어냈다")


@check("응답 정리 — 이상한 값이 와도 안 죽는다")
def _():
    from nervterm.llm import base
    got = base.normalize({"line": "…", "emotion": "그런감정없음",
                          "affection_delta": "99", "choices": "배열아님",
                          "trust_delta": None})
    eq(got["emotion"], "neutral", "모르는 감정은 neutral 로")
    eq(got["affection_delta"], 3, "clamp")
    eq(got["choices"], [], "배열이 아니면 빈 목록")
    eq(got["trust_delta"], 0, "None 은 0")
    eq(base.normalize("dict 아님"), None, "dict 가 아니면 None")


@check("에이전트 — Claude·Codex·로컬이 등록돼 있다")
def _():
    from nervterm import agents
    ids = {a.id for a in agents.AGENTS}
    eq(ids, {"claude", "codex", "local"}, "에이전트 목록")
    for a in agents.AGENTS:
        if a.needs_hook:
            true(bool(a.hook_path()), f"{a.id}: 훅 경로가 없다")
        true(isinstance(a.hook_installed(), bool),
             f"{a.id}: hook_installed 가 bool 이 아니다")


@check("Claude 세션 파싱 — 프롬프트·파일·커밋을 뽑는다")
def _():
    from nervterm import agents
    a = agents.get("claude")
    kinds = lambda facts: {k for _, _, k, _, _ in facts}

    got = a.harvest({"type": "user", "promptSource": "typed",
                     "timestamp": "2026-08-21T10:00:00Z",
                     "cwd": "/home/x/proj", "gitBranch": "main",
                     "message": {"content": "이거 고쳐줘"}}, "s1")
    true("prompt" in kinds(got), "프롬프트를 못 뽑았다")
    true("project" in kinds(got), "프로젝트를 못 뽑았다")

    # 사람이 안 친 프롬프트는 무시한다
    eq(a.harvest({"type": "user", "promptSource": "sdk",
                  "message": {"content": "게임이 부른 것"}}, "s1"), [],
       "SDK 프롬프트를 실적으로 셌다")

    got = a.harvest({"type": "assistant", "timestamp": "2026-08-21T10:00:00Z",
                     "message": {"content": [
                         {"type": "tool_use", "name": "Edit",
                          "input": {"file_path": "/a/b/main.py"}},
                         {"type": "tool_use", "name": "Bash",
                          "input": {"command": 'git commit -m "고침"',
                                    "description": "커밋한다"}}]}}, "s1")
    k = kinds(got)
    for want in ("file", "desc", "commit"):
        true(want in k, f"{want} 를 못 뽑았다")


@check("Codex 세션 파싱 — 다른 형식에서 같은 사실을 뽑는다")
def _():
    from nervterm import agents
    import json as _json
    a = agents.get("codex")
    kinds = lambda facts: {k for _, _, k, _, _ in facts}

    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z", "type": "event_msg",
                     "payload": {"type": "user_message",
                                 "message": "이거 고쳐줘"}}, "s1")
    true("prompt" in kinds(got), "프롬프트를 못 뽑았다")

    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z", "type": "event_msg",
                     "payload": {"type": "patch_apply_end", "success": True,
                                 "changes": {"/a/b/main.py": {}}}}, "s1")
    true("file" in kinds(got), "patch_apply_end 에서 파일을 못 뽑았다")

    eq(a.harvest({"timestamp": "2026-08-21T10:00:00Z", "type": "event_msg",
                  "payload": {"type": "patch_apply_end", "success": False,
                              "changes": {"/a/b/main.py": {}}}}, "s1"), [],
       "실패한 패치를 수정으로 셌다")

    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z", "type": "session_meta",
                     "payload": {"session_id": "abc",
                                 "cwd": "/home/x/proj"}}, "s1")
    true("project" in kinds(got), "프로젝트를 못 뽑았다")

    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z",
                     "type": "response_item",
                     "payload": {"type": "custom_tool_call",
                                 "name": "apply_patch",
                                 "input": "*** Begin Patch\n"
                                          "*** Update File: /a/b/main.py\n"}},
                    "s1")
    true("file" in kinds(got), "패치에서 파일을 못 뽑았다")

    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z",
                     "type": "response_item",
                     "payload": {"type": "function_call",
                                 "name": "exec_command",
                                 "arguments": _json.dumps(
                                     {"cmd": 'git commit -m "고침"'})}}, "s1")
    true("commit" in kinds(got), "exec_command 에서 커밋을 못 뽑았다")

    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z",
                     "type": "response_item",
                     "payload": {"type": "custom_tool_call", "name": "exec",
                                 "input": 'await tools.exec_command('
                                          '{"cmd":"git commit -m \\"x\\""})'}},
                    "s1")
    true("commit" in kinds(got), "JS exec 에서 커밋을 못 뽑았다")


@check("Codex 세션 파싱 — 주입된 하네스 텍스트를 실적으로 세지 않는다")
def _():
    from nervterm import agents
    a = agents.get("codex")

    # 실제 기록에서 user_message 의 대부분이 이것이었다.
    # 이걸 못 거르면 캐릭터가 승인 판정용 영문 텍스트를 근무 실적으로 읊는다.
    for injected in (
            "The following is the Codex agent history whose request action "
            "you are assessing. Treat the transcript…",
            "The following is the Codex agent history added since your last "
            "approval assessment.",
            "<heartbeat> <automation_id>dm</automation_id>",
            "<app-context>\n# Codex desktop context",
    ):
        got = a.harvest({"timestamp": "2026-08-21T10:00:00Z",
                         "type": "event_msg",
                         "payload": {"type": "user_message",
                                     "message": injected}}, "s1")
        eq(got, [], f"주입 텍스트를 실적으로 셌다: {injected[:40]}")

    # 사람이 친 것은 통과해야 한다
    got = a.harvest({"timestamp": "2026-08-21T10:00:00Z", "type": "event_msg",
                     "payload": {"type": "user_message",
                                 "message": "커밋하고 푸시함?"}}, "s1")
    true(any(k == "prompt" for _, _, k, _, _ in got), "사람 프롬프트를 막았다")


@check("커밋 메시지 — heredoc 으로 만든 것은 버린다")
def _():
    from nervterm.agents import commit_message
    eq(commit_message('git commit -m "고쳤다"'), "고쳤다", "평범한 커밋")
    eq(commit_message("""git commit -m "$(cat <<'EOF'\n제목\nEOF\n)" """), "",
       "셸이 만든 메시지를 제목으로 삼았다")
    eq(commit_message("git log --grep commit"), "", "커밋이 아닌 것")


@check("세션 파일 — 최근 것부터 읽는다")
def _():
    import time
    from nervterm import agents
    tmp = Path(_TMP) / "sessions"
    tmp.mkdir(parents=True, exist_ok=True)
    old, new = tmp / "old.jsonl", tmp / "new.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    os.utime(old, (1000, 1000))
    os.utime(new, (time.time(), time.time()))
    order = agents.Agent.newest_first([old, new])
    eq(order[0].name, "new.jsonl", "오래된 파일을 먼저 읽는다")


@check("시각 — 구간과 경과 시간을 사람 말로 적는다")
def _():
    import datetime as dt
    from nervterm import clock
    for hour, want in ((2, "심야"), (5, "새벽"), (8, "아침"),
                       (14, "낮"), (18, "저녁"), (22, "밤")):
        got = clock.now_line(dt.datetime(2026, 8, 22, hour, 30))
        true(want in got, f"{hour}시가 '{want}' 이 아니다: {got}")
    got = clock.now_line(dt.datetime(2026, 8, 22, 13, 49))
    true("오후 1시 49분" in got, f"12시간제 표기: {got}")
    true("(토)" in got, f"요일: {got}")

    now = dt.datetime(2026, 8, 22, 13, 49)
    for mins, want in ((1, "방금"), (25, "25분 전"), (300, "5시간 전"),
                       (1500, "어제"), (5000, "3일 전")):
        eq(clock.ago(now - dt.timedelta(minutes=mins), now), want,
           f"{mins}분 전")
    eq(clock.ago("", now), "", "빈 값")
    eq(clock.ago("이건 시각이 아니다", now), "", "깨진 값")
    eq(clock.ago(now + dt.timedelta(hours=1), now), "", "미래 시각")

    true(clock.is_odd_hour(dt.datetime(2026, 8, 22, 3, 0)), "새벽 3시")
    true(not clock.is_odd_hour(dt.datetime(2026, 8, 22, 14, 0)), "낮 2시")


@check("시각 — 프롬프트에 시간 블록이 들어간다")
def _():
    import datetime as dt
    from nervterm import characters, clock, db, game, world
    characters.load(refresh=True)
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        old = (dt.datetime.now() - dt.timedelta(hours=5)).isoformat(
            timespec="seconds")
        con.execute("INSERT INTO dialogue(player,char,ts,role,text,emotion,"
                    "sess) VALUES(?,?,?,?,?,?,?)",
                    (db.PLAYER, char.id, old, "rei", "그래.", "neutral", "옛"))
        con.commit()
        g = game.Game(con, char, offline=True, animate=False)
        ctx = g.context(g.state())
    true("[지금]" in ctx, "시간 블록이 없다")
    true("시" in ctx and "분" in ctx, "시각 표기가 없다")
    true("5시간 전" in ctx, f"경과 시간이 없다")
    true("다른 시간대를 상상하지 마라" in ctx, "시간 취급 지침이 없다")


@check("프로바이더 — 모델이 프로바이더별로 분리된다")
def _():
    from nervterm import llm
    # 옛 방식(공용 키)에 남의 모델이 남아 있어도 따라가지 않는다.
    cfg = {"provider": "claude-cli", "models": {"ollama": "qwen3:14b"}}
    eq(llm.ClaudeCLI(cfg).model, "sonnet", "남의 모델이 따라왔다")
    eq(llm.Ollama(cfg).model, "qwen3:14b", "자기 모델을 못 읽었다")
    cfg2 = {"models": {"claude-cli": "opus", "ollama": "gemma"}}
    eq(llm.ClaudeCLI(cfg2).model, "opus", "프로바이더별 저장")
    eq(llm.Ollama(cfg2).model, "gemma", "프로바이더별 저장")


@check("설정 — v2 의 공용 모델은 버려진다 (틀리면 조용히 죽으므로)")
def _():
    from nervterm import settings
    p = settings.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "version": 2,
        "llm": {"provider": "claude-cli", "model": "qwen3:14b",
                "base_url": "http://localhost:11434"}}), encoding="utf-8")
    settings._cache = None
    try:
        got = settings.get("llm", {})
        true("model" not in got, "공용 모델이 남았다")
        eq(got.get("models"), {}, "추측해서 옮겼다")
    finally:
        p.write_text(json.dumps({"version": 3}), encoding="utf-8")
        settings._cache = None


@check("상태줄 위젯 — 캐시를 읽어 한두 줄을 그린다")
def _():
    from nervterm import characters, db, widget, world
    characters.load(refresh=True)
    w = world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        db.put(con, "affection", 42, char=char.id)
        db.put(con, "lcl", 1234)
        db.say(con, "rei", "…그래.", "neutral", "s", char=char.id)
        widget.remember(con, char, w, "관심")
        con.commit()
    got = widget.render()
    true(bool(got), "아무것도 안 그렸다")
    true("레이" in got, "이름이 없다")
    true("42" in got, "호감이 없다")
    true("그래" in got, "마지막 대사가 없다")
    true(len(got.splitlines()) == 2, "두 줄이어야 한다")


@check("상태줄 위젯 — 데이터가 없으면 조용히 아무것도 안 그린다")
def _():
    from nervterm import widget
    old = os.environ.get("NERV_DATA")
    os.environ["NERV_DATA"] = str(Path(_TMP) / "없는폴더")
    try:
        eq(widget.render(), "", "없는 저장소에서 뭔가 그렸다")
    finally:
        if old:
            os.environ["NERV_DATA"] = old


@check("상태줄 위젯 — 게임 모듈을 끌어오지 않는다")
def _():
    # 위젯은 Claude Code 가 화면을 갱신할 때마다 돈다. rich·플러그인을
    # 임포트하면 그 비용이 매번 붙는다.
    import subprocess
    code = ("import sys; sys.path.insert(0, %r);"
            "import nervterm.widget;"
            "bad=[m for m in ('rich','nervterm.plugins','nervterm.game',"
            "'nervterm.characters','nervterm.ui') if m in sys.modules];"
            "print(','.join(bad))" % str(ROOT))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=30).stdout.strip()
    eq(out, "", f"무거운 모듈이 딸려 왔다: {out}")


@check("상태줄 설치 — 남의 상태줄을 덮지 않는다")
def _():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_installhooks", ROOT / "install-hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    true(mod.is_our_statusline(
        {"statusLine": {"command": "/x/nervterm/widget.py"}}), "우리 것 판별")
    true(not mod.is_our_statusline(
        {"statusLine": {"command": "/usr/local/bin/other.sh"}}), "남의 것 판별")
    true(not mod.is_our_statusline({}), "없을 때")


@check("훅 판별 — 남의 훅을 우리 것으로 오인하지 않는다")
def _():
    from nervterm.agents import is_our_hook
    ours = {"hooks": [{"type": "command",
                       "command": "/opt/nerv/eva hook"}]}
    true(is_our_hook(ours), "우리 훅을 못 알아봤다")
    for other in ("/usr/bin/reindex-hook.sh",
                  "~/.claude/hooks/peon-ping/peon.sh",
                  "eva --status"):
        true(not is_our_hook({"hooks": [{"command": other}]}),
             f"남의 훅을 우리 것으로 봤다: {other}")


@check("위험 명령 — 언급과 실행을 구분한다")
def _():
    from nervterm import economy
    true(economy.check_danger("rm -rf /") is not None, "실행을 못 잡았다")
    true(economy.check_danger('echo "rm -rf /"') is None,
         "인용부호 안의 언급을 실행으로 봤다")
    true(economy.check_danger("cat <<'EOF'\nmkfs /dev/sda\nEOF") is None,
         "heredoc 안의 언급을 실행으로 봤다")


@check("뷰 모델 — 상태창을 만들 수 있다")
def _():
    from nervterm import characters, db, game, world
    from nervterm.ui import view as V
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        g = game.Game(con, char, offline=True, animate=False)
        st = g.state()
        true(isinstance(st, V.Status), "Status 가 아니다")
        eq(st.currency_name, "LCL", "재화 이름이 안 들어왔다")
        eq(st.char_name, "레이", "이름")
        true(st.money_text().startswith("¤"), "재화 표기")


@check("뷰 모델 — 목록·기록 화면이 데이터만으로 만들어진다")
def _():
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        g = game.Game(con, char, offline=True, animate=False)
        st = g.state()
        rows = game.catalog(char.gifts, st.affection)
        true(len(rows) > 0, "선물 목록이 비었다")
        for _, item in rows:
            eq(len(item), 5, "선물 튜플 모양")


@check("오프라인 대사 — LLM 없이도 응답이 나온다")
def _():
    from nervterm import characters, db, persona, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)

        class FakeStatus:
            stage_idx = 0
        got = persona.fallback_response(con, FakeStatus(), char)
        true(bool(got["line"]), "대사가 비었다")
        eq(got["affection_delta"], 0, "폴백이 수치를 움직이면 안 된다")


@check("초기화 — 캐릭터 하나만 지운다")
def _():
    from nervterm import characters, db, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        db.put(con, "affection", 50, char="rei")
        db.put(con, "affection", 60, char="asuka")
        db.put(con, "lcl", 999)
        db.reset_character(con, "rei")
        eq(db.geti(con, "affection", char="rei"), 5, "레이가 초기화 안 됐다")
        eq(db.geti(con, "affection", char="asuka"), 60, "아스카까지 지웠다")
        eq(db.geti(con, "lcl"), 999, "재화까지 지웠다")


@check("초기화 — 관계만 지우면 재화는 남는다")
def _():
    from nervterm import db, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        db.put(con, "lcl", 1234)
        db.put(con, "affection", 70, char="rei")
        db.reset_relationships(con)
        eq(db.geti(con, "affection", char="rei"), 5, "관계가 안 지워졌다")
        eq(db.geti(con, "lcl"), 1234, "재화가 지워졌다")


@check("초기화 — 전부 지우면 재화도 간다")
def _():
    from nervterm import db, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        db.put(con, "lcl", 555)
        db.reset_everything(con)
        eq(db.geti(con, "lcl"), 0, "재화가 안 지워졌다")
        eq(db.geti(con, "affection", char="rei"), 5, "관계 기본값")


@check("초기화 — 남의 데이터는 건드리지 않는다")
def _():
    from nervterm import db, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        con.execute("INSERT OR REPLACE INTO state(player,char,key,value) "
                    "VALUES('남','rei','affection','88')")
        con.commit()
        db.reset_everything(con)
        row = con.execute("SELECT value FROM state WHERE player='남' "
                          "AND key='affection'").fetchone()
        true(row is not None and row[0] == "88", "남의 기록을 지웠다")
        con.execute("DELETE FROM state WHERE player='남'")
        con.commit()


@check("UI 플러그인 — 계약대로 올라온다")
def _():
    from nervterm import ui, world
    from nervterm.ui.base import BaseUI
    w = world.load(refresh=True)
    got = ui.load(w, refresh=True)
    true(isinstance(got, BaseUI), "BaseUI 를 상속하지 않았다")
    eq(ui.LOAD_ERROR, "", f"로드 오류: {ui.LOAD_ERROR}")
    for name in ("boot", "title_card", "select_character", "frame",
                 "shop", "status", "memory", "worklog", "help", "menu",
                 "notice", "dim", "confirm", "thinking"):
        true(callable(getattr(got, name, None)), f"{name} 이 없다")


@check("UI 플러그인 — 없는 걸 고르면 기본으로 떨어진다")
def _():
    from nervterm import settings, ui, world
    from nervterm.ui.base import BaseUI
    settings.put("plugins.ui", "이런건없다")
    try:
        got = ui.load(world.active(), refresh=True)
        true(isinstance(got, BaseUI), "떨어질 곳이 없다")
        true(bool(ui.LOAD_ERROR), "사유를 안 남겼다")
    finally:
        settings.put("plugins.ui", "nerv")
        ui.load(world.active(), refresh=True)


@check("캐릭터 켜고 끄기 — 관계는 남는다")
def _():
    from nervterm import characters, settings
    settings.set_character_enabled("eva-characters", "asuka", False)
    characters.load(refresh=True)
    try:
        true("asuka" not in characters.ENABLED, "껐는데 켜져 있다")
        true("asuka" in characters.IDS, "껐다고 명부에서 사라졌다")
    finally:
        settings.set_character_enabled("eva-characters", "asuka", True)
        characters.load(refresh=True)


@check("plugin.toml 미니 파서 — 인라인 주석·따옴표 안 #·배열")
def _():
    from nervterm.plugins import _parse_toml_mini
    got = _parse_toml_mini(
        '[plugin]\n'
        'id = "demo"        # 주석\n'
        "name = '데모' # 또 주석\n"
        'color = "#ff0000"\n'
        'tags = [ "a#b", "c" ]\n'
        'count = 3 # 숫자\n'
        'flag = true\n'
        '[character]\n'
        'world = "nerv"      # 이 팩이 전제하는 세계관\n')
    eq(got["plugin"]["id"], "demo", "따옴표 값 뒤 인라인 주석")
    eq(got["plugin"]["name"], "데모", "싱글쿼트 뒤 주석")
    eq(got["plugin"]["color"], "#ff0000", "따옴표 안 # 은 값")
    eq(got["plugin"]["tags"], ["a#b", "c"], "배열 원소 안 #")
    eq(got["plugin"]["count"], 3, "주석 딸린 정수")
    eq(got["plugin"]["flag"], True, "불리언")
    eq(got["character"]["world"], "nerv", "문서 예제 그대로")


@check("plugin.toml 미니 파서 — tomllib 과 같은 결과 (동봉 플러그인 전부)")
def _():
    from nervterm import plugins
    try:
        import tomllib
    except ImportError:
        return                    # 3.9/3.10 — 비교 대상이 없다
    for base, _src in plugins.search_paths():
        if not base.is_dir():
            continue
        for mf in sorted(base.glob("*/plugin.toml")):
            text = mf.read_text(encoding="utf-8")
            eq(plugins._parse_toml_mini(text), tomllib.loads(text), str(mf))


@check("깨진 plugin.toml — 사라지지 않고 사유가 남는다")
def _():
    import shutil
    from nervterm import identity, plugins
    broken = identity.data_dir() / "plugins" / "broken-pack"
    broken.mkdir(parents=True, exist_ok=True)
    # UTF-8 로 읽을 수 없는 바이트 — read_manifest 가 실제로 던지는 경로
    (broken / "plugin.toml").write_bytes(b"[plugin]\nid = \xff\xfe")
    try:
        plugins.discover(refresh=True)
        true(any(name == "broken-pack" for name, _ in plugins.PARSE_ERRORS),
             "PARSE_ERRORS 에 없다")
    finally:
        shutil.rmtree(broken, ignore_errors=True)
        plugins.discover(refresh=True)


@check("경제 — 일일 상한 절단과 총획득 보호")
def _():
    from nervterm import config, db, economy
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        room = max(0, config.DAILY_LCL_CAP - (db.daily_row(con)["lcl"] or 0))
        got, _ = economy.apply(con, lcl=config.DAILY_LCL_CAP * 2, kind="test")
        eq(got, room, "일일 상한을 넘겨 적립됐다")
        te = db.geti(con, "total_earned")
        economy.apply(con, lcl=-5, kind="test", respect_cap=False)
        eq(db.geti(con, "total_earned"), te, "음수 lcl 이 총획득을 깎았다")


@check("방치 — 감점 상한, 중복 방지, 복귀 리셋 (캐릭터를 찾아온 때 기준)")
def _():
    import datetime
    from nervterm import config, db, economy
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        past = (datetime.datetime.now()
                - datetime.timedelta(days=30)).isoformat(timespec="seconds")
        db.put(con, "last_seen", past)
        # 에이전트로 매일 일했어도(전역 last_active 가 지금) 레이는 서운하다
        economy.touch_activity(con)
        days, pen = economy.settle_neglect(con)
        true(days >= 29 and pen < 0, f"방치가 감점되지 않았다 ({days}, {pen})")
        true(pen >= config.AFF_NEGLECT_CAP, "상한을 넘어 깎았다")
        _, pen2 = economy.settle_neglect(con)
        eq(pen2, 0, "같은 방치를 두 번 감점")
        economy.touch_seen(con)
        eq(economy.settle_neglect(con), (0, 0), "복귀 후에도 방치로 봤다")
        eq(db.geti(con, "neglect_total"), 0, "복귀했는데 누적이 리셋 안 됨")
        # 만난 적 없는 사람은 서운할 이유가 없다
        db.put(con, "last_seen", "", char="misato")
        eq(economy.settle_neglect(con, char="misato"), (0, 0),
           "한 번도 안 만난 사람이 방치 감점을 받았다")


@check("지루함 — 2글자 정상어는 통과, 성의 없는 것만 잡는다")
def _():
    from nervterm import db, stance
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        eq(stance.check_boring(con, "안녕"), "", "정상 인사를 처벌")
        eq(stance.check_boring(con, "미안"), "", "정상 사과를 처벌")
        true(stance.check_boring(con, "ㅇㅇ"), "성의 없는 답을 통과시켰다")
        true(stance.check_boring(con, "ㅋ"), "1글자를 통과시켰다")


@check("거절 — 인내가 없으면 거절한다")
def _():
    from nervterm import config, db, stance
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        keep = db.geti(con, "patience")
        try:
            db.put(con, "patience", 0)
            true(stance.refuses(con, need=10, what="선물"),
                 "인내 0 인데 받아줬다")
            db.put(con, "patience", 80)
            db.put(con, "trust", 50)
            db.put(con, "interest", 50)
            eq(stance.refuses(con, need=10, what="선물"), None,
               "상태가 멀쩡한데 거절했다")
        finally:
            db.put(con, "patience", keep)


@check("약속 — 감점은 1회, 기한이 지나면 잊는다")
def _():
    import datetime
    from nervterm import config, db, stance
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        db.put(con, "trust", 60)

        def add(text, days_ago):
            ts = (datetime.datetime.now() - datetime.timedelta(days=days_ago)
                  ).isoformat(timespec="seconds")
            con.execute("INSERT INTO memory(player,char,ts,kind,text) "
                        "VALUES(?,?,?,?,?)",
                        (db.PLAYER, "rei", ts, "promise", text))

        add("수족관에 같이 간다", config.PROMISE_GRACE_DAYS + 2)
        add("옥상에 간다",
            config.PROMISE_GRACE_DAYS + config.PROMISE_FORGET_DAYS + 1)
        eq(stance.settle_promises(con), 2, "감점 건수")
        eq(db.geti(con, "trust"), 60 + config.TRUST_BROKEN_PROMISE * 2)
        eq(stance.settle_promises(con), 0, "같은 약속을 두 번 감점")
        broken = stance.check_broken_promises(con)
        eq([t for t, _ in broken], ["수족관에 같이 간다"],
           "기한 지난 약속이 잊히지 않았다")


@check("llm.ask — 가짜 프로바이더로 예산 차감·JSON 연결")
def _():
    from nervterm import db, llm

    class Fake(llm.Provider):
        id = "fake"
        label = "fake"
        billing = llm.BILLING_NONE

        def __init__(self):
            super().__init__({})

        def available(self):
            return True, ""

        def complete(self, system, user, timeout=None, schema=None):
            return '{"line": "응.", "affection_delta": 99}'

    orig = llm.current
    llm.current = lambda: Fake()
    try:
        with db.session() as con:
            db.init(con)
            before = db.daily_row(con)["llm"] or 0
            got = llm.ask(con, "sys", "user")
            eq(got["line"], "응.", "응답 JSON 이 연결되지 않았다")
            eq(db.daily_row(con)["llm"], before + 1, "호출 카운트")
            eq(llm.normalize(got, clamp=3)["affection_delta"], 3, "클램프")
    finally:
        llm.current = orig


# ═══════════════════════════════════════════════════════════════════════
#  락 · 트랜잭션 — eva 를 켜 둬도 훅이 막히지 않는다
# ═══════════════════════════════════════════════════════════════════════
def _fake_provider(reply=None, *, seen=None, schema_seen=None):
    """가짜 프로바이더. reply 는 dict 또는 (system, user, schema) → dict."""
    from nervterm import llm

    class Fake(llm.Provider):
        id = "fake"
        label = "fake"
        billing = llm.BILLING_NONE

        def __init__(self):
            super().__init__({})

        def available(self):
            return True, ""

        def complete(self, system, user, *, timeout=None, schema=None):
            self.calls = 1
            if seen is not None:
                seen.append((system, user, schema))
            got = reply(system, user, schema) if callable(reply) else reply
            return json.dumps(got if got is not None else {
                "line": "…그래.", "emotion": "neutral"}, ensure_ascii=False)
    return Fake


def _with_provider(klass, fn):
    from nervterm import llm
    orig = llm.current
    llm.current = lambda: klass()
    try:
        return fn()
    finally:
        llm.current = orig


def _hook(payload: dict) -> float:
    """실제 훅을 프로세스로 띄운다. 걸린 시간(초)."""
    import subprocess
    import time
    t = time.monotonic()
    subprocess.run([sys.executable, "-m", "nervterm", "hook"],
                   input=json.dumps(payload), text=True, cwd=str(ROOT),
                   timeout=30, env={**os.environ})
    return time.monotonic() - t


@check("락 — 게임이 화면을 그리고 기다리는 동안 훅이 막히지 않는다")
def _():
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        g = game.Game(con, char, offline=True, animate=False)
        g.state()                 # redraw() 가 입력 대기 직전에 부르는 것
        true(not con.in_transaction, "화면을 그린 뒤 쓰기 트랜잭션이 열려 있다")
        before = db.geti(con, "lcl")
        took = _hook({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                      "tool_input": {}, "tool_response": {},
                      "session_id": "lock"})
        true(took < 1.5, f"훅이 {took:.1f}초 막혔다")
        true(db.geti(con, "lcl") > before, "게임이 켜진 동안 적립이 사라졌다")


@check("락 — LLM 응답을 기다리는 동안 쓰기 락을 쥐지 않는다")
def _():
    from nervterm import db, llm, recall
    holding = []

    def reply(system, user, schema):
        holding.append(_CON[0].in_transaction)
        return {"line": "응."}
    _CON = []
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        _CON.append(con)
        recall.remember(con, "fact", "상대는 커피를 많이 마신다")
        recall.relevant(con, "커피")          # UPDATE hits — 트랜잭션을 연다
        _with_provider(_fake_provider(reply),
                       lambda: llm.ask(con, "sys", "user"))
    eq(holding, [False], "응답을 기다리는 동안 트랜잭션이 열려 있었다")


@check("락 — daily_row 는 읽기만 한다")
def _():
    from nervterm import db
    with db.session() as con:
        db.init(con)
        row = db.daily_row(con, "1999-01-01")
        eq(row["tools"], 0, "없는 날은 0")
        true(not con.in_transaction, "읽기가 트랜잭션을 열었다")
        eq(con.execute("SELECT COUNT(*) FROM daily WHERE day='1999-01-01'"
                       ).fetchone()[0], 0, "읽기가 행을 만들었다")


@check("tx — 실패하면 묶은 문장이 전부 되돌려진다")
def _():
    from nervterm import db
    with db.session() as con:
        db.init(con)
        db.put(con, "lcl", 100)
        try:
            with db.tx(con):
                db.put(con, "lcl", 1)
                raise RuntimeError("중간에 죽었다")
        except RuntimeError:
            pass
        eq(db.geti(con, "lcl"), 100, "절반만 반영됐다")


@check("bump — 한 문장이라 상하한도 그 안에서 지킨다")
def _():
    from nervterm import db
    with db.session() as con:
        db.init(con)
        db.set_char("rei")
        db.put(con, "patience", 95)
        eq(db.bump(con, "patience", 30, lo=0, hi=100), 100, "상한")
        eq(db.bump(con, "patience", -500, lo=0, hi=100), 0, "하한")
        eq(db.bump(con, "새키", 3), 3, "없던 키")


# ═══════════════════════════════════════════════════════════════════════
#  기억 압축 — 스키마를 강제하는 프로바이더에서도
# ═══════════════════════════════════════════════════════════════════════
@check("기억 압축 — facts 스키마로 부르고, 실패하면 표시를 옮기지 않는다")
def _():
    from nervterm import characters, db, game, llm, world
    world.load(refresh=True)
    seen = []
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        for i in range(32):
            db.say(con, "user" if i % 2 == 0 else "rei", f"말 {i}번째", "", "s")
        mark0 = db.geti(con, "consolidated_upto")
        g = game.Game(con, char, offline=False, animate=False, headless=True)

        # 1) 프로바이더가 엉뚱한 모양을 내면(스키마 강제 실패) 표시 유지
        _with_provider(_fake_provider({"line": "캐릭터 응답 모양"}, seen=seen),
                       g.consolidate)
        true(seen[-1][2] is llm.FACTS_SCHEMA, "facts 스키마로 부르지 않았다")
        eq(db.geti(con, "consolidated_upto"), mark0,
           "실패했는데 표시를 옮겨 그 대화가 영영 기억이 못 된다")

        # 2) 제대로 오면 기억이 생기고 약속에는 대상이 붙는다
        facts = {"facts": [
            {"text": "다음에 수족관에 같이 가기로 했다", "kind": "promise",
             "target": "date:aquarium"},
            {"text": "상대는 고양이 두 마리를 키운다", "kind": "fact",
             "target": ""}]}
        made = _with_provider(_fake_provider(facts), g.consolidate)
        eq(made, 2, "만든 기억 수")
        true(db.geti(con, "consolidated_upto") > mark0, "표시가 안 옮겨졌다")
        row = con.execute("SELECT target FROM memory WHERE kind='promise' "
                          "AND text=? AND char='rei'",
                          ("다음에 수족관에 같이 가기로 했다",)).fetchone()
        eq(row["target"], "date:aquarium", "약속의 이행 대상")


# ═══════════════════════════════════════════════════════════════════════
#  근무 일지 — 날짜는 로컬 기준
# ═══════════════════════════════════════════════════════════════════════
@check("근무 일지 — UTC 시각을 로컬 날짜로 (한국 오전 근무가 '어제' 가 되지 않는다)")
def _():
    import time
    from nervterm import agents
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    try:
        day, ts = agents.local_time("2026-08-21T23:30:00.123Z")
        eq(day, "2026-08-22", "UTC 23:30 은 한국 08:30 — 다음 날")
        true(ts.startswith("2026-08-22T08:30"), f"로컬 시각: {ts}")
        got = agents.get("claude").harvest(
            {"type": "user", "promptSource": "typed",
             "timestamp": "2026-08-21T23:30:00Z",
             "message": {"content": "아침에 친 프롬프트"}}, "s")
        eq({d for d, *_ in got}, {"2026-08-22"}, "harvest 의 날짜")
        got = agents.get("codex").harvest(
            {"timestamp": "2026-08-21T23:30:00Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "코덱스 아침"}},
            "s")
        eq({d for d, *_ in got}, {"2026-08-22"}, "Codex harvest 의 날짜")
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


# ═══════════════════════════════════════════════════════════════════════
#  조사
# ═══════════════════════════════════════════════════════════════════════
@check("조사 — 받침 있는 이름이 와도 프롬프트 문장이 안 깨진다")
def _():
    from nervterm import characters, persona, spec
    from nervterm.hangul import josa
    for word, pair, want in (("렘", "이/가", "렘이"), ("레이", "이/가", "레이가"),
                             ("람", "으로서/로서", "람으로서"),
                             ("서울", "으로/로", "서울로"),
                             ("동화", "이/가", "동화가"),
                             ("LCL", "이/가", "LCL이"),
                             ("미사토", "과/와", "미사토와")):
        eq(josa(word, pair), want, f"{word}+{pair}")
    rei = characters.get("rei")
    rem = spec.Character(**{**rei.__dict__, "id": "rem", "name": "렘"})
    text = persona.rules(rem) + persona.impression_rules("렘")
    for bad in ("렘가 ", "렘는 ", "렘를 ", "렘로서"):
        true(bad not in text, f"'{bad}' 가 프롬프트에 남았다")
    true("렘은 비위를" in text, "은/는")


# ═══════════════════════════════════════════════════════════════════════
#  약속 — 지킬 수 있다
# ═══════════════════════════════════════════════════════════════════════
def _fresh_promises(con, char):
    """시험끼리 저장소를 같이 쓴다 — 앞 시험의 약속이 끼지 않게."""
    from nervterm import db
    db.set_char(char)
    con.execute("DELETE FROM memory WHERE player=? AND char=? "
                "AND kind='promise'", (db.PLAYER, char))


def _promise_age(con, pid, days=0, hours=0):
    import datetime
    ts = (datetime.datetime.now() - datetime.timedelta(days=days, hours=hours)
          ).isoformat(timespec="seconds")
    con.execute("UPDATE memory SET ts=? WHERE id=?", (ts, pid))


@check("약속 — 약속한 곳에 가고 약속한 것을 주면 지킨 것이다")
def _():
    from nervterm import characters, config, db, stance
    with db.session() as con:
        db.init(con)
        _fresh_promises(con, "rei")
        rei = characters.get("rei")
        db.put(con, "trust", 30)
        pid = stance.make_promise(con, "다음에 옥상에 같이 간다", "date:roof", rei)
        true(pid, "약속이 안 생겼다")
        eq(stance.fulfil(con, "date", "aquarium"), [], "다른 곳은 아니다")
        eq(stance.fulfil(con, "date", "roof"), ["다음에 옥상에 같이 간다"],
           "약속한 곳에 갔는데 지킨 걸로 안 쳤다")
        eq(db.geti(con, "trust"), 30 + config.TRUST_KEPT_PROMISE, "보상")
        eq(stance.open_promises(con), [], "지킨 약속이 남아 있다")
        gid = stance.make_promise(con, "목도리를 사다 준다", "gift:scarf", rei)
        eq(stance.fulfil(con, "gift", "scarf"), ["목도리를 사다 준다"], "선물")
        true(gid, "선물 약속")
        # 모르는 대상은 말로만 한 약속으로
        eq(stance.clean_target("date:없는곳", rei), "", "모르는 장소")
        eq(stance.clean_target("DATE:roof ", rei), "date:roof", "정규화")


@check("약속 — '또 올게' 는 충분히 지나서 찾아와야 지킨 것이다")
def _():
    from nervterm import db, stance
    with db.session() as con:
        db.init(con)
        _fresh_promises(con, "asuka")
        pid = stance.make_promise(con, "내일 또 온다", "visit")
        eq(stance.fulfil(con, "visit"), [], "약속하자마자 지킨 걸로 쳤다")
        _promise_age(con, pid, hours=20)
        eq(stance.fulfil(con, "visit"), ["내일 또 온다"], "다시 왔는데 안 쳐 줬다")


@check("약속 — '쉬겠다' 는 그 밤의 근무 기록으로 판정한다")
def _():
    import datetime
    from nervterm import db, stance
    with db.session() as con:
        db.init(con)
        _fresh_promises(con, "misato")
        yesterday = datetime.datetime.now() - datetime.timedelta(days=2)
        kept = stance.make_promise(con, "오늘은 일찍 잔다", "rest")
        broke = stance.make_promise(con, "오늘 밤엔 쉰다고 했다", "rest")
        for pid in (kept, broke):
            con.execute("UPDATE memory SET ts=? WHERE id=?",
                        (yesterday.replace(hour=20).isoformat(
                            timespec="seconds"), pid))
        night = (yesterday + datetime.timedelta(days=1)).replace(
            hour=3, minute=0, second=0)
        # 두 번째 약속만 그 밤에 일했다 — 같은 밤이라 둘 다 깨져야 맞지만
        # 시험을 위해 첫 약속의 밤을 다른 날로 민다.
        con.execute("UPDATE memory SET ts=? WHERE id=?",
                    ((yesterday - datetime.timedelta(days=3)).replace(
                        hour=20).isoformat(timespec="seconds"), kept))
        con.execute("INSERT INTO ledger(player,char,ts,kind,delta_lcl,"
                    "delta_aff,reason) VALUES(?,?,?,?,?,?,?)",
                    (db.PLAYER, "", night.isoformat(timespec="seconds"),
                     "tool", 5, 0, "Edit"))
        k, b = stance.check_rest(con)
        eq(k, ["오늘은 일찍 잔다"], "쉰 밤을 못 알아봤다")
        eq(b, ["오늘 밤엔 쉰다고 했다"], "새벽에 일했는데 지킨 걸로 쳤다")


@check("약속 — 말로 한 약속만 대화로 지킬 수 있다")
def _():
    from nervterm import db, stance
    with db.session() as con:
        db.init(con)
        _fresh_promises(con, "rei")
        word = stance.make_promise(con, "커피를 줄이기로 했다", "")
        act = stance.make_promise(con, "도서관에 같이 간다", "date:library")
        eq(stance.keep_by_word(con, act), "", "행동이 필요한 약속을 말로 지켰다")
        eq(stance.keep_by_word(con, str(word)), "커피를 줄이기로 했다",
           "말로 한 약속")
        eq(stance.keep_by_word(con, "99999"), "", "없는 번호")


@check("약속 — 기한을 넘기면 감점 1회, 오래되면 잊는다")
def _():
    from nervterm import config, db, stance
    with db.session() as con:
        db.init(con)
        _fresh_promises(con, "misato")
        db.put(con, "trust", 60)
        a = stance.make_promise(con, "수족관에 같이 간다")
        b = stance.make_promise(con, "옥상에 간다", "")
        _promise_age(con, a, days=config.PROMISE_GRACE_DAYS + 2)
        _promise_age(con, b, days=config.PROMISE_GRACE_DAYS
                     + config.PROMISE_FORGET_DAYS + 1)
        eq(stance.settle_promises(con), 2, "감점 건수")
        eq(db.geti(con, "trust"), 60 + config.TRUST_BROKEN_PROMISE * 2)
        eq(stance.settle_promises(con), 0, "같은 약속을 두 번 감점")
        eq([t for t, _ in stance.check_broken_promises(con)],
           ["수족관에 같이 간다"], "기한 지난 약속이 잊히지 않았다")


@check("약속 — 응답에 실린 약속이 생기고, 같은 말로 기한이 늘지 않는다")
def _():
    from nervterm import characters, db, stance
    with db.session() as con:
        db.init(con)
        _fresh_promises(con, "rei")
        rei = characters.get("rei")
        got = stance.apply_response(con, {
            "promise": "다음 주에 병실에 문병 온다",
            "promise_target": "date:ward"}, rei)
        eq(got.get("promise_made"), "다음 주에 병실에 문병 온다", "약속")
        pid, *_ = stance.open_promises(con)[0]
        _promise_age(con, pid, days=3)
        again = stance.apply_response(con, {
            "promise": "다음 주에 병실에 문병 온다", "promise_target": ""}, rei)
        true("promise_made" not in again, "같은 약속을 새로 만들었다")
        eq(stance.open_promises(con)[0][3], 3, "같은 말로 기한이 늘었다")


@check("약속 — v5 승격이 옛 플래그를 상태로 옮긴다")
def _():
    from nervterm import db
    with db.session() as con:
        db.init(con)
        con.execute("INSERT INTO memory(player,char,ts,kind,text) "
                    "VALUES(?,?,?,?,?)", (db.PLAYER, "rei", db.now(),
                                          "promise", "옛 약속"))
        pid = con.execute("SELECT MAX(id) FROM memory").fetchone()[0]
        db.flag(con, f"promise_penalized_{pid}", "1", char="rei")
        db._upgrade_v5(con)
        eq(con.execute("SELECT status FROM memory WHERE id=?",
                       (pid,)).fetchone()[0], "broken", "감점된 옛 약속")


# ═══════════════════════════════════════════════════════════════════════
#  경제 — 커밋 호감 상한
# ═══════════════════════════════════════════════════════════════════════
@check("경제 — 커밋은 호감을 주지 않고, 신뢰만 하루 상한 안에서")
def _():
    from nervterm import config, db, economy
    with db.session() as con:
        db.init(con)
        db.put(con, "met_count", 1, char="rei")
        db.put(con, "met_count", 0, char="asuka")
        db.put(con, "cap_commit_trust", "", char="rei")
        db.put(con, "trust", 20, char="rei")
        db.put(con, "trust", 20, char="asuka")
        rei0 = db.geti(con, "affection", char="rei")
        asuka0 = db.geti(con, "affection", char="asuka")
        for _ in range(10):
            economy.on_tool(con, tool="Bash",
                            tool_input={"command": 'git commit -m "x"'},
                            tool_response="", ok=True)
        eq(db.geti(con, "affection", char="rei"), rei0,
           "대화 없이 커밋만으로 호감이 올랐다")
        eq(db.geti(con, "affection", char="asuka"), asuka0, "안 만난 사람")
        eq(db.geti(con, "trust", char="rei"),
           20 + config.TRUST_COMMIT_DAILY_MAX, "신뢰 하루 상한")
        eq(db.geti(con, "trust", char="asuka"), 20, "안 만난 사람의 신뢰")


@check("인사 — 상대가 아무것도 안 했으니 수치가 움직이지 않는다")
def _():
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("misato")
        db.set_char(char.id)
        before = {k: db.geti(con, k) for k in
                  ("affection", "trust", "interest", "patience")}
        db.put(con, "last_seen", db.now())        # 방치·회복이 끼지 않게
        db.put(con, "patience_ts", db.now())
        g = game.Game(con, char, offline=False, animate=False, headless=True)
        _with_provider(_fake_provider({
            "line": "어라~ 왔네?", "affection_delta": 3, "trust_delta": 5,
            "interest_delta": 4, "patience_delta": 2}), g.greet)
        after = {k: db.geti(con, k) for k in before}
        eq(after, before, "인사만으로 수치가 움직였다 — 껐다 켜기로 올릴 수 있다")


@check("v6 승격 — 커밋 몫만 빼고 다시 쌓는다, 두 번 돌아도 같다")
def _():
    from nervterm import db
    with db.session() as con:
        db.init(con)
        p, c = "fixtest", "rei"
        con.execute("DELETE FROM ledger WHERE player=?", (p,))
        con.execute("DELETE FROM dialogue WHERE player=?", (p,))
        for key, value in (("affection", 100), ("trust", 100)):
            con.execute("INSERT OR REPLACE INTO state(player,char,key,value) "
                        "VALUES(?,?,?,?)", (p, c, key, str(value)))
        con.execute("INSERT INTO dialogue(player,char,ts,role,text,emotion,"
                    "sess) VALUES(?,?,?,?,?,?,?)",
                    (p, c, "2026-08-01T10:00:00", "rei", "…", "", "s"))
        rows = [("commit", 2, "2026-08-01"), ("talk", 3, "2026-08-01"),
                ("commit", 2, "2026-08-02"), ("neglect", -15, "2026-08-03"),
                ("date", 4, "2026-08-04"), ("commit", 2, "2026-08-04")]
        for kind, d, day in rows:
            con.execute("INSERT INTO ledger(player,char,ts,kind,delta_lcl,"
                        "delta_aff,reason) VALUES(?,?,?,?,0,?,?)",
                        (p, c, f"{day}T12:00:00", kind, d, kind))
        db._upgrade_v6(con)
        get = lambda k: int(con.execute(
            "SELECT value FROM state WHERE player=? AND char=? AND key=?",
            (p, c, k)).fetchone()[0])
        # 5 → +3 = 8 → -15 → 0(경계) → +4 = 4
        eq(get("affection"), 4, "커밋 몫을 뺀 재계산")
        eq(get("trust"), 10 + 3 * 2, "신뢰는 커밋한 날 × 하루 상한")
        db._upgrade_v6(con)
        eq(get("affection"), 4, "두 번 돌았더니 또 깎였다")
        eq(con.execute("SELECT COUNT(*) FROM ledger WHERE player=? "
                       "AND kind='correction'", (p,)).fetchone()[0], 1,
           "회수 기록")
        con.execute("DELETE FROM ledger WHERE player=?", (p,))
        con.execute("DELETE FROM state WHERE player=?", (p,))
        con.execute("DELETE FROM dialogue WHERE player=?", (p,))


@check("경제 — Codex 의 apply_patch 도 파일 수정으로 친다")
def _():
    from nervterm import config, db, economy
    with db.session() as con:
        db.init(con)
        edits0 = db.daily_row(con)["edits"]
        got = dict(economy.on_tool(con, tool="apply_patch",
                                   tool_input={"command": "*** Begin Patch"},
                                   tool_response="", ok=True))
        eq(db.daily_row(con)["edits"], edits0 + 1, "수정 횟수")
        true(got.get("lcl", 0) in (config.TOOL_REWARD["apply_patch"], 0),
             f"적립: {got}")


# ═══════════════════════════════════════════════════════════════════════
#  근무 사건
# ═══════════════════════════════════════════════════════════════════════
@check("근무 사건 — 새벽·회복·종일·새 저장소를 한 번씩 남긴다")
def _():
    import datetime
    from nervterm import config, db, events
    with db.session() as con:
        db.init(con)
        con.execute("DELETE FROM work_events WHERE player=?", (db.PLAYER,))
        night = datetime.datetime(2026, 9, 2, 3, 10)   # 수요일 새벽
        got = events.after_tool(con, ok=True, tested=True, committed=False,
                                prev_fail_streak=config.EVENT_RECOVER_FAILS,
                                when=night)
        true("late_night" in got, f"새벽: {got}")
        true("recovered" in got, f"회복: {got}")
        again = events.after_tool(con, ok=True, tested=True, committed=False,
                                  prev_fail_streak=5, when=night)
        eq(again, [], "같은 날 같은 사건을 또 남겼다")

        # 새 저장소 — 설치 직후에는 알리지 않는다
        db.put(con, "created", db.now())
        eq(events.after_tool(con, ok=True, tested=False, committed=False,
                             prev_fail_streak=0, cwd="/x/첫저장소"), [],
           "설치 직후의 저장소를 새것이라 했다")
        old = (datetime.datetime.now() - datetime.timedelta(days=30)
               ).isoformat(timespec="seconds")
        db.put(con, "created", old)
        got = events.after_tool(con, ok=True, tested=False, committed=False,
                                prev_fail_streak=0, cwd="/x/둘째저장소")
        true("new_project" in got, f"새 저장소: {got}")
        eq(events.after_tool(con, ok=True, tested=False, committed=False,
                             prev_fail_streak=0, cwd="/x/둘째저장소"), [],
           "아는 저장소를 또 새것이라 했다")
        true(events.streak(con, 7), "연속 접속 고비")
        true(not events.streak(con, 8), "고비가 아닌 날")
        lines = events.lines(events.recent(con, days=4000))
        true(any("새벽 3시" in x for x in lines), f"줄: {lines}")


@check("근무 사건 — 훅이 실제로 남기고, 상태줄에 한 마디를 건다")
def _():
    from nervterm import characters, db, widget, world
    w = world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        rei = characters.get("rei")
        widget.remember(con, rei, w, "관심")
        db.put(con, "widget_quip_ts", "", char="rei")
    _hook({"hook_event_name": "Stop", "session_id": "q"})
    with db.session() as con:
        quip = db.get(con, "widget_quip", char="rei")
        true(quip in rei.quips["stop"], f"Stop 한 마디: {quip!r}")
    got = widget.render()
    true(quip in got, "상태줄이 새 한 마디를 안 보여준다")


@check("상태줄 — 단계는 그 자리에서 계산한다 (훅이 호감을 바꿔도 맞다)")
def _():
    from nervterm import characters, db, widget, world
    w = world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        rei = characters.get("rei")
        db.put(con, "affection", 3, char="rei")
        widget.remember(con, rei, w, "무관심")
        db.put(con, "affection", 70, char="rei")     # 게임을 끈 뒤 바뀜
    true("애착" in widget.render(), "캐시된 옛 단계가 떴다")


# ═══════════════════════════════════════════════════════════════════════
#  방치 — 일은 했는데 안 찾아왔다
# ═══════════════════════════════════════════════════════════════════════
@check("방치 — 안 찾아온 동안 단말에서 일한 날을 센다")
def _():
    import datetime
    from nervterm import db, economy
    with db.session() as con:
        db.init(con)
        db.set_char("asuka")
        last = datetime.datetime.now() - datetime.timedelta(days=5)
        db.put(con, "last_seen", last.isoformat(timespec="seconds"))
        for back in (4, 3, 2):
            day = (datetime.date.today()
                   - datetime.timedelta(days=back)).isoformat()
            db.daily_bump(con, "tools", 10, day=day)
        eq(economy.worked_while_away(con), 3, "일한 날 수")
        economy.touch_seen(con)
        eq(economy.worked_while_away(con), 0, "찾아왔는데도 센다")


# ═══════════════════════════════════════════════════════════════════════
#  eva say — 게임과 같은 규칙을 지난다
# ═══════════════════════════════════════════════════════════════════════
@check("eva say — 지루함 감점과 인내 회복이 똑같이 적용된다")
def _():
    import datetime
    from nervterm import characters, config, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("misato")
        db.set_char(char.id)
        db.put(con, "patience", 20)
        db.put(con, "interest", 50)
        db.put(con, "patience_ts", (datetime.datetime.now()
                                    - datetime.timedelta(hours=5)
                                    ).isoformat(timespec="seconds"))
        g = game.Game(con, char, offline=True, animate=False, headless=True)
        g.settle()
        true(db.geti(con, "patience") > 20, "say 경로에서 인내가 안 돈다")
        i0 = db.geti(con, "interest")
        g.talk("ㅇㅇ")
        eq(db.geti(con, "interest"), i0 + config.INTEREST_BORING,
           "say 경로에서 지루함 감점이 빠졌다")
        true(any(e.role == "rei" for e in g.buf), "대답이 없다")


# ═══════════════════════════════════════════════════════════════════════
#  캐릭터 간 인지
# ═══════════════════════════════════════════════════════════════════════
@check("캐릭터 간 인지 — 같은 세계 사람만, 보이는 일만 안다")
def _():
    from nervterm import characters, db, settings, social, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        con.execute("DELETE FROM social WHERE player=?", (db.PLAYER,))
        db.put(con, "last_seen", "", char="asuka")
        social.log(con, "rei", "date", "aquarium", "수족관")
        social.log(con, "rei", "gift", "scarf", "목도리")
        asuka = characters.get("asuka")
        text = social.block(con, asuka)
        true("레이와 수족관에 갔다" in text, f"데이트: {text}")
        true("목도리를 줬다" in text, "선물")
        true("인형" in text, "아스카의 태도 지침이 안 실렸다")
        eq(social.block(con, characters.get("emilia")), "",
           "다른 세계 사람이 알았다")
        settings.put("social.aware", False)
        try:
            eq(social.block(con, asuka), "", "꺼도 실렸다")
        finally:
            settings.put("social.aware", True)


@check("캐릭터 간 인지 — 지난번에 만난 뒤의 일만")
def _():
    import datetime
    from nervterm import characters, db, social
    with db.session() as con:
        db.init(con)
        con.execute("DELETE FROM social WHERE player=?", (db.PLAYER,))
        social.log(con, "misato", "date", "jazz", "재즈 바")
        db.put(con, "last_seen", (datetime.datetime.now()
                                  + datetime.timedelta(minutes=1)
                                  ).isoformat(timespec="seconds"), char="rei")
        true("재즈 바" not in social.block(con, characters.get("rei")),
             "이미 만난 뒤에 알았던 일을 또 꺼낸다")


# ═══════════════════════════════════════════════════════════════════════
#  프롬프트 — 고정부가 앞, 캐시가 맞는다
# ═══════════════════════════════════════════════════════════════════════
@check("프롬프트 — 고정부는 턴마다 바이트 단위로 같다")
def _():
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    seen = []
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        g = game.Game(con, char, offline=False, animate=False, headless=True)
        _with_provider(_fake_provider(seen=seen), lambda: (
            g.talk("안녕"), g.talk("오늘 좀 힘들었어")))
    first, second = seen[0][0], seen[1][0]
    true(isinstance(first, list) and len(first) == 2, "두 조각이 아니다")
    eq(first[0], second[0], "고정부가 턴마다 바뀐다 — 캐시가 안 맞는다")
    true("[출력 형식" in first[0], "출력 규칙이 고정부에 없다")
    true("[지금]" in first[1] and "[지금]" not in first[0], "시각은 가변부")


# ═══════════════════════════════════════════════════════════════════════
#  HTTP 프로바이더 — 재시도와 요청 모양
# ═══════════════════════════════════════════════════════════════════════
@check("Anthropic — 캐시 표시·effort·구조화 출력, 4xx 에만 맨몸 재시도")
def _():
    from nervterm.llm import base, http
    sent = []

    def fake_post(url, payload, headers, timeout):
        sent.append(payload)
        if len(sent) == 1:
            return None, 400
        return {"content": [{"type": "text", "text": '{"line":"응."}'}],
                "stop_reason": "end_turn"}, 200
    orig = http._post
    http._post = fake_post
    try:
        p = http.AnthropicAPI({})
        os.environ["ANTHROPIC_API_KEY"] = "test"
        got = p.complete(["고정", "가변"], "user", schema=base.FACTS_SCHEMA)
    finally:
        http._post = orig
        os.environ.pop("ANTHROPIC_API_KEY", None)
    eq(got, '{"line":"응."}', "응답")
    first = sent[0]
    eq(first["system"][0]["cache_control"], {"type": "ephemeral"}, "캐시 표시")
    eq(first["output_config"]["format"]["schema"], base.FACTS_SCHEMA,
       "호출한 스키마가 아니다")
    eq(first["output_config"]["effort"], "low", "effort")
    true("output_config" not in sent[1], "재시도는 맨몸이어야 한다")
    eq(p.calls, 2, "요청 수")


@check("Ollama — 타임아웃은 다시 보내지 않는다 (240초를 두 번 기다리지 않게)")
def _():
    from nervterm.llm import http
    sent = []

    def fake_post(url, payload, headers, timeout):
        sent.append(payload)
        return None, 0                  # 연결 실패·타임아웃
    orig = http._post
    http._post = fake_post
    try:
        p = http.Ollama({"models": {"ollama": "x"}})
        eq(p.complete("s", "u"), None, "실패")
    finally:
        http._post = orig
    eq(len(sent), 1, "타임아웃 뒤에 또 보냈다")


@check("유료 상한 — 재시도한 요청도 센다")
def _():
    from nervterm import db, llm
    from nervterm.llm import guard

    class Paid(llm.Provider):
        id = "paid"
        label = "paid"
        billing = llm.BILLING_API

        def __init__(self):
            super().__init__({})

        def available(self):
            return True, ""

        def complete(self, system, user, *, timeout=None, schema=None):
            self.calls = 2
            return '{"line":"응."}'
    with db.session() as con:
        db.init(con)
        before = guard.used_today(con)
        _with_provider(Paid, lambda: llm.ask(con, "s", "u"))
        eq(guard.used_today(con), before + 2, "돈이 나간 요청 수와 다르다")


# ═══════════════════════════════════════════════════════════════════════
#  캐릭터 계약 — 새 필드
# ═══════════════════════════════════════════════════════════════════════
@check("캐릭터 계약 — 돌봄·에피소드·한 마디·금지 표현을 검사한다")
def _():
    from nervterm import characters, spec
    rei = characters.get("rei")

    def broken(**over):
        clone = spec.Character(**{**rei.__dict__, **over})
        try:
            spec.validate_character(clone)
        except spec.SpecError as exc:
            return str(exc)
        return ""
    true(broken(quips={"comit": ["오타"]}), "모르는 한 마디 종류를 통과시켰다")
    true(broken(episodes=[("a", "b", 1, 2, 3)]), "모자란 에피소드")
    true(broken(care={"x": ("이름", 0, 3, "의미")}), "가격 0 인 돌봄")
    true(broken(forbidden="[깨진"), "깨진 정규식")
    eq(broken(), "", "멀쩡한 걸 거부했다")
    for cid in characters.IDS:
        c = characters.get(cid)
        for kind in spec.QUIP_KINDS:
            true(c.quips.get(kind), f"{cid}: '{kind}' 한 마디가 없다")
        import re
        pattern = re.compile(c.forbidden) if c.forbidden else None
        for kind, lines in c.quips.items():
            for line in lines:
                true(pattern is None or not pattern.search(line),
                     f"{cid} 의 한 마디가 자기 말투 규칙을 어긴다: {line}")


@check("돌봄 — 사면 며칠 살아 있고, 그동안 관심이 식지 않는다")
def _():
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        db.put(con, "lcl", 1000)
        db.put(con, "care_water", "")
        g = game.Game(con, char, offline=True, animate=False, headless=True)
        g.care("water")
        active = g.care_active()
        eq([k for k, *_ in active], ["water"], "돌봄이 안 켜졌다")
        eq(active[0][2], char.care["water"][2] - 1, "남은 일수")
        g.care("water")                # 이어서 하면 늘어난다
        eq(g.care_active()[0][2], char.care["water"][2] * 2 - 1, "연장")
        true(g.patience_boost() > 1, "인내 회복 배율")


@check("에피소드 — 순서대로 열리고, 한 번뿐이다")
def _():
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("asuka")
        db.set_char(char.id)
        for key, *_ in char.episodes:
            db.flag(con, f"episode_done_{key}", "")
        db.put(con, "affection", 100)
        db.put(con, "trust", 100)
        db.put(con, "patience", 100)
        db.put(con, "interest", 100)
        db.put(con, "lcl", 10000)
        g = game.Game(con, char, offline=True, animate=False, headless=True)
        items = g.episode_items(g.state())
        eq([opened for _, opened, _, _ in items], [True, False, False],
           "앞의 이야기 없이 뒤가 열렸다")
        g.pick_action = lambda choices: choices[0]
        g.episode(char.episodes[0][0])
        true(db.flag(con, f"episode_done_{char.episodes[0][0]}"),
             "끝까지 했는데 완료가 아니다")
        items = g.episode_items(g.state())
        eq([opened for _, opened, _, _ in items], [True, True, False],
           "다음 이야기가 안 열렸다")
        money = db.geti(con, "lcl")
        g.episode(char.episodes[0][0])
        eq(db.geti(con, "lcl"), money, "이미 한 이야기에 값을 받았다")


# ═══════════════════════════════════════════════════════════════════════
#  독립 검토에서 나온 것 — 실제 흐름(Game)으로 시험한다
# ═══════════════════════════════════════════════════════════════════════
@check("방치 — 한 번 길게 비운 뒤에도 다음 부재는 다시 감점된다")
def _():
    import datetime
    from nervterm import characters, db, game, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)

        def absent(days):
            db.put(con, "last_seen", (datetime.datetime.now()
                                      - datetime.timedelta(days=days)
                                      ).isoformat(timespec="seconds"))
            db.put(con, "affection", 50)
            g = game.Game(con, char, offline=True, animate=False,
                          headless=True)
            g.settle()
            return db.geti(con, "affection") - 50
        true(absent(10) < 0, "긴 부재")
        true(absent(5) < 0, "그보다 짧은 다음 부재가 감점되지 않았다")
        true(absent(12) < 0, "그 다음 부재도")


@check("캐릭터 간 인지 — 찾아온 뒤에도 그 전에 남들과 있었던 일이 실린다")
def _():
    import datetime
    from nervterm import characters, db, game, social, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        con.execute("DELETE FROM social WHERE player=?", (db.PLAYER,))
        db.put(con, "last_seen", (datetime.datetime.now()
                                  - datetime.timedelta(days=2)
                                  ).isoformat(timespec="seconds"),
               char="asuka")
        social.log(con, "rei", "date", "aquarium", "수족관")
        asuka = characters.get("asuka")
        db.set_char(asuka.id)
        g = game.Game(con, asuka, offline=True, animate=False, headless=True)
        g.settle()                       # last_seen 이 지금이 된다
        ctx = g.context(g.state())
        true("레이와 수족관에 갔다" in ctx,
             "찾아온 순간 last_seen 이 바뀌어 남들의 일이 사라졌다")


@check("기억 압축 — 없는 장소를 대상으로 한 약속은 말로 한 약속이 된다")
def _():
    from nervterm import characters, db, game, stance, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("misato")
        _fresh_promises(con, char.id)
        for i in range(32):
            db.say(con, "user" if i % 2 == 0 else "rei", f"이야기 {i}", "", "c")
        g = game.Game(con, char, offline=False, animate=False, headless=True)
        facts = {"facts": [
            {"text": "다음에 놀이공원에 같이 간다", "kind": "promise",
             "target": "date:themepark"},
            {"text": "오늘 밤엔 일찍 자기로 했다", "kind": "promise",
             "target": "rest"},
            {"text": "포장마차에 같이 가기로 했다", "kind": "promise",
             "target": "date:yatai"}]}
        _with_provider(_fake_provider(facts), g.consolidate)
        got = {text: target for _p, text, target, _a
               in stance.open_promises(con)}
        eq(got.get("다음에 놀이공원에 같이 간다"), "", "없는 장소가 대상으로 남았다")
        eq(got.get("오늘 밤엔 일찍 자기로 했다"), "",
           "언제 한 지 모르는 '쉬겠다' 를 밤으로 판정하려 했다")
        eq(got.get("포장마차에 같이 가기로 했다"), "date:yatai", "맞는 대상")
        pid = next(p for p, text, *_ in stance.open_promises(con)
                   if "놀이공원" in text)
        true(stance.keep_by_word(con, pid), "말로 지킬 길도 막혔다")


@check("새 저장소 — 승격 직후 원래 하던 저장소, 하위 폴더는 새것이 아니다")
def _():
    import datetime
    from nervterm import db, events
    root = Path(_TMP) / "repos" / "oldrepo"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    with db.session() as con:
        db.init(con)
        db.put(con, "created", (datetime.datetime.now()
                                - datetime.timedelta(days=90)
                                ).isoformat(timespec="seconds"))
        con.execute("DELETE FROM projects WHERE player=?", (db.PLAYER,))
        con.execute("INSERT OR IGNORE INTO work_facts(player,day,ts,kind,text)"
                    " VALUES(?,?,?,?,?)", (db.PLAYER, "2026-01-01",
                                           "2026-01-01T10:00:00", "project",
                                           "oldrepo (main)"))
        db._upgrade_v5(con)              # 승격이 기준선을 채운다
        eq(events.after_tool(con, ok=True, tested=False, committed=False,
                             prev_fail_streak=0, cwd=str(root)), [],
           "원래 하던 저장소를 새것이라 했다")
        eq(events.after_tool(con, ok=True, tested=False, committed=False,
                             prev_fail_streak=0, cwd=str(root / "src")), [],
           "하위 폴더를 새 저장소라 했다")
        eq(events.project_root(str(root / "src")), str(root), "뿌리 찾기")


# ═══════════════════════════════════════════════════════════════════════
#  Codex — 지금 판의 세션 기록, 승인 검토 스레드, 공평한 스캔 예산
# ═══════════════════════════════════════════════════════════════════════
@check("Codex — 지금 판(item_completed)에서 프롬프트·파일·커밋·제목을 뽑는다")
def _():
    from nervterm import agents
    a = agents.get("codex")
    ts = "2026-10-01T10:00:00Z"

    def item(it):
        return {"timestamp": ts, "type": "event_msg",
                "payload": {"type": "item_completed", "item": it}}
    kinds = lambda got: {k for _, _, k, _, _ in got}
    true("prompt" in kinds(a.harvest(item(
        {"type": "UserMessage", "content": [{"type": "text",
                                             "text": "이거 고쳐줘"}]}), "s")),
         "UserMessage")
    eq(a.harvest(item({"type": "UserMessage", "content": [
        {"type": "text", "text": "<environment_context> 주입"}]}), "s"), [],
       "주입 텍스트를 실적으로 셌다")
    got = a.harvest(item({"type": "FileChange", "status": "completed",
                          "changes": {"/a/b/main.py": {}}}), "s")
    eq([g[3] for g in got if g[2] == "file"], ["main.py"], "FileChange")
    got = a.harvest(item({"type": "CommandExecution", "status": "completed",
                          "command": ["/bin/zsh", "-lc",
                                      'git commit -m "고쳤다"']}), "s")
    eq([g[3] for g in got if g[2] == "commit"], ["고쳤다"], "CommandExecution")
    eq(a.harvest(item({"type": "CommandExecution", "status": "failed",
                       "command": ["git", "commit", "-m", "x"]}), "s"), [],
       "실패한 명령을 커밋으로 셌다")
    got = a.harvest({"id": "abc", "thread_name": "블로그 정리",
                     "updated_at": ts}, "")
    eq([(g[2], g[3], g[4]) for g in got], [("title", "블로그 정리", "abc")],
       "session_index 제목")
    got = a.harvest({"timestamp": ts, "type": "session_meta", "payload": {
        "thread_source": "guardian_review", "parent_thread_id": "p",
        "cwd": "/x"}}, "s")
    eq([g[2] for g in got], [agents.SKIP_FILE], "승인 검토 스레드를 못 걸렀다")


@check("Codex — 스캔: 승인 검토 파일은 건너뛰고, Claude 가 예산을 독차지하지 않는다")
def _():
    import datetime
    import uuid as _uuid
    from nervterm import agents, db, settings, work
    base = Path(_TMP) / "scan-fair"
    shutil_rm = __import__("shutil").rmtree
    shutil_rm(base, ignore_errors=True)
    codex_home = base / "codex"
    day_dir = codex_home / "sessions" / "2026" / "10" / "01"
    day_dir.mkdir(parents=True)
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    user_id, guard_id = str(_uuid.uuid4()), str(_uuid.uuid4())

    def write(sid, meta, items):
        lines = [{"timestamp": now_utc, "type": "session_meta",
                  "payload": meta}]
        lines += [{"timestamp": now_utc, "type": "event_msg",
                   "payload": {"type": "item_completed", "item": it}}
                  for it in items]
        (day_dir / f"rollout-2026-10-01T10-00-00-{sid}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines)
            + "\n", encoding="utf-8")
    write(user_id, {"thread_source": "user", "cwd": "/x/codexproj"},
          [{"type": "UserMessage",
            "content": [{"type": "text", "text": "코덱스로 한 일"}]}])
    write(guard_id, {"thread_source": "guardian_review",
                     "parent_thread_id": user_id, "cwd": "/x"},
          [{"type": "UserMessage",
            "content": [{"type": "text", "text": "검토 스레드의 말"}]}])
    (codex_home / "session_index.jsonl").write_text(json.dumps(
        {"id": user_id, "thread_name": "코덱스 스레드 제목",
         "updated_at": now_utc}, ensure_ascii=False) + "\n", encoding="utf-8")

    # Claude 쪽에는 예산보다 큰 밀린 기록
    claude_dir = base / "claude" / "proj"
    claude_dir.mkdir(parents=True)
    filler = json.dumps({"type": "system", "text": "x" * 900}) + "\n"
    (claude_dir / "big.jsonl").write_text(filler * 6000, encoding="utf-8")

    claude = agents.get("claude")
    claude.sessions_dir = lambda: base / "claude"
    old_codex = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(codex_home)
    settings.put("agents.codex", True)
    try:
        with db.session() as con:
            db.init(con)
            con.execute("DELETE FROM work_facts WHERE player=?", (db.PLAYER,))
            work.scan(con, budget=2_000_000)
            texts = {r["text"] for r in con.execute(
                "SELECT text FROM work_facts WHERE player=? AND agent='codex'",
                (db.PLAYER,))}
            true("코덱스로 한 일" in texts,
                 "Claude 가 예산을 다 써서 Codex 를 못 읽었다")
            true("검토 스레드의 말" not in texts, "승인 검토 스레드를 읽었다")
            skipped = con.execute(
                "SELECT skip FROM work_scan WHERE path LIKE ?",
                (f"%{guard_id}%",)).fetchone()
            eq(skipped["skip"] if skipped else None, 1, "건너뛰기 표시")
            titles = work._human_titles(con, db.today(), 10)
            true("코덱스 스레드 제목" in titles, f"제목이 안 이어졌다: {titles}")
    finally:
        del claude.sessions_dir           # 클래스의 것(시험용 빈 폴더)으로
        os.environ["CODEX_HOME"] = old_codex
        settings.put("agents.codex", False)
        shutil_rm(base, ignore_errors=True)


@check("훅 — Claude Code 의 실패는 PostToolUseFailure 로 온다 (등록·처리)")
def _():
    import importlib.util
    from nervterm import agents, db
    spec = importlib.util.spec_from_file_location(
        "_installhooks2", ROOT / "install-hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    wanted = mod.wanted_for(agents.get("claude"))
    true("PostToolUseFailure" in wanted, "실패 이벤트를 등록하지 않는다")
    true(wanted["PostToolUseFailure"], "실패 이벤트에 도구 matcher 가 없다")
    true("PermissionRequest" in mod.wanted_for(agents.get("codex")),
         "Codex 승인 대기 이벤트")
    with db.session() as con:
        db.init(con)
        fails0 = db.daily_row(con)["fails"]
        db.put(con, "fail_streak", 0)
    _hook({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
           "tool_input": {"command": "false"}, "session_id": "f"})
    with db.session() as con:
        eq(db.daily_row(con)["fails"], fails0 + 1, "실패를 못 셌다")
        eq(db.geti(con, "fail_streak"), 1, "연속 실패")


# ═══════════════════════════════════════════════════════════════════════
#  보상·근무 기록 대상 — 체크한 에이전트만
# ═══════════════════════════════════════════════════════════════════════
def _hook_as(agent_arg, payload):
    import subprocess
    cmd = [sys.executable, "-m", "nervterm", "hook"]
    if agent_arg:
        cmd.append(agent_arg)
    subprocess.run(cmd, input=json.dumps(payload), text=True, cwd=str(ROOT),
                   timeout=30, env={**os.environ})


@check("보상 대상 — 체크를 푼 에이전트의 작업은 적립하지 않는다")
def _():
    from nervterm import db, settings
    edit = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {}, "tool_response": {}, "session_id": "g"}
    settings.put("agents.codex", False)
    settings.put("agents.claude", True)
    try:
        with db.session() as con:
            db.init(con)
            # 앞 시험들이 오늘 적립 상한을 채웠을 수 있다
            con.execute("UPDATE daily SET lcl=0 WHERE player=? AND day=?",
                        (db.PLAYER, db.today()))
            before = db.geti(con, "lcl")
        _hook_as("codex", edit)
        _hook_as(None, {**edit, "transcript_path":
                        "/Users/x/.codex/sessions/2026/10/01/rollout-a.jsonl"})
        with db.session() as con:
            eq(db.geti(con, "lcl"), before, "체크를 푼 Codex 에서 적립됐다")
        _hook_as("claude", edit)
        with db.session() as con:
            true(db.geti(con, "lcl") > before, "체크한 Claude 가 적립 안 됐다")
            mid = db.geti(con, "lcl")
        settings.put("agents.codex", True)
        _hook_as("codex", edit)
        with db.session() as con:
            true(db.geti(con, "lcl") > mid, "다시 체크했는데 적립 안 됐다")
    finally:
        settings.put("agents.codex", False)


@check("보상 대상 — 체크를 푼 에이전트의 기록은 캐릭터가 모른다")
def _():
    from nervterm import db, settings, work
    with db.session() as con:
        db.init(con)
        day = db.today()
        con.execute("DELETE FROM work_facts WHERE player=? AND day=?",
                    (db.PLAYER, day))
        for agent, text in (("claude", "클로드에서 한 일"),
                            ("codex", "코덱스에서 한 일")):
            con.execute("INSERT INTO work_facts(player,day,ts,kind,text,sid,"
                        "agent) VALUES(?,?,?,?,?,?,?)",
                        (db.PLAYER, day, db.now(), "prompt", text, "s", agent))
        settings.put("agents.claude", True)
        settings.put("agents.codex", False)
        got = work.digest(con)
        true("클로드에서 한 일" in got, "체크한 쪽 기록이 없다")
        true("코덱스에서 한 일" not in got, "체크를 푼 쪽 기록이 실렸다")
        settings.put("agents.codex", True)
        true("코덱스에서 한 일" in work.digest(con), "다시 체크하면 돌아와야 한다")
        settings.put("agents.codex", False)


@check("훅 설치 — 명령 끝에 에이전트 이름을 붙인다")
def _():
    import importlib.util
    from nervterm import agents
    spec = importlib.util.spec_from_file_location(
        "_installhooks3", ROOT / "install-hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for aid in ("claude", "codex"):
        cmd = mod.command_for(agents.get(aid))
        true(cmd.endswith(f" hook {aid}"), f"{aid}: {cmd}")
        true(agents.is_our_hook({"hooks": [{"command": cmd}]}),
             "우리 훅으로 못 알아본다")


# ═══════════════════════════════════════════════════════════════════════
#  모델 목록 · 연결 시험
# ═══════════════════════════════════════════════════════════════════════
@check("모델 목록 — Codex 의 두 목록을 합치고, 숨긴 것은 뺀다")
def _():
    from nervterm import llm
    cli = {"models": [
        {"slug": "gpt-a", "display_name": "A", "visibility": "list",
         "priority": 5, "description": "옛것"},
        {"slug": "review", "visibility": "hide", "priority": 1}]}
    cache = {"models": [
        {"slug": "gpt-new", "display_name": "New", "visibility": "list",
         "priority": 1, "description": "최신"},
        {"slug": "gpt-a", "display_name": "A", "visibility": "list",
         "priority": 7}]}
    got = llm.CodexCLI.parse_catalog(cli, cache, None)
    eq([m[0] for m in got], ["gpt-new", "gpt-a"], "순서·중복·숨김")
    eq(got[1][2], "옛것", "우선순위가 높은 쪽 설명")
    names = [m[0] for m in llm.ClaudeCLI({}).catalog()]
    for alias in ("sonnet", "opus", "haiku"):
        true(alias in names, f"Claude 별칭 {alias} 가 없다")


@check("모델 바꾸기 — 연결 시험이 실패하면 아무것도 안 바뀐다")
def _():
    from nervterm import db, llm, settings

    class Picky(llm.Provider):
        id = "picky"
        label = "까다로운 것"
        billing = llm.BILLING_NONE
        default_model = "ok-model"

        def complete(self, system, user, *, timeout=None, schema=None):
            self.calls = 1
            if self.model == "ok-model" or self.model == "good":
                return '{"line": "들린다."}'
            self.last_error = f"그런 모델 없음: {self.model}"
            return None
    llm.BY_ID["picky"] = Picky
    before_provider = settings.get("llm.provider")
    try:
        with db.session() as con:
            db.init(con)
            ok, why = llm.switch(con, provider_id="picky", model="bad")
            eq(ok, False, "안 되는 모델로 바뀌었다")
            true("bad" in why, f"사유: {why}")
            eq(settings.get("llm.provider"), before_provider,
               "실패했는데 프로바이더가 바뀌었다")
            eq((settings.get("llm.models", {}) or {}).get("picky"), None,
               "실패했는데 모델이 저장됐다")
            ok, line = llm.switch(con, provider_id="picky", model="good")
            eq((ok, line), (True, "들린다."), "되는 모델")
            eq(settings.get("llm.provider"), "picky", "프로바이더 저장")
            eq(settings.get("llm.models", {})["picky"], "good", "모델 저장")
    finally:
        settings.put("llm.provider", before_provider)
        llm.BY_ID.pop("picky", None)


@check("연결 시험 — 안 되는 이유를 알려 준다")
def _():
    from nervterm import db, llm

    class Broken(llm.Provider):
        id = "broken"
        label = "고장"
        billing = llm.BILLING_NONE

        def complete(self, system, user, *, timeout=None, schema=None):
            return "이건 JSON 이 아니다"
    with db.session() as con:
        db.init(con)
        ok, why = llm.check(con, Broken({}))
        eq(ok, False, "JSON 이 아닌데 통과")
        true("JSON" in why, f"사유: {why}")


# ═══════════════════════════════════════════════════════════════════════
#  누구인가 — 데몬 아래에서도 실제 계정
# ═══════════════════════════════════════════════════════════════════════
@check("식별 — getlogin 이 'root' 를 줘도 실제 실행 계정으로 본다")
def _():
    import pwd
    from nervterm import identity, widget
    me = pwd.getpwuid(os.getuid()).pw_name
    saved = os.environ.pop("REI_PLAYER")
    orig = os.getlogin
    os.getlogin = lambda: "root"            # Codex 데스크톱 훅이 겪는 상황
    try:
        eq(identity.player(), me, "데몬 아래 훅이 'root' 로 적립된다")
        eq(widget._player(), me, "위젯도 같은 규칙이어야 한다")
    finally:
        os.getlogin = orig
        os.environ["REI_PLAYER"] = saved


@check("v7 승격 — 유령 'root' 의 적립을 실제 사용자에게 합친다 (만난 적 있으면 안 건드림)")
def _():
    import pwd
    from nervterm import db
    me = pwd.getpwuid(os.getuid()).pw_name
    saved = db.PLAYER
    db.PLAYER = me
    try:
        with db.session() as con:
            db.init(con)
            for p in (me, "root"):
                for table in ("state", "ledger", "daily"):
                    con.execute(f"DELETE FROM {table} WHERE player=?", (p,))
            for p, lcl in ((me, 100), ("root", 900)):
                for key in ("lcl", "total_earned"):
                    con.execute("INSERT INTO state(player,char,key,value) "
                                "VALUES(?,'',?,?)", (p, key, str(lcl)))
                con.execute("INSERT INTO daily(player,day,tools,lcl) "
                            "VALUES(?,?,?,?)", (p, "2026-09-01", 10, lcl))
                con.execute("INSERT INTO ledger(player,char,ts,kind,delta_lcl)"
                            " VALUES(?,'',?,?,?)", (p, db.now(), "tool", lcl))
            con.execute("INSERT INTO state(player,char,key,value) "
                        "VALUES('root','rei','met_count','0')")
            db._merge_phantoms(con)
            eq(db.geti(con, "lcl"), 1000, "지갑을 합치지 않았다")
            eq(con.execute("SELECT tools, lcl FROM daily WHERE player=? AND "
                           "day='2026-09-01'", (me,)).fetchone()[:], (20, 1000),
               "하루 집계")
            eq(con.execute("SELECT COUNT(*) FROM state WHERE player='root'"
                           ).fetchone()[0], 0, "유령이 남았다")
            # 실제로 root 로 논 사람은 건드리지 않는다
            con.execute("INSERT INTO state(player,char,key,value) "
                        "VALUES('root','','lcl','5')")
            con.execute("INSERT INTO state(player,char,key,value) "
                        "VALUES('root','rei','met_count','3')")
            db._merge_phantoms(con)
            eq(db.geti(con, "lcl"), 1000, "실제 root 플레이어를 합쳐 버렸다")
            for table in ("state", "ledger", "daily"):
                con.execute(f"DELETE FROM {table} WHERE player IN (?,?)",
                            (me, "root"))
    finally:
        db.PLAYER = saved


# ═══════════════════════════════════════════════════════════════════════
#  로컬 에이전트 — git 커밋 · Ollama 대화
# ═══════════════════════════════════════════════════════════════════════
def _git(repo, *args):
    import subprocess
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True)


def _commit(repo, msg):
    (Path(repo) / f"f{abs(hash(msg)) % 100000}.txt").write_text(msg)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


@check("로컬 — reflog 를 git 없이 읽는다")
def _():
    from nervterm import local
    got = local.parse_reflog_line(
        "0000 abcd1234 t <t@x> 1790000000 +0900\tcommit: 버그 고침")
    eq(got, ("abcd1234", 1790000000, "commit", "버그 고침"), "한 줄")
    true(local.is_commit("commit (amend)"), "amend")
    true(not local.is_commit("checkout"), "checkout 은 커밋이 아니다")


@check("로컬 — 켠 뒤의 커밋만, 훅이 적은 커밋은 빼고 적립한다")
def _():
    import shutil
    from nervterm import db, local, settings, work
    if shutil.which("git") is None:
        return
    base = Path(_TMP) / "local-work"
    shutil.rmtree(base, ignore_errors=True)
    repo = base / "proj" / "app"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _commit(repo, "켜기 전의 커밋")
    settings.put("local.roots", [str(base)])
    settings.put("agents.local", True)
    try:
        with db.session() as con:
            db.init(con)
            con.execute("UPDATE daily SET lcl=0 WHERE player=? AND day=?",
                        (db.PLAYER, db.today()))
            db.put(con, "local_since", "")
            db.put(con, "local_repos_at", "")
            lcl0 = db.geti(con, "lcl")
            eq(local.scan(con), 0, "처음 켤 때 지난 커밋을 적립했다")
            eq(db.geti(con, "lcl"), lcl0, "기준선")
            con.execute("UPDATE state SET value=? WHERE player=? AND char='' "
                        "AND key='local_since'",
                        ("2000-01-01T00:00:00", db.PLAYER))
            _commit(repo, "로컬 에이전트가 한 커밋")
            eq(local.scan(con), 1, "새 커밋을 못 봤다")
            eq(db.geti(con, "lcl"), lcl0 + local.LOCAL_COMMIT_LCL, "적립")
            true("로컬 에이전트가 한 커밋" in work.digest(con),
                 "근무 일지에 안 보인다")
            # 훅(Claude)이 이미 적은 커밋은 로컬로 또 세지 않는다
            _commit(repo, "클로드가 한 커밋")
            eq(local.note_commit(con, str(repo), "claude"), 1, "해시 기록")
            eq(local.scan(con), 0, "훅이 적은 커밋을 또 적립했다")
            # 체크를 풀면 읽지도 적립하지도 않는다
            settings.put("agents.local", False)
            _commit(repo, "끈 뒤의 커밋")
            eq(local.scan(con), 0, "꺼도 적립됐다")
    finally:
        settings.put("agents.local", False)
        shutil.rmtree(base, ignore_errors=True)


@check("로컬 — 체크를 푼 에이전트의 커밋도 해시는 남긴다 (로컬로 새지 않게)")
def _():
    import shutil
    from nervterm import db, settings
    if shutil.which("git") is None:
        return
    repo = Path(_TMP) / "off-agent-repo"
    shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "코덱스가 한 커밋")
    settings.put("agents.codex", False)
    _hook_as("codex", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_input": {"command": 'git commit -m "x"'},
                       "tool_response": "", "cwd": str(repo),
                       "session_id": "c"})
    import subprocess
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    with db.session() as con:
        got = con.execute("SELECT source FROM commits WHERE player=? "
                          "AND hash=?", (db.PLAYER, head)).fetchone()
        eq(got["source"] if got else None, "codex", "해시를 안 남겼다")
    shutil.rmtree(repo, ignore_errors=True)


@check("로컬 — Ollama 대화는 켠 뒤에 친 말부터 읽는다")
def _():
    from nervterm import agents, db, settings, work
    home = Path(_TMP) / "ollama-home"
    home.mkdir(exist_ok=True)
    hist = home / "history"
    hist.write_text("예전에 물어본 것\n", encoding="utf-8")
    os.environ["OLLAMA_HOME"] = str(home)
    settings.put("agents.local", True)
    try:
        with db.session() as con:
            db.init(con)
            work.scan(con)
            with hist.open("a", encoding="utf-8") as f:
                f.write("이 함수 리팩터링 해 줘\n/bye\n")
            work.scan(con)
            texts = {r["text"] for r in con.execute(
                "SELECT text FROM work_facts WHERE player=? AND agent='local'",
                (db.PLAYER,))}
            true("이 함수 리팩터링 해 줘" in texts, "새로 친 말을 못 읽었다")
            true("예전에 물어본 것" not in texts, "켜기 전의 말을 오늘 것으로 읽었다")
            true("/bye" not in texts, "REPL 명령을 대화로 읽었다")
    finally:
        settings.put("agents.local", False)
        os.environ.pop("OLLAMA_HOME", None)


# ═══════════════════════════════════════════════════════════════════════
def main() -> int:
    import shutil
    print(f"임시 저장소: {_TMP}\n")
    for name in PASS:
        print(f"  \033[32m✓\033[0m {name}")
    for name, why, tb in FAIL:
        print(f"  \033[31m✗\033[0m {name}")
        print(f"      {why}")
    print()
    print(f"{len(PASS)} 통과, {len(FAIL)} 실패")
    if FAIL and os.environ.get("NERV_DEBUG"):
        for name, _, tb in FAIL:
            print(f"\n=== {name} ===\n{tb}")
    shutil.rmtree(_TMP, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
