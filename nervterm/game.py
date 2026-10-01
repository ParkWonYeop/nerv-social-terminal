# -*- coding: utf-8 -*-
"""게임 루프.

여기서는 '무엇을 보여줄지'만 정한다. '어떻게 보일지'는 UI 플러그인이
정한다 — 그래서 이 파일에는 색도, 좌표도, rich 도 없다.
화면에 뭔가를 내보낼 때는 언제나 뷰 모델(ui.view)을 만들어 넘긴다.

headless=True 면 화면을 그리지 않는다. `eva say` 가 쓴다 — 같은 규칙
(지루함 감점, 인내 회복, 방치·약속 정산)을 그대로 거치고 결과 줄만
buf 에 남긴다. 예전에는 say 가 규칙을 따로 복제해 절반만 적용했다.
"""
import datetime as _dt
import os
import random
import uuid

from . import (characters, clock, config, db, economy, events, llm, persona,
               recall, settings, social, stance, ui, world)
from .hangul import josa
from .ui import view as V


def catalog(items: dict, aff: int, *, locked: bool = False):
    """선물(gifts)·데이트(dates) 공용 목록.

    항목 튜플의 [1]=가격, [2]=최소 호감도. locked=False 는 열린 것을
    가격순으로, True 는 잠긴 것을 필요 호감도순으로 준다.
    """
    if locked:
        return [(k, v) for k, v in
                sorted(items.items(), key=lambda x: x[1][2]) if aff < v[2]]
    return [(k, v) for k, v in
            sorted(items.items(), key=lambda x: x[1][1]) if aff >= v[2]]


HINT = [
    V.Hint("/date", "데이트"), V.Hint("/gift", "선물"),
    V.Hint("/care", "돌봄"), V.Hint("/episode", "이야기"),
    V.Hint("/status", "기록"), V.Hint("/memory", "기억"),
    V.Hint("/work", "일지"), V.Hint("/help", "?"), V.Hint("/quit", "나감"),
]

# 같은 사람을 이 시간 안에 다시 부르면 새 방문이 아니라 이어지는 것으로
# 본다 — `eva say` 를 열 번 하면 열 번 찾아온 게 아니다.
VISIT_GAP_MINUTES = 60

# 이 단계부터는 남들도 안다(캐릭터 간 인지). 처음 닿았을 때 한 번.
PUBLIC_STAGE_FROM = 3

DEFAULT_CHOICES = ["옆에 조용히 앉는다", "무슨 생각을 하냐고 묻는다",
                   "말없이 하늘을 본다"]


