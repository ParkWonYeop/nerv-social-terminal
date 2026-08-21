# -*- coding: utf-8 -*-
"""설정 화면.

여기도 그리지 않는다. MenuView 를 만들어 UI 플러그인에 넘기고,
고른 key 를 받아 처리한다. 그래서 설정 화면도 플러그인이 자기
모양으로 그릴 수 있다.

돌려주는 값:
    ""          그냥 시작 화면으로 돌아간다
    "restart"   UI 플러그인이 바뀌었다 — 프로세스를 다시 띄워야 한다
    "quit"      나간다
"""
from . import (characters, db, llm, plugins, settings, term, ui, world)
from .llm import guard
from .ui import view as V

RESTART = "restart"


# ═══════════════════════════════════════════════════════════════════════
#  최상위
# ═══════════════════════════════════════════════════════════════════════
def open_settings(con) -> str:
    while True:
        w = world.active()
        prov = llm.current()
        ok, why = llm.probe(prov)

        items = [
            V.MenuItem("1", "대화", value=f"하루 {llm_cap_label()}",
                       note="대사 생성 횟수, 연출"),
            V.MenuItem("2", "LLM 연결", value=prov.label,
                       tone="danger" if prov.is_billable() else "plain",
                       note=(why if not ok else
                             "캐릭터가 어느 모델로 말하는가")),
            V.MenuItem("3", "캐릭터", value=f"{len(characters.ENABLED)}"
                                          f"/{len(characters.IDS)} 켜짐",
                       note="누구를 만날 수 있게 할지"),
            V.MenuItem("4", "화면 (UI 플러그인)", value=ui.current_id(),
                       note="바꾸면 다시 시작한다"),
            V.MenuItem("5", "세계관", value=w.name,
                       note=f"재화: {w.currency_name}"),
            V.MenuItem("6", "재화를 적립할 에이전트",
                       value=", ".join(settings.enabled_agents()) or "없음",
                       note="훅이 설치된 에이전트에서 작업량을 가져온다"),
            V.MenuItem("9", "초기화", tone="danger",
                       note="관계·기억·재화를 지운다"),
        ]
        notes = []
        if plugin_problems():
            notes.append(("danger", "플러그인 문제: " +
                          " / ".join(plugin_problems())))

        got = ui.menu(V.MenuView(
            title="설정", subtitle=f"상대 — {db.PLAYER}",
            items=items, notes=notes,
            hint="번호를 고른다.  b 돌아가기  ·  q 나감"))

        if got in (None, "b"):
            return ""
        if got == "quit":
            return "quit"
        if got == "1":
            _talk_settings(con)
        elif got == "2":
            _llm_settings(con)
        elif got == "3":
            _character_settings(con)
        elif got == "4":
            if _ui_settings(con):
                return RESTART
        elif got == "5":
            _world_settings(con)
        elif got == "6":
            _agent_settings(con)
        elif got == "9":
            _reset_settings(con)


def llm_cap_label() -> str:
    from . import config
    return f"{config.daily_llm_calls()}회"


def plugin_problems():
    out = []
    for (kind, pid), plug in sorted(plugins.discover().items()):
        if not plug.ok:
            out.append(f"{kind}:{pid} — {plug.error}")
    for pack, why in characters.LOAD_ERRORS:
        out.append(f"{pack} — {why}")
    if ui.LOAD_ERROR:
        out.append(ui.LOAD_ERROR)
    if world.LOAD_ERROR:
        out.append(world.LOAD_ERROR)
    return out


