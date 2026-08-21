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
    local = llm.OpenAICompat({"base_url": "http://localhost:1234"})
    true(not local.is_billable(), "로컬인데 과금으로 봤다")
    remote = llm.OpenAICompat({"base_url": "https://someone.example.com"})
    true(remote.is_billable(), "바깥 주소인데 과금이 아니라고 봤다")


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


@check("에이전트 — 두 종류가 등록돼 있다")
def _():
    from nervterm import agents
    ids = {a.id for a in agents.AGENTS}
    eq(ids, {"claude", "codex"}, "에이전트 목록")
    for a in agents.AGENTS:
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
    from nervterm import characters, db, game, scenes, world
    world.load(refresh=True)
    with db.session() as con:
        db.init(con)
        char = characters.get("rei")
        db.set_char(char.id)
        g = game.Game(con, char, offline=True, animate=False)
        st = g.state()
        rows = scenes.gift_list(char.gifts, st.affection)
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
