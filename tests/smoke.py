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
    k = kinds(got)
    true("prompt" in k, "프롬프트를 못 뽑았다")
    true("title" in k, "제목 대체를 못 만들었다")

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