# ═══════════════════════════════════════════════════════════════════════
#  1. 대화
# ═══════════════════════════════════════════════════════════════════════
def _talk_settings(con) -> None:
    from . import config
    while True:
        anim = settings.get("animation", True)
        speed = settings.get("typing_speed", 0.028)
        env = settings.overridden_by_env("daily_llm_calls")

        items = [
            V.MenuItem("1", "하루 대사 생성 상한",
                       value=f"{config.daily_llm_calls()}회",
                       note=("환경변수 " + env + " 가 덮고 있다" if env else
                             "넘으면 사전 작성 대사로 자동 전환된다")),
            V.MenuItem("2", "타이핑·부팅 연출",
                       value="켬" if anim else "끔",
                       note="SSH 가 느리면 끄는 게 낫다"),
            V.MenuItem("3", "타이핑 속도",
                       value=f"{speed:.3f}초/글자" if speed else "즉시",
                       note="0 이면 한 번에 출력"),
        ]
        got = ui.menu(V.MenuView(title="설정 — 대화", items=items))
        if got in (None, "b"):
            return
        if got == "quit":
            return
        if got == "1":
            raw = term.ask_line("  하루 상한 (0~5000) > ")
            if raw and raw.isdigit():
                settings.put("daily_llm_calls", max(0, min(5000, int(raw))))
        elif got == "2":
            settings.put("animation", not anim)
        elif got == "3":
            raw = term.ask_line("  초/글자 (0 ~ 0.2, 예: 0.028) > ")
            try:
                settings.put("typing_speed",
                             max(0.0, min(0.2, float(raw or 0))))
            except (TypeError, ValueError):
                pass


# ═══════════════════════════════════════════════════════════════════════
#  2. LLM 연결
# ═══════════════════════════════════════════════════════════════════════
def _llm_settings(con) -> None:
    while True:
        cfg = settings.get("llm", {}) or {}
        prov = llm.current()
        ok, why = llm.probe(prov)

        items = [V.MenuItem("1", "프로바이더", value=prov.label,
                            note=llm.BILLING_KO.get(prov.billing, ""))]
        if prov.wants_model:
            items.append(V.MenuItem(
                "2", "모델", value=prov.model or "(기본값)"))
        if prov.wants_base_url:
            items.append(V.MenuItem(
                "3", "서버 주소", value=prov.base_url or "(기본값)"))
        if prov.wants_api_key:
            items.append(V.MenuItem(
                "4", "키를 읽을 환경변수", value=prov.key_env or "(없음)",
                note="키 자체는 저장하지 않는다"))
        items.append(V.MenuItem(
            "5", "과금 안전장치",
            value="켬 (유료 API 차단)" if guard.guard_on() else "끔",
            tone="plain" if guard.guard_on() else "danger",
            note=("끄기 전에는 토큰당 과금되는 프로바이더를 고를 수 없다"
                  if guard.guard_on() else
                  f"유료 프로바이더를 고를 수 있다. 하루 상한 "
                  f"{guard.daily_cap()}회")))
        if not guard.guard_on():
            items.append(V.MenuItem(
                "6", "유료 호출 하루 상한",
                value=f"{guard.daily_cap()}회" if guard.daily_cap()
                      else "상한 없음",
                tone="danger" if not guard.daily_cap() else "plain",
                note=f"오늘 {guard.used_today(con)}회 썼다"))
        items.append(V.MenuItem("t", "지금 연결 시험",
                                note="한 턴 실제로 불러 본다"))

        notes = [("good" if ok else "danger",
                  ("연결 가능" if ok else f"지금 쓸 수 없다 — {why}"))]
        if prov.is_billable():
            notes.append(("danger", "이 설정은 토큰당 요금이 청구된다."))

        got = ui.menu(V.MenuView(
            title="설정 — LLM 연결", items=items, notes=notes,
            subtitle="캐릭터가 어느 모델로 말하는가. 재화 적립과는 무관하다."))

        if got in (None, "b", "quit"):
            return
        if got == "1":
            _pick_provider(con)
        elif got == "2":
            raw = term.ask_line(f"  모델 (엔터면 기본값 "
                                f"{prov.default_model or '자동'}) > ")
            settings.put("llm.model", (raw or "").strip())
        elif got == "3":
            raw = term.ask_line(f"  주소 (엔터면 {prov.default_base_url}) > ")
            settings.put("llm.base_url", (raw or "").strip())
        elif got == "4":
            raw = term.ask_line(f"  환경변수 이름 "
                                f"(엔터면 {prov.default_key_env}) > ")
            settings.put("llm.api_key_env", (raw or "").strip())
        elif got == "5":
            _toggle_guard()
        elif got == "6":
            raw = term.ask_line("  유료 호출 하루 상한 (0 이면 무제한) > ")
            if raw and raw.isdigit():
                settings.put("llm.api_daily_call_cap", int(raw))
        elif got == "t":
            _test_connection(con)