class Game:
    LABEL = {"trust": "신뢰", "interest": "관심", "patience": "인내"}

    def __init__(self, con, char, *, offline=False, animate=True,
                 headless=False):
        self.con = con
        self.char = char
        self.offline = offline
        self.animate = animate and not headless
        self.headless = headless
        self.buf = []          # 화면에 보일 최근 로그 (V.LogEntry)
        self.framed = False    # 하단 고정 프레임이 화면에 그려져 있는가
        self.sess = uuid.uuid4().hex[:12]      # 이번 접속 식별자
        self.typing = settings.get("typing_speed", 0.028)
        # (안 찾아온 일수, 그동안 단말에서 일한 날 수) — settle() 이 채운다
        self.away = (0, 0)
        # 이번 접속이 시작될 때까지 인사에 쓴 근무 사건의 끝. 이번 접속
        # 동안에는 그 뒤의 사건을 계속 보여준다.
        self.event_floor = db.geti(con, "event_mark")
        # 이번 접속 전에 마지막으로 찾아온 때. 찾아오는 순간 last_seen 은
        # 지금이 되므로, '그 뒤에 남들과 있었던 일' 은 이걸로 잰다.
        self.prev_seen = db.get(con, "last_seen")
        from . import work
        self.work = work
        self.scan()

    def scan(self):
        """트랜스크립트에서 새로 쌓인 작업 기록을 읽어들인다(증분).

        파일 하나치씩 짧은 트랜잭션으로 넣는다(work.py). 읽고 파싱하는
        동안 락을 쥐지 않는다 — 그 사이 훅이 막히면 안 된다.
        """
        try:
            return self.work.scan(self.con)
        except Exception:                                     # noqa: BLE001
            return 0

    # ── 상태 ───────────────────────────────────────────────────────────
    def state(self) -> V.Status:
        con = self.con
        aff = db.geti(con, "affection")
        name, guide, idx = characters.stage_of(self.char, aff)
        row = db.daily_row(con)
        w = world.active()
        return V.Status(
            player=db.PLAYER,
            char_name=self.char.name,
            char_full=self.char.full,
            char_ja=self.char.display_ja,
            char_en=self.char.display_en,
            affection=aff,
            trust=db.geti(con, "trust"),
            interest=db.geti(con, "interest"),
            patience=db.geti(con, "patience"),
            stage=name, stage_idx=idx, stage_guide=guide,
            mood=db.get(con, "mood") or "flat",
            money=db.geti(con, "lcl"),
            currency_name=w.currency_name,
            currency_symbol=w.currency_symbol,
            tools=row["tools"] or 0,
            edits=row["edits"] or 0,
            commits=row["commits"] or 0,
            streak=db.geti(con, "streak_days"),
            llm_used=row["llm"] or 0,
            llm_cap=config.daily_llm_calls(),
            llm_warn_at=config.llm_warn_at(),
            provider_label=llm.provider_label(),
            offline=self.offline,
            billable=llm.is_billable(),
            terminal_name=w.terminal_name,
        )

    def last_seen(self):
        """(마지막 대화 시각, 직전 접속의 마지막 시각).

        '며칠 만에 왔다' 만으로는 부족하다. 5분 전에 말하다 끊은 것과
        어제 밤에 헤어진 것은 다르다.
        """
        row = self.con.execute(
            "SELECT ts FROM dialogue WHERE player=? AND char=? "
            "ORDER BY id DESC LIMIT 1", (db.PLAYER, self.char.id)).fetchone()
        prev = self.con.execute(
            "SELECT ts FROM dialogue WHERE player=? AND char=? AND sess<>? "
            "ORDER BY id DESC LIMIT 1",
            (db.PLAYER, self.char.id, self.sess)).fetchone()
        return (row["ts"] if row else ""), (prev["ts"] if prev else "")

    def send_work_text(self) -> bool:
        """근무 기록 원문을 프롬프트에 실어도 되는가.

        과금(=외부 API) 프로바이더에는 원문(프롬프트·커밋 메시지·저장소
        이름)을 보내지 않는다 — 집계(도구/커밋 횟수)만 싣는다. 원문
        전송은 privacy.send_work_text 로 옵트인. 구독 CLI·로컬 서버는
        제한하지 않는다 — 그 세션 기록 자체가 그 계정/기계에서 나온
        것이라 새로 새는 정보가 없다.
        """
        return (not llm.is_billable()
                or bool(settings.get("privacy.send_work_text", False)))

    # ── 돌봄 ───────────────────────────────────────────────────────────
    def care_active(self):
        """지금 효과가 살아 있는 돌봄. [(key, 이름, 남은 일수, 의미)]"""
        out, today = [], _dt.date.today()
        for key, (name, _price, _days, meaning) in (
                getattr(self.char, "care", None) or {}).items():
            try:
                until = _dt.date.fromisoformat(
                    db.get(self.con, f"care_{key}"))
            except ValueError:
                continue
            left = (until - today).days
            if left >= 0:
                out.append((key, name, left, meaning))
        return out

    def care_lines(self):
        return [f"{name} ({'오늘까지' if left == 0 else f'{left}일 더'})"
                for _k, name, left, _m in self.care_active()]

    def patience_boost(self) -> float:
        return config.CARE_PATIENCE_BOOST if self.care_active() else 1.0

    # ── 프롬프트 ───────────────────────────────────────────────────────
    def context(self, st, extra="", *, query="", boring=""):
        con = self.con
        mems = [t for t, _ in recall.relevant(con, query, n=8)]
        last_talk, last_sess = self.last_seen()
        send_text = self.send_work_text()
        days, away_work = self.away
        recent = (events.recent(con, after_id=self.event_floor)
                  if send_text else [])
        return persona.context_block(
            self.char,
            now_line=clock.now_line(),
            gap_line=clock.gap_line(last_talk, last_sess),
            odd_hour=clock.is_odd_hour() and st.tools > 0,
            stance_block=stance.block(con, stance.read(con), self.char,
                                      boring=boring),
            aff=st.affection, stage_name=st.stage,
            stage_guide=st.stage_guide, money=st.money,
            currency=st.currency_name,
            today_tools=st.tools, today_commits=st.commits,
            days_since=days, away_work=away_work,
            streak=st.streak, memories=mems,
            work_today=self.work.digest(con) if send_text else "",
            work_past=self.work.past_days(con, 3) if send_text else [],
            event_lines=events.lines(recent),
            social_block=social.block(con, self.char, since=self.prev_seen),
            care_lines=self.care_lines(),
            last_convo=recall.render(
                recall.last_conversation(con, self.sess, 6), self.char.name),
            this_convo=recall.render(
                recall.this_conversation(con, self.sess, 8),
                self.char.name),
            danger_note=extra or db.flag(con, "last_danger"),
        )

    # ── 출력 ───────────────────────────────────────────────────────────
    def push(self, role, text, emotion=""):
        self.buf.append(V.LogEntry(role, text, emotion))
        self.buf = self.buf[-40:]

    def redraw(self, *, animate=False):
        if self.headless:
            return
        ui.frame(self.state(), self.buf, HINT,
                 animate=animate, delay=self.typing)
        self.framed = True

    def page(self):
        """흐르는 출력(목록·기록 화면) 시작 — 프레임이 깨졌음을 표시."""
        self.framed = False

    def remember_for_widget(self):
        """Claude Code 상태줄 위젯이 읽을 값을 갱신한다.

        위젯은 속도 때문에 플러그인을 읽지 않는다. 그래서 표시에 필요한
        이름·색·단계표·한 마디 묶음·재화를 여기서 적어 둔다.
        """
        from . import widget
        try:
            aff = db.geti(self.con, "affection")
            stage, _, _ = characters.stage_of(self.char, aff)
            widget.remember(self.con, self.char, world.active(), stage)
        except Exception:                                     # noqa: BLE001
            pass          # 위젯 때문에 게임이 멈추면 안 된다

    def speak(self, got, *, kind="talk"):
        """캐릭터의 발화를 화면·DB에 반영하고 관계 변화를 적용."""
        con = self.con
        narration = got.get("narration", "")
        line, emotion = got.get("line", "…"), got.get("emotion", "neutral")
        inner, delta = got.get("inner", ""), got.get("affection_delta", 0)

        if narration:
            self.push("narr", narration)
        self.push("rei", line, emotion)
        if inner:
            self.push("inner", inner)

        before = characters.stage_of(self.char, db.geti(con, "affection"))[2]
        with db.tx(con):
            db.say(con, "rei", line, emotion, self.sess)
            if delta:
                economy.apply(con, aff=delta, kind=kind, reason=line[:60])
            moved = stance.apply_response(con, got, self.char)
            if got.get("memory"):
                recall.remember(con, "fact", got["memory"])
            db.bump(con, "turns", 1)

        # 이 대사로 무엇이 얼마나 변했는지 로그에 남긴다
        bits = []
        if delta:
            bits.append(("호감", delta))
        for f in ("trust", "interest", "patience"):
            if moved.get(f):
                bits.append((self.LABEL[f], moved[f]))
        if bits:
            self.push("delta", " · ".join(
                f"{n} {'+' if d > 0 else ''}{d}" for n, d in bits))
        if moved.get("promise_made"):
            self.push("sys", f"약속 — {moved['promise_made']}")
        if moved.get("promise_kept"):
            self.push("sys", f"약속을 지켰다 — {moved['promise_kept']}  "
                             f"(신뢰 +{config.TRUST_KEPT_PROMISE})")
        self.note_stage(before)

        self.remember_for_widget()
        self.redraw(animate=self.animate)

    def note_stage(self, before: int) -> None:
        """단계가 올랐으면 알린다. 높은 단계는 남들도 알게 된다."""
        aff = db.geti(self.con, "affection")
        name, _, idx = characters.stage_of(self.char, aff)
        if idx <= before:
            return
        self.push("sys", f"관계 — '{name}'")
        flag = f"stage_public_{idx}"
        if idx >= PUBLIC_STAGE_FROM and not db.flag(self.con, flag):
            db.flag(self.con, flag, "1")
            social.log(self.con, self.char.id, "stage", str(idx), name)

    # ── 캐릭터에게 묻기 ────────────────────────────────────────────────
    def ask(self, st, user_msg, *, extra_ctx="", clamp=3, query="",
            boring="", want_impression=False, rules=""):
        extra = rules
        if want_impression:
            extra += persona.impression_rules(self.char.name)
        sysp = persona.system_prompt(
            self.char, self.context(st, extra_ctx, query=query,
                                    boring=boring), extra)
        raw = llm.ask(self.con, sysp, user_msg, offline=self.offline)
        got = llm.normalize(raw, clamp=clamp) if raw else None
        if got:
            return got
        return persona.fallback_response(self.con, st, self.char)

    def _thinking(self):
        if self.offline:
            return _null()
        return ui.thinking(self.char.name)

    # ── 명령 ───────────────────────────────────────────────────────────
    def consolidate(self):
        """쌓인 대화를 기억으로 압축. 접속당 최대 1회, LLM 1호출."""
        if self.offline:
            return 0

        def ask_fn(system, user):
            # 캐릭터 응답 스키마가 아니라 facts 스키마로 부른다 —
            # 스키마를 강제하는 프로바이더에서도 facts 가 나오게.
            return llm.ask(self.con, system, user, offline=self.offline,
                           schema=llm.FACTS_SCHEMA)

        try:
            # 압축된 약속은 언제 한 것인지 정확히 모른다(압축한 때로 찍힌다).
            # '쉬겠다' 는 그 밤을 특정해야 판정되므로 말로 한 약속으로 둔다.
            def check(target):
                got = stance.clean_target(target, self.char)
                return "" if got == "rest" else got

            return recall.consolidate(
                self.con, ask_fn, name=self.char.name,
                targets=persona.targets_text(self.char), check_target=check)
        except Exception:                                     # noqa: BLE001
            if os.environ.get("NERV_DEBUG") or os.environ.get("REI_DEBUG"):
                import traceback
                traceback.print_exc()
            elif not self.headless:
                ui.dim("기억을 정리하지 못했다.")
            return 0

    def settle(self) -> None:
        """찾아왔을 때 — 지나간 시간이 관계에 남긴 것을 정산한다.

        방치, 관심 감소, 인내 회복, 약속(기한·'쉬겠다'·'또 올게').
        `eva say` 도 이걸 거친다.
        """
        con, nm = self.con, self.char.name
        last = db.get(con, "last_seen")
        try:
            gap_min = (_dt.datetime.now() - _dt.datetime.fromisoformat(
                last)).total_seconds() / 60
        except ValueError:
            gap_min = None
        fresh_visit = gap_min is None or gap_min >= VISIT_GAP_MINUTES

        with db.tx(con):
            days, penalty = economy.settle_neglect(con)
            away_work = economy.worked_while_away(con)
            self.away = (days, away_work)
            care = bool(self.care_active())
            recovered = stance.recover_patience(con,
                                                boost=self.patience_boost())
            chilled = 0 if care else stance.decay_interest(con, days)
            kept_rest, broken_rest = stance.check_rest(con)
            broken = stance.settle_promises(con) + len(broken_rest)
            kept_visit = stance.fulfil(con, "visit") if fresh_visit else []
            if fresh_visit:
                db.bump(con, "met_count", 1)
                social.log(con, self.char.id, "visit")
            # 게임 접속도 활동이다 — 없으면 매일 게임만 켜는 사람에게
            # "N일 만에 왔다" 는 거짓말이 반복된다. 정산 뒤여야 한다:
            # 앞이면 방치 일수가 0 으로 계산돼 감점 자체가 사라진다.
            economy.touch_activity(con)
            economy.touch_seen(con)

        if penalty:
            self.push("sys", f"{days}일 동안 오지 않았다.  호감 {penalty}")
        if away_work:
            self.push("sys", f"그동안 단말에는 {away_work}일 나왔다 — "
                             f"{josa(nm, '은/는')} 알고 있다.")
        if chilled:
            self.push("sys", f"관심 {chilled}")
        if broken:
            self.push("sys", f"지키지 않은 약속 {broken}건.  신뢰가 깎였다.")
        for text in kept_rest + kept_visit:
            self.push("sys", f"약속을 지켰다 — {text}  "
                             f"(신뢰 +{config.TRUST_KEPT_PROMISE})")
        if recovered:
            self.push("delta", f"인내 +{recovered}  (시간이 지났다)")

    def greet(self):
        self.settle()
        con, nm = self.con, self.char.name
        st = self.state()
        danger = db.flag(con, "last_danger")
        if danger:
            self.push("sys", f"{josa(nm, '은/는')} 그 일을 알고 있다: {danger}")

        days, away_work = self.away
        reul = josa(nm, "을/를")
        if days >= 2:
            msg = f"상대가 {days}일 만에 {reul} 찾아왔다. 첫 마디를 건네라."
            if away_work:
                msg += (f" 그동안 단말에서 일한 날이 {away_work}일이다 — "
                        f"여기 있었으면서 {reul} 찾아오지 않은 것이다. "
                        "성격대로 받아들여라.")
        else:
            msg = f"상대가 단말 앞에 앉아 {reul} 찾아왔다. 첫 마디를 건네라."
        if danger:
            msg += (f" {josa(nm, '은/는')} 상대가 '{danger}' 를 한 것을 "
                    "알고 있고, 그게 마음에 걸린다.")
        fresh = (events.recent(con, after_id=self.event_floor, limit=3)
                 if self.send_work_text() else [])
        if fresh:
            msg += ("\n위 [최근 단말 기록에서 눈에 띄는 일] 중 걸리는 것이 "
                    "있으면 하나만 짧게 건드려도 좋다. 다 언급하지는 마라.")
        else:
            msg += ("\n오늘 상대가 한 일이나 지난번 대화 중 하나를 짧게 "
                    "건드려도 좋다. 다 언급하지는 마라. 한 마디면 된다.")
        prev = db.get(con, "last_greeting")
        if prev:
            msg += (f"\n\n지난번에 만났을 때 {nm}의 첫 마디는 "
                    f"'{llm.inline_text(prev)}' 였다. "
                    "같은 말도, 같은 소재도 반복하지 마라. 다른 것을 골라라.")

        with self._thinking():
            got = self.ask(st, msg, clamp=0,
                           want_impression=stance.wants_impression(con))
        # 인사는 캐릭터가 먼저 거는 말이다. 상대는 아직 아무것도 안 했다 —
        # 관계 수치가 움직일 이유가 없다. 예전에는 +1 까지 허용해서 eva 를
        # 껐다 켜기만 해도 호감이 올랐다. (말투·기분·인상은 그대로 받는다.)
        for key in ("affection_delta", "trust_delta", "interest_delta",
                    "patience_delta"):
            got[key] = 0
        got["kept_promise"] = ""
        if not got["narration"] and not self.offline:
            got["narration"] = random.choice(self.char.greet_narr)
        self.speak(got, kind="greet")
        with db.tx(con):
            db.put(con, "last_greeting", got["line"])
            db.flag(con, "last_danger", "")
            if fresh:
                # 인사에서 꺼낸 사건은 다음 인사에서 또 꺼내지 않는다
                db.put(con, "event_mark", max(r[0] for r in fresh))

    def talk(self, text):
        if not text.strip():
            self.page()
            if not self.headless:
                ui.dim("무슨 말을 할까?")
            return
        con = self.con
        # 스캔을 먼저 — state() 의 도구/커밋 수와 work digest 가 같은
        # 시점을 보게. 반대면 한 프롬프트 안에서 "도구 0회"와 오늘 고친
        # 파일 목록이 함께 실린다.
        self.scan()
        recovered = stance.recover_patience(con, boost=self.patience_boost())
        if recovered:
            self.push("delta", f"인내 +{recovered}  (시간이 지났다)")
        st = self.state()
        boring = stance.check_boring(con, text)
        self.push("user", text)
        with db.tx(con):
            db.say(con, "user", text, "", self.sess)
            economy.touch_seen(con)
            if boring:
                ib, pb = db.geti(con, "interest"), db.geti(con, "patience")
                ia = stance.move(con, "interest", config.INTEREST_BORING)
                pa = stance.move(con, "patience", config.PATIENCE_BORING)
                db.log(con, "boring", 0, 0, boring)
        if boring:
            bits = [f"{n} {d}" for n, d in
                    (("관심", ia - ib), ("인내", pa - pb)) if d]
            if bits:
                self.push("delta", " · ".join(bits) + f"  ({boring})")
        self.redraw()
        msg = (f"[상대가 방금 한 말]\n{text}\n\n"
               f"{josa(self.char.name, '으로서/로서')} 응답하라.")
        want_imp = stance.wants_impression(con)
        with self._thinking():
            got = self.ask(st, msg, query=text, boring=boring,
                           want_impression=want_imp)
        self.speak(got)

    # ── 공용: 고르고, 거절당하고, 값을 치른다 ──────────────────────────
    def _refused(self, need: int, what: str, currency: str) -> bool:
        """거절당했으면 True. 거절하면 재화는 쓰이지 않는다."""
        why = stance.refuses(self.con, need=need, what=what)
        if not why:
            return False
        self.push("sys", f"{what} — 청했다. "
                         f"({why} — {josa(currency, '은/는')} 쓰이지 않았다.)")
        narr, line = stance.refusal_line(self.char, why)
        self.speak(persona.empty_response(
            narr or f"{josa(self.char.name, '은/는')} 고개를 저었다.", line,
            "distant"))
        return True

    def _pay(self, st, price: int, kind: str, what: str) -> bool:
        if economy.spend(self.con, price, kind, what):
            return True
        self.page()
        ui.notice(f"{josa(st.currency_name, '이/가')} 부족하다. "
                  f"({st.currency_symbol} {price} 필요 / "
                  f"보유 {st.currency_symbol} {st.money})", "danger")
        return False

    def _kept_note(self, kind: str, key: str) -> str:
        """이 행동이 약속을 지킨 것이면 기록하고, 프롬프트용 한 줄을 준다."""
        with db.tx(self.con):
            kept = stance.fulfil(self.con, kind, key)
        for text in kept:
            self.push("sys", f"약속을 지켰다 — {text}  "
                             f"(신뢰 +{config.TRUST_KEPT_PROMISE})")
        if not kept:
            return ""
        return ("\n[중요] 이건 전에 두 사람이 한 약속을 지킨 것이다: "
                + " / ".join(kept) + f". {josa(self.char.name, '은/는')} "
                "그걸 안다. 성격대로 반응하라.\n")

    # ── 선물 ───────────────────────────────────────────────────────────
    def gift_view(self, st) -> V.ShopView:
        rows = []
        for k, (name, price, need, _, _) in catalog(
                self.char.gifts, st.affection):
            owned = self.con.execute(
                "SELECT given FROM owned WHERE player=? AND char=? AND item=?",
                (db.PLAYER, self.char.id, k)).fetchone()
            rows.append(V.ShopRow(
                key=k, name=name, price=price, need=need,
                affordable=st.money >= price,
                given=(owned["given"] if owned else 0)))
        locked = [V.ShopRow(key=k, name=v[0], price=v[1], need=v[2],
                            locked=True)
                  for k, v in catalog(self.char.gifts, st.affection,
                                      locked=True)]
        return V.ShopView(title="상점 — 선물", rows=rows, locked=locked,
                          money=st.money,
                          currency_symbol=st.currency_symbol,
                          hint="↑↓ 고르고 Enter.  Esc 취소")

    def show_gifts(self):
        self.page()
        ui.shop(self.gift_view(self.state()))

    def gift(self, key):
        st = self.state()
        if not key:
            # 목록만 보여주고 끝내지 않는다 — 골라서 그대로 건넨다.
            self.page()
            picked = ui.choose_shop(self.gift_view(st))
            if picked is None:
                self.redraw()
                return
            key = picked.key
        item = self.char.gifts.get(key)
        if not item:
            self.page()
            ui.notice(f"'{key}' 라는 물건은 없다.", "danger")
            return
        name, price, need, base, meaning = item
        if st.affection < need:
            self.page()
            ui.notice(f"아직 이걸 건넬 사이는 아니다. (호감 {need} 필요)",
                      "danger")
            return
        if self._refused(need, f"{name}", st.currency_name):
            return
        if not self._pay(st, price, "gift", name):
            return

        con = self.con
        with db.tx(con):
            con.execute(
                "INSERT INTO owned(player,char,item,count,given) "
                "VALUES(?,?,?,0,1) "
                "ON CONFLICT(player,char,item) DO UPDATE SET given=given+1",
                (db.PLAYER, self.char.id, key))
            again = con.execute(
                "SELECT given FROM owned WHERE player=? AND char=? AND item=?",
                (db.PLAYER, self.char.id, key)).fetchone()["given"]
            social.log(con, self.char.id, "gift", key, name)

        self.push("sys", f"{josa(name, '을/를')} 건넸다.  "
                         f"({st.currency_symbol} -{price})")
        kept = self._kept_note("gift", key)
        self.redraw()

        nm = self.char.name
        msg = (f"상대가 {nm}에게 {josa(repr_q(name), '을/를')} 건넸다.\n"
               f"[이 물건이 {nm}에게 갖는 의미]\n{meaning}\n{kept}")
        if again > 1:
            msg += (f"\n주의: 이건 {again}번째로 같은 걸 받는 것이다. "
                    f"{josa(nm, '은/는')} 반복을 알아챈다. "
                    "감흥이 처음만 못하다.\n")
        msg += f"\n선물을 받은 {josa(nm, '으로서/로서')} 반응하라."

        clamp = max(1, base) if again == 1 else 1
        with self._thinking():
            got = self.ask(st, msg, clamp=clamp)
        if again > 1:
            got["affection_delta"] = min(got["affection_delta"], 1)
        recall.remember(con, "gift", f"{josa(name, '을/를')} 받았다.", 2)
        self.speak(got, kind="gift")

    # ── 장면 (데이트·에피소드 공용) ────────────────────────────────────
    SCENE_RULES = (
        "\n[이번 출력만 특별 규칙]\n"
        'JSON 의 "choices" 배열에 상대가 고를 수 있는 행동/대사 3개를 넣는다.\n'
        "선택지는 서로 성격이 달라야 한다: 하나는 무난하게, 하나는 상대의 "
        "마음에 깊이 다가가되 위험하게, 하나는 엉뚱하거나 거리를 두는 것.\n"
        "선택지는 상대(플레이어)의 1인칭 행동/대사로 쓴다. 각 20자 이내.\n"
    )

    def _scene(self, st, msg, *, clamp, choices: bool, setting=""):
        """장면 한 막. 실패하면 사전 대사로 채운다."""
        rules = self.SCENE_RULES if choices else ""
        sysp = persona.system_prompt(self.char, self.context(st), rules)
        with self._thinking():
            raw = llm.ask(self.con, sysp, msg, offline=self.offline)
        got = llm.normalize(raw, clamp=clamp) if raw else None
        if not got:
            narr, line, emo = persona.fallback(self.char, st.stage_idx)
            if setting and choices:
                narr = setting.split(".")[0] + "."
            got = persona.empty_response(
                narr, line, emo,
                affection_delta=0 if choices else 1,
                choices=list(DEFAULT_CHOICES) if choices else [])
        elif choices and not got["choices"]:
            # 장면은 살리고 선택지만 기본값으로 채운다
            got["choices"] = list(DEFAULT_CHOICES)
        return got

    def _take_action(self, choices) -> str:
        action = self.pick_action(choices)
        if not action:
            self.page()
            if not self.headless:
                ui.dim("…아무것도 하지 않았다.")
            return ""
        self.push("user", action)
        db.say(self.con, "user", action, "", self.sess)
        self.redraw()
        return action

    # ── 데이트 ─────────────────────────────────────────────────────────
    def date_view(self, st) -> V.ShopView:
        rows = [V.ShopRow(key=k, name=v[0], price=v[1], need=v[2],
                          affordable=st.money >= v[1])
                for k, v in catalog(self.char.dates, st.affection)]
        locked = [V.ShopRow(key=k, name=v[0], price=v[1], need=v[2],
                            locked=True)
                  for k, v in catalog(self.char.dates, st.affection,
                                      locked=True)]
        return V.ShopView(title="갈 수 있는 곳", rows=rows, locked=locked,
                          money=st.money,
                          currency_symbol=st.currency_symbol,
                          hint="↑↓ 고르고 Enter.  Esc 취소")

    def show_dates(self):
        self.page()
        ui.shop(self.date_view(self.state()))

    def date(self, key):
        st = self.state()
        if not key:
            self.page()
            picked = ui.choose_shop(self.date_view(st))
            if picked is None:
                self.redraw()
                return
            key = picked.key
        spot = self.char.dates.get(key)
        if not spot:
            self.page()
            ui.notice(f"'{key}' 라는 곳은 없다.", "danger")
            return
        name, price, need, setting = spot
        nm = self.char.name
        if st.affection < need:
            self.page()
            ui.notice(f"{josa(nm, '은/는')} 아직 따라나서지 않을 것이다. "
                      f"(호감 {need} 필요)", "danger")
            return
        if self._refused(need, name, st.currency_name):
            return
        if not self._pay(st, price, "date", name):
            return

        social.log(self.con, self.char.id, "date", key, name)
        self.push("sys", f"──  {name}  ──  데이트  "
                         f"({st.currency_symbol} -{price})")
        kept = self._kept_note("date", key)
        self.redraw()

        # 1막: 장면과 선택지
        msg = (f"[장소] {name}\n{setting}\n{kept}\n"
               f"{josa(nm, '과/와')} 단둘이 이 장소에 왔다. 도착한 순간의 "
               f"장면과 {nm}의 첫 마디를 쓰고, 상대가 고를 행동 3개를 "
               "제시하라.")
        got = self._scene(st, msg, clamp=2, choices=True, setting=setting)
        self.speak(got, kind="date")

        action = self._take_action(got["choices"])
        if not action:
            return

        # 2막: 판정
        # line 은 모델이, action 은 사용자가 쓴 텍스트 — 대괄호·개행을
        # 지워 [장소] 같은 구획 헤더를 위조하지 못하게 하고 넣는다.
        msg2 = (f"[장소] {name}\n{setting}\n\n"
                f"[방금 {josa(nm, '이/가')} 한 말] "
                f"{llm.inline_text(got['line'])}\n"
                f"[상대가 고른 행동] {llm.inline_text(action)}\n\n"
                f"이 행동에 대한 {nm}의 반응을 쓰라. 데이트의 마무리 장면이다.\n"
                f"행동이 진심이고 {josa(nm, '을/를')} 향한 것이면 크게 마음이 "
                f"움직인다(+4~+8). 무난하면 +1~+3. 성의 없거나 "
                f"{josa(nm, '을/를')} 도구 취급하면 음수(-5까지).")
        got2 = self._scene(st, msg2, clamp=8, choices=False)
        recall.remember(self.con, "date",
                        f"{name}에 함께 갔다. 상대는 '{action}' 했다.", 2)
        self.speak(got2, kind="date")

    def pick_action(self, choices) -> str:
        """장면 중의 행동 선택.

        마지막에 '직접 입력' 을 둔다. 거기까지 내려가서 Enter 를 누르면
        자유 입력으로 넘어가고, 쓴 말이 그대로 행동이 된다. 제시된
        선택지 밖으로 나갈 길이 없으면 대화가 아니라 설문이 된다.

        화면을 지우지 않는다(clear=False) — 방금 나온 장면과 첫 마디가
        위에 남아 있어야 무엇을 고르는지 알 수 있다.
        """
        items = [V.MenuItem(key=str(i), label=c, payload=c)
                 for i, c in enumerate(choices, 1)]
        items.append(V.MenuItem(
            key="_typed", label="직접 입력…", tone="warn", input_mode=True,
            note="하고 싶은 말이나 행동을 직접 쓴다"))

        got = ui.choose(V.MenuView(
            title="", items=items, clear=False, max_rows=8,
            hint="↑↓ 고르고 Enter.  Esc 그만두기"))
        if got is None:
            return ""
        return got.typed if got.input_mode else (got.payload or "")

    # ── 돌봄 ───────────────────────────────────────────────────────────
    def care_view(self, st) -> V.ShopView:
        active = {k: left for k, _n, left, _m in self.care_active()}
        rows = []
        for key, (name, price, days, _m) in sorted(
                (getattr(self.char, "care", None) or {}).items(),
                key=lambda kv: kv[1][1]):
            label = f"{name}  ·  {days}일"
            if key in active:
                left = active[key]
                label += f"  (지금 {'오늘까지' if left == 0 else f'{left}일 더'})"
            rows.append(V.ShopRow(key=key, name=label, price=price,
                                  affordable=st.money >= price))
        return V.ShopView(
            title=f"돌봄 — 며칠 동안 {josa(self.char.name, '을/를')} 챙긴다",
            rows=rows, money=st.money, currency_symbol=st.currency_symbol,
            hint="↑↓ 고르고 Enter.  Esc 취소")

    def care(self, key):
        """반복해서 쓰는 소모품. 효과: 그동안 인내가 빨리 돌고 관심이
        식지 않으며, 캐릭터는 챙김을 받고 있다는 걸 안다."""
        st = self.state()
        items = getattr(self.char, "care", None) or {}
        if not items:
            self.page()
            ui.notice(f"{josa(self.char.name, '을/를')} 위해 챙길 것이 아직 "
                      "없다.", "danger")
            return
        if not key:
            self.page()
            picked = ui.choose_shop(self.care_view(st))
            if picked is None:
                self.redraw()
                return
            key = picked.key
        item = items.get(key)
        if not item:
            self.page()
            ui.notice(f"'{key}' 라는 것은 없다.", "danger")
            return
        name, price, days, meaning = item
        if not self._pay(st, price, "care", name):
            return

        con, today = self.con, _dt.date.today()
        active = key in {k for k, *_ in self.care_active()}
        try:
            cur = _dt.date.fromisoformat(db.get(con, f"care_{key}"))
        except ValueError:
            cur = today - _dt.timedelta(days=1)
        start = max(cur + _dt.timedelta(days=1), today)
        until = start + _dt.timedelta(days=days - 1)
        db.put(con, f"care_{key}", until.isoformat())
        self.push("sys", f"{name} — {until.month}월 {until.day}일까지.  "
                         f"({st.currency_symbol} -{price})")
        self.redraw()

        nm = self.char.name
        msg = (f"상대가 {nm}에게 {josa(repr_q(name), '을/를')} 해 줬다. "
               "며칠 동안 이어진다.\n"
               f"[이게 {nm}에게 갖는 의미]\n{meaning}\n")
        if active:
            msg += ("\n이미 챙겨 주고 있던 것을 이어서 해 준 것이다. "
                    "처음처럼 반응하지 마라. 짧게.\n")
        msg += f"\n{josa(nm, '으로서/로서')} 짧게 반응하라."
        with self._thinking():
            got = self.ask(st, msg, clamp=0 if active else 1)
        self.speak(got, kind="care")

    # ── 에피소드 ───────────────────────────────────────────────────────
    def episode_items(self, st):
        """[(item, 열렸나, 이미 했나, 막힌 이유)] — 순서대로 열린다."""
        out, prev_done = [], True
        for item in getattr(self.char, "episodes", None) or ():
            key, title, price, need_aff, need_trust, _premise = item
            done = bool(db.flag(self.con, f"episode_done_{key}"))
            why = ""
            if not prev_done:
                why = "앞의 이야기를 먼저"
            elif st.affection < need_aff:
                why = f"호감 {need_aff} 필요"
            elif st.trust < need_trust:
                why = f"신뢰 {need_trust} 필요"
            out.append((item, not why, done, why))
            prev_done = done
        return out

    def episode(self, key):
        """관계가 깊어져야 열리는, 한 번뿐인 이야기. 세 막."""
        st = self.state()
        entries = self.episode_items(st)
        if not entries:
            self.page()
            ui.notice(f"{josa(self.char.name, '과/와')}의 이야기는 아직 "
                      "준비되지 않았다.", "danger")
            return
        if not key:
            self.page()
            menu = []
            for (k, title, price, *_), opened, done, why in entries:
                if done:
                    reason = "이미 함께 겪었다"
                elif not opened:
                    reason = why
                elif st.money < price:
                    reason = (f"{st.currency_symbol} {price} 필요 — 보유 "
                              f"{st.currency_symbol} {st.money}")
                else:
                    reason = ""
                menu.append(V.MenuItem(
                    key=k, label=title,
                    value="완료" if done else f"{st.currency_symbol} {price}",
                    disabled=bool(reason), disabled_reason=reason,
                    note=f"이름: {k}", payload=k))
            got = ui.choose(V.MenuView(
                title=f"{josa(self.char.name, '과/와')}의 이야기",
                subtitle="한 번뿐이다. 관계가 깊어져야 다음이 열린다.",
                items=menu, hint="↑↓ 고르고 Enter.  Esc 취소"))
            if got is None:
                self.redraw()
                return
            key = got.payload
        found = next((e for e in entries if e[0][0] == key), None)
        if found is None:
            self.page()
            ui.notice(f"'{key}' 라는 이야기는 없다.", "danger")
            return
        (key, title, price, need_aff, _need_trust, premise), opened, done, \
            why = found
        if done:
            self.page()
            ui.notice(f"{josa(repr_q(title), '은/는')} 이미 함께 겪었다.",
                      "danger")
            return
        if not opened:
            self.page()
            ui.notice(f"{josa(repr_q(title), '은/는')} 아직 열리지 않았다 — {why}.",
                      "danger")
            return
        if self._refused(need_aff, title, st.currency_name):
            return
        if not self._pay(st, price, "episode", title):
            return

        nm = self.char.name
        self.push("sys", f"──  {title}  ──  ({st.currency_symbol} -{price})")
        self.redraw()

        # 1막
        got = self._scene(st, (
            f"[이야기] {title}\n[전제] {premise}\n\n"
            f"이 이야기의 첫 장면을 쓰고 {nm}의 첫 마디를 쓰라. 상대가 고를 "
            "행동 3개를 제시하라. 아직 결말로 가지 마라."),
            clamp=2, choices=True, setting=premise)
        self.speak(got, kind="episode")
        act1 = self._take_action(got["choices"])
        if not act1:
            # 시작한 이야기는 돈을 돌려주지 않는다 — 대신 끝난 것도 아니다
            self.push("sys", f"{josa(repr_q(title), '은/는')} 다음에 다시 "
                             "이어 갈 수 있다.")
            self.redraw()
            return

        # 2막
        got2 = self._scene(st, (
            f"[이야기] {title}\n[전제] {premise}\n\n"
            f"[방금 {josa(nm, '이/가')} 한 말] {llm.inline_text(got['line'])}\n"
            f"[상대가 고른 행동] {llm.inline_text(act1)}\n\n"
            f"이야기가 깊어지는 두 번째 장면이다. {nm}의 반응과, 이 이야기의 "
            f"핵심 — {josa(nm, '이/가')} 숨겨 온 것 — 이 드러나기 직전까지 쓰라. "
            "상대가 고를 행동 3개를 다시 제시하라."), clamp=3, choices=True)
        self.speak(got2, kind="episode")
        act2 = self._take_action(got2["choices"])
        if not act2:
            self.push("sys", f"{josa(repr_q(title), '은/는')} 다음에 다시 "
                             "이어 갈 수 있다.")
            self.redraw()
            return

        # 3막 — 결말
        got3 = self._scene(st, (
            f"[이야기] {title}\n[전제] {premise}\n\n"
            f"[방금 {josa(nm, '이/가')} 한 말] {llm.inline_text(got2['line'])}\n"
            f"[상대가 고른 행동] {llm.inline_text(act2)}\n\n"
            f"이야기의 마지막 장면이다. {nm}의 반응으로 이 이야기를 맺어라. "
            f"상대의 행동이 진심이고 {josa(nm, '을/를')} 향한 것이었으면 크게 "
            "마음이 움직인다(+4~+8). 상처를 건드렸으면 음수도 된다."),
            clamp=8, choices=False)
        with db.tx(self.con):
            db.flag(self.con, f"episode_done_{key}", db.now())
            recall.remember(self.con, "event",
                            f"함께 겪은 일: {title}. 상대는 '{act1}', "
                            f"그리고 '{act2}' 했다.", 4)
            social.log(self.con, self.char.id, "episode", key, title)
        self.speak(got3, kind="episode")

    # ── 기록 화면 ──────────────────────────────────────────────────────
    def status(self):
        con, st = self.con, self.state()
        self.page()
        work_days = [
            V.WorkDay(day=r["day"], tools=r["tools"], edits=r["edits"],
                      commits=r["commits"], fails=r["fails"], money=r["lcl"])
            for r in con.execute(
                "SELECT day,tools,edits,commits,fails,lcl,stops FROM daily "
                "WHERE player=? ORDER BY day DESC LIMIT 7", (db.PLAYER,))]
        ledger = [
            V.LedgerRow(when=r["ts"][5:16], kind=r["kind"],
                        delta=r["delta_aff"], reason=r["reason"] or "")
            for r in con.execute(
                "SELECT ts,kind,delta_aff,reason FROM ledger "
                "WHERE player=? AND char=? AND delta_aff!=0 "
                "ORDER BY id DESC LIMIT 8", (db.PLAYER, self.char.id))]
        pending = [(text, stance.target_label(target, self.char), age)
                   for _pid, text, target, age in stance.open_promises(con)]
        kept = [r["text"] for r in stance.promises(con, status="kept",
                                                   limit=3)]
        ui.status(V.StatusView(
            player=st.player, char_name=self.char.name,
            axes=[V.Axis("호감", st.affection, 40),
                  V.Axis("신뢰", st.trust, 40),
                  V.Axis("관심", st.interest, 40),
                  V.Axis("인내", st.patience, 40)],
            impression=db.get(con, "impression"),
            doubts=db.get(con, "doubts"),
            broken_promises=stance.check_broken_promises(con),
            open_promises=pending, kept_promises=kept,
            care=self.care_lines(),
            work_days=work_days, ledger=ledger,
            total_earned=db.geti(con, "total_earned"),
            money=st.money, met_count=db.geti(con, "met_count"),
            currency_symbol=st.currency_symbol))

    def memory(self):
        self.page()
        rows = [V.MemoryRow(date=m["ts"][:10], kind=m["kind"],
                            text=m["text"], weight=m["weight"])
                for m in self.con.execute(
                    "SELECT ts,kind,text,weight FROM memory "
                    "WHERE player=? AND char=? AND status<>'forgotten' "
                    "ORDER BY weight DESC, id DESC LIMIT 24",
                    (db.PLAYER, self.char.id))]
        ui.memory(V.MemoryView(char_name=self.char.name, rows=rows,
                               pending=recall.pending_count(self.con)))

    def worklog(self):
        """캐릭터가 보고 있는 근무 기록을 그대로 보여준다."""
        self.scan()
        self.page()
        digest = self.work.digest(self.con)
        recent = events.recent(self.con, days=7, limit=6)
        ui.worklog(V.WorklogView(
            char_name=self.char.name,
            today=digest.splitlines() if digest else [],
            past=self.work.past_days(self.con, 5),
            events=[f"{ts[5:16]}  {text}" for _i, ts, _k, text in recent]))

    def help(self):
        self.page()
        w = world.active()
        ui.help(V.HelpView(
            rows=[("그냥 입력", "말을 건다"),
                  ("/talk <말>", "같음"),
                  ("↑↓ Enter", "목록에서 고른다  ·  Esc 취소"),
                  ("/date", "갈 곳을 고른다  ·  /date roof 로 바로 지정도 된다"),
                  ("/gift", "선물을 고른다  ·  /gift plant 로 바로 지정도 된다"),
                  ("/care", "며칠 동안 챙겨 준다  ·  인내가 빨리 돌고 관심이 "
                            "식지 않는다"),
                  ("/episode", "한 번뿐인 이야기  ·  관계가 깊어지면 열린다"),
                  ("/status", "관계 상태 · 약속 · 근무 기록 · 변화 내역"),
                  ("/memory", "기억하는 것들"),
                  ("/work", "단말로 보고 있는 근무 기록과 눈에 띄는 일"),
                  ("/clear", "화면 정리"),
                  ("/quit", "나간다")],
            notes=[f"{josa(w.currency_name, '은/는')} 훅이 설치된 에이전트로 "
                   "실제 작업을 할 때마다 쌓인다.",
                   "약속은 지킬 수 있다 — 약속한 곳에 가고, 약속한 것을 "
                   "주고, 다시 찾아오고, 쉬겠다고 했으면 정말 쉬면 된다."]))


def repr_q(text: str) -> str:
    """작은따옴표로 감싼다. 조사는 따옴표 안의 마지막 글자를 보고 붙는다."""
    return f"'{text}'"


class _null:
    def __enter__(self): return None
    def __exit__(self, *a): return False