def _pick_provider(con) -> None:
    items = []
    for i, klass in enumerate(llm.CATALOG, 1):
        probe = klass(settings.get("llm", {}) or {})
        blocked = guard.blocked_reason(probe)
        ok, why = probe.available()
        tone = "danger" if probe.is_billable() else "plain"
        note = probe.note
        if not blocked and not ok:
            note = f"{note}  ({why})"
        items.append(V.MenuItem(
            str(i), klass.label,
            value=llm.BILLING_KO.get(probe.billing, ""),
            tone=tone, note=note,
            disabled=bool(blocked),
            disabled_reason=blocked,
            payload=klass))

    got = ui.menu(V.MenuView(
        title="프로바이더 고르기", items=items,
        notes=[("plain", "구독 좌석과 로컬 모델은 추가 요금이 없다."),
               ("plain", "API 키를 쓰는 것만 토큰당 청구된다 — "
                         "가드가 켜져 있으면 고를 수 없다.")]))
    if got in (None, "b", "quit"):
        return
    chosen = next((it.payload for it in items if it.key == got), None)
    if chosen is None:
        return

    probe = chosen(settings.get("llm", {}) or {})
    if probe.is_billable():
        # 가드는 이미 꺼져 있다(아니면 고를 수 없었다). 그래도 한 번 더 보여준다.
        for tone, text in guard.warning_lines(probe):
            ui.line(text, tone)
        if not ui.confirm(f"{probe.label} 로 바꾼다. 요금이 청구될 수 있다.",
                          "동의"):
            ui.notice("바꾸지 않았다.", "info")
            ui.pause()
            return

    settings.put("llm.provider", chosen.id)
    # 프로바이더가 바뀌면 모델·주소는 그 프로바이더의 기본값으로 되돌린다.
    # 남아 있으면 엉뚱한 모델 이름이 다른 서버로 날아간다.
    settings.put("llm.model", "")
    settings.put("llm.base_url", "")
    ui.notice(f"{chosen.label} 로 바꿨다.", "good")
    ui.pause()


def _toggle_guard() -> None:
    if not guard.guard_on():
        guard.set_guard(True)
        ui.notice("과금 안전장치를 켰다. 유료 프로바이더는 다시 잠긴다.",
                  "good")
        ui.pause()
        return

    ui.blank()
    ui.notice("과금 안전장치를 끄려고 한다.", "danger")
    ui.blank()
    ui.line("끄면 API 키를 쓰는 프로바이더를 고를 수 있게 된다.", "plain")
    ui.line("그 프로바이더는 대사 한 줄마다 토큰 요금이 청구된다.", "plain")
    ui.line("구독 좌석(claude / codex 로그인)은 이것과 무관하다 — "
            "가드를 꺼도 켜도 똑같이 쓸 수 있다.", "plain")
    ui.blank()
    ui.line(f"끈 뒤에도 하루 {guard.daily_cap()}회 상한은 남는다.", "info")

    if ui.confirm("정말 끄겠는가.", guard.DISABLE_PHRASE):
        guard.set_guard(False)
        ui.notice("안전장치를 껐다. 유료 프로바이더를 고를 수 있다.", "danger")
    else:
        ui.notice("그대로 뒀다.", "info")
    ui.pause()


def _test_connection(con) -> None:
    prov = llm.current()
    ok, why = llm.probe(prov)
    ui.blank()
    if not ok:
        ui.notice(f"부를 수 없다 — {why}", "danger")
        ui.pause()
        return
    if prov.is_billable():
        ui.notice("시험 호출도 요금이 청구된다.", "danger")
        if not ui.confirm("한 번 부른다.", "동의"):
            return
    ui.notice(f"{prov.label} 에 한 턴 물어본다…", "info")
    got = llm.ask(con, "너는 시험용 응답기다. 반드시 JSON 하나만 낸다.",
                  '{"line":"들린다","emotion":"neutral"} 형식으로 답하라.')
    ui.blank()
    if got:
        ui.notice("응답이 왔다.", "good")
        ui.dim(str(got)[:200])
    else:
        ui.notice("응답이 없다. 사전 작성 대사로 돌게 된다.", "danger")
    ui.pause()


# ═══════════════════════════════════════════════════════════════════════
#  3. 캐릭터
# ═══════════════════════════════════════════════════════════════════════
def _character_settings(con) -> None:
    while True:
        items, n = [], 0
        for pack_id, chars in characters.PACKS.items():
            for char in chars:
                n += 1
                on = settings.character_enabled(pack_id, char.id)
                aff = db.geti(con, "affection", char=char.id)
                items.append(V.MenuItem(
                    str(n), char.full,
                    value="켬" if on else "끔",
                    tone="plain" if on else "dim",
                    note=f"{pack_id}  ·  호감 {aff}",
                    payload=(pack_id, char.id, on)))
        if not items:
            items.append(V.MenuItem("-", "설치된 캐릭터가 없다",
                                    disabled=True,
                                    disabled_reason="캐릭터 플러그인을 설치하라"))

        got = ui.menu(V.MenuView(
            title="설정 — 캐릭터", items=items,
            subtitle="꺼도 관계는 남는다. 다시 켜면 그대로 이어진다.",
            notes=[("plain", "끈 캐릭터도 근무 기록·위험 명령은 계속 본다 — "
                             "같은 단말이니까.")]))
        if got in (None, "b", "quit"):
            return
        target = next((it.payload for it in items if it.key == got), None)
        if target is None:
            continue
        pack_id, char_id, on = target
        if on and len([c for c in characters.ENABLED]) <= 1:
            ui.notice("마지막 한 사람은 끌 수 없다.", "danger")
            ui.pause()
            continue
        settings.set_character_enabled(pack_id, char_id, not on)
        characters.load(refresh=True)


# ═══════════════════════════════════════════════════════════════════════
#  4. UI 플러그인
# ═══════════════════════════════════════════════════════════════════════
def _ui_settings(con) -> bool:
    """UI 를 바꿨으면 True — 호출부가 프로세스를 다시 띄운다."""
    current = ui.current_id()
    items = []
    for i, plug in enumerate(ui.available(), 1):
        items.append(V.MenuItem(
            str(i), plug.name,
            value="사용 중" if plug.id == current else "",
            note=f"{plug.id} {plug.version}  ·  {plug.description}",
            disabled=not plug.ok, disabled_reason=plug.error,
            payload=plug.id))
    if not items:
        items.append(V.MenuItem("-", "설치된 UI 플러그인이 없다",
                                disabled=True,
                                disabled_reason="기본 화면으로 돈다"))

    got = ui.menu(V.MenuView(
        title="설정 — 화면", items=items,
        subtitle="한 번에 하나만 쓸 수 있다.",
        notes=[("plain", "바꾸면 게임을 다시 시작한다 — "
                         "부팅 연출부터 새 화면으로 뜬다.")]))
    if got in (None, "b", "quit"):
        return False
    chosen = next((it.payload for it in items if it.key == got), None)
    if chosen is None or chosen == current:
        return False
    settings.put("plugins.ui", chosen)
    ui.notice(f"{chosen} 로 바꿨다. 다시 시작한다…", "good")
    ui.pause()
    return True


# ═══════════════════════════════════════════════════════════════════════
#  5. 세계관
# ═══════════════════════════════════════════════════════════════════════
def _world_settings(con) -> None:
    current = world.active().id
    items = []
    for i, plug in enumerate(world.available(), 1):
        items.append(V.MenuItem(
            str(i), plug.name,
            value="사용 중" if plug.id == current else "",
            note=plug.description,
            disabled=not plug.ok, disabled_reason=plug.error,
            payload=plug.id))
    if not items:
        items.append(V.MenuItem("-", "설치된 세계관이 없다", disabled=True))

    got = ui.menu(V.MenuView(
        title="설정 — 세계관", items=items,
        subtitle="재화의 이름과 플레이어의 역할을 정한다.",
        notes=[("plain", "캐릭터 팩이 전제하는 세계관과 다른 걸 고르면 "
                         "대사가 어긋날 수 있다.")]))
    if got in (None, "b", "quit"):
        return
    chosen = next((it.payload for it in items if it.key == got), None)
    if chosen and chosen != current:
        world.use(chosen)
        ui.notice(f"세계관을 {chosen} 로 바꿨다.", "good")
        ui.pause()


# ═══════════════════════════════════════════════════════════════════════
#  6. 에이전트
# ═══════════════════════════════════════════════════════════════════════
def _agent_settings(con) -> None:
    from .agents import AGENTS

    while True:
        table = settings.get("agents", {}) or {}
        items = []
        for i, agent in enumerate(AGENTS, 1):
            on = bool(table.get(agent.id))
            installed = agent.hook_installed()
            items.append(V.MenuItem(
                str(i), agent.label,
                value="켬" if on else "끔",
                note=("훅 설치됨" if installed else
                      f"훅이 없다 — {agent.install_hint}"),
                tone="plain" if installed or not on else "warn",
                payload=agent.id))

        got = ui.menu(V.MenuView(
            title="설정 — 재화를 적립할 에이전트", items=items,
            subtitle="훅이 설치된 에이전트의 작업량이 재화가 된다.",
            notes=[("plain", "여기서 켜는 것은 '세션 기록을 읽을 대상' 이다."),
                   ("plain", "훅 설치는 install-hooks.py 가 한다.")]))
        if got in (None, "b", "quit"):
            return
        target = next((it.payload for it in items if it.key == got), None)
        if target:
            settings.put(f"agents.{target}", not bool(table.get(target)))


# ═══════════════════════════════════════════════════════════════════════
#  9. 초기화
# ═══════════════════════════════════════════════════════════════════════
def _reset_settings(con) -> None:
    c = db.counts(con)
    w = world.active()

    items = [
        V.MenuItem("1", "이 사람과의 관계만", tone="warn",
                   note="한 명을 고른다. 재화·근무 기록은 남는다"),
        V.MenuItem("2", "모든 관계", tone="warn",
                   note=f"호감·신뢰·기억 {c['memory']}개·대화 "
                        f"{c['dialogue']}줄. 재화는 남는다"),
        V.MenuItem("3", "전부 — 재화와 근무 기록까지", tone="danger",
                   note=f"{w.currency_name} {c['lcl']:,} · 근무 {c['days']}일 · "
                        f"기록 {c['facts']}건까지 전부"),
        V.MenuItem("4", "설정만 기본값으로", tone="warn",
                   note="플레이 데이터는 건드리지 않는다"),
    ]
    got = ui.menu(V.MenuView(
        title="설정 — 초기화", items=items,
        subtitle="되돌릴 수 없다.",
        notes=[("danger", "지워진 것은 복구할 방법이 없다."),
               ("plain", f"저장 위치: {db.config.db_path()}")]))

    if got in (None, "b", "quit"):
        return

    if got == "1":
        _reset_one_character(con)
    elif got == "2":
        if ui.confirm("모든 캐릭터와의 관계를 처음으로 되돌린다.", "초기화"):
            db.reset_relationships(con)
            ui.notice("관계를 되돌렸다.", "good")
        ui.pause()
    elif got == "3":
        ui.blank()
        ui.line(f"{w.currency_name} {c['lcl']:,} (총 획득 {c['earned']:,}) 이 "
                f"0 이 된다.", "danger")
        ui.line(f"근무 기록 {c['days']}일치와 추출된 사실 {c['facts']}건이 "
                f"사라진다.", "danger")
        ui.line("트랜스크립트 읽은 위치도 지워져서, 다음 실행 때 과거 "
                "기록을 다시 훑는다.", "plain")
        if ui.confirm("전부 지운다.", "전부 지운다"):
            db.reset_everything(con)
            ui.notice("전부 지웠다.", "good")
        ui.pause()
    elif got == "4":
        if ui.confirm("설정을 기본값으로 되돌린다.", "초기화"):
            settings.reset_to_defaults()
            ui.notice("설정을 되돌렸다. 다음 실행부터 적용된다.", "good")
        ui.pause()


def _reset_one_character(con) -> None:
    items = []
    for i, cid in enumerate(characters.IDS, 1):
        char = characters.get(cid)
        items.append(V.MenuItem(
            str(i), char.full,
            value=f"호감 {db.geti(con, 'affection', char=cid)}",
            payload=cid))
    got = ui.menu(V.MenuView(title="누구와의 관계를 지우는가", items=items))
    if got in (None, "b", "quit"):
        return
    cid = next((it.payload for it in items if it.key == got), None)
    if not cid:
        return
    char = characters.get(cid)
    if ui.confirm(f"{char.full} 와의 관계·기억·선물 기록을 전부 지운다.",
                  char.name):
        db.reset_character(con, cid)
        ui.notice(f"{char.full} 와는 처음 만나는 사이가 됐다.", "good")
    ui.pause()
