# -*- coding: utf-8 -*-
"""게임 루프.

여기서는 '무엇을 보여줄지'만 정한다. '어떻게 보일지'는 UI 플러그인이
정한다 — 그래서 이 파일에는 색도, 좌표도, rich 도 없다.
화면에 뭔가를 내보낼 때는 언제나 뷰 모델(ui.view)을 만들어 넘긴다.
"""
import os
import random
import uuid

from . import (characters, config, db, economy, llm, persona, recall, scenes,
               settings, stance, ui, world)
from .ui import view as V

HINT = [
    V.Hint("/date", "데이트"), V.Hint("/gift", "선물"),
    V.Hint("/status", "기록"), V.Hint("/memory", "기억"),
    V.Hint("/work", "일지"), V.Hint("/help", "?"), V.Hint("/quit", "나감"),
]


class Game:
    def __init__(self, con, char, *, offline=False, animate=True):
        self.con = con
        self.char = char
        self.offline = offline
        self.animate = animate
        self.buf = []          # 화면에 보일 최근 로그 (V.LogEntry)
        self.framed = False    # 하단 고정 프레임이 화면에 그려져 있는가
        self.sess = uuid.uuid4().hex[:12]      # 이번 접속 식별자
        self.typing = settings.get("typing_speed", 0.028)
        recall.ensure(con)
        from . import work
        self.work = work
        work.ensure(con)
        self.scan()

    def scan(self):
        """트랜스크립트에서 새로 쌓인 작업 기록을 읽어들인다(증분)."""
        try:
            return self.work.scan(self.con)
        except Exception:
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

    def context(self, st, extra="", *, query="", with_convo=True, boring=""):
        con = self.con
        mems = [t for t, _ in recall.relevant(con, query, n=8)]
        return persona.context_block(
            self.char,
            stance_block=stance.block(con, stance.read(con), self.char,
                                      boring=boring),
            aff=st.affection, stage_name=st.stage,
            stage_guide=st.stage_guide, money=st.money,
            currency=st.currency_name,
            today_tools=st.tools, today_commits=st.commits,
            days_since=economy.days_since_active(con),
            streak=st.streak, memories=mems,
            work_today=self.work.digest(con),
            work_past=self.work.past_days(con, 3),
            last_convo=recall.render(
                recall.last_conversation(con, self.sess, 6), self.char.name),
            this_convo=recall.render(
                recall.this_conversation(con, self.sess, 8),
                self.char.name) if with_convo else "",
            danger_note=extra or db.flag(con, "last_danger"),
        )

    # ── 출력 ───────────────────────────────────────────────────────────
    def push(self, role, text, emotion=""):
        self.buf.append(V.LogEntry(role, text, emotion))
        self.buf = self.buf[-40:]

    def redraw(self, *, animate=False):
        ui.frame(self.state(), self.buf, HINT,
                 animate=animate, delay=self.typing)
        self.framed = True

    def page(self):
        """흐르는 출력(목록·기록 화면) 시작 — 프레임이 깨졌음을 표시."""
        self.framed = False

    LABEL = {"trust": "신뢰", "interest": "관심", "patience": "인내"}

    def speak(self, got, *, kind="talk"):
        """캐릭터의 발화를 화면·DB에 반영하고 관계 변화를 적용."""
        narration = got.get("narration", "")
        line, emotion = got.get("line", "…"), got.get("emotion", "neutral")
        inner, delta = got.get("inner", ""), got.get("affection_delta", 0)

        if narration:
            self.push("narr", narration)
        self.push("rei", line, emotion)
        db.say(self.con, "rei", line, emotion, self.sess)
        if inner:
            self.push("inner", inner)
        if delta:
            economy.apply(self.con, aff=delta, kind=kind, reason=line[:60])
        moved = stance.apply_response(self.con, got)

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

        if got.get("memory"):
            recall.remember(self.con, "fact", got["memory"])
        db.bump(self.con, "turns", 1)
        self.con.commit()
        self.redraw(animate=self.animate)

    # ── 캐릭터에게 묻기 ────────────────────────────────────────────────
    def ask(self, st, user_msg, *, extra_ctx="", clamp=3, query="",
            boring="", want_impression=False):
        sysp = persona.system_prompt(
            self.char, self.context(st, extra_ctx, query=query, boring=boring))
        if want_impression:
            sysp += persona.impression_rules(self.char.name)
        raw = llm.ask(self.con, sysp, user_msg, offline=self.offline)
        got = llm.normalize(raw, clamp=clamp) if raw else None
        if got:
            return got
        return persona.fallback_response(self.con, st, self.char)

    def _thinking(self):
        return ui.thinking(self.char.name) if not self.offline else _null()

    # ── 명령 ───────────────────────────────────────────────────────────
    def consolidate(self):
        """쌓인 대화를 기억으로 압축. 접속당 최대 1회, LLM 1호출."""
        if self.offline:
            return 0

        def ask_fn(system, user):
            return llm.ask(self.con, system, user, offline=self.offline)

        try:
            made = recall.consolidate(self.con, ask_fn, name=self.char.name)
        except Exception:                                     # noqa: BLE001
            if os.environ.get("NERV_DEBUG") or os.environ.get("REI_DEBUG"):
                import traceback
                traceback.print_exc()
            else:
                ui.dim("기억을 정리하지 못했다.")
            return 0
        self.con.commit()
        return made

    def greet(self):
        days, _ = economy.settle_neglect(self.con)
        recovered = stance.recover_patience(self.con)
        chilled = stance.decay_interest(self.con, days)
        broken = stance.settle_promises(self.con)
        db.bump(self.con, "met_count", 1)
        self.con.commit()
        st = self.state()
        danger = db.flag(self.con, "last_danger")

        # 방치 페널티는 훅(SessionStart)이 먼저 적용했을 수 있다.
        # 적용 주체와 무관하게 아직 알리지 않은 몫을 여기서 알린다.
        pending = db.geti(self.con, "neglect_notify")
        if pending:
            nd = db.geti(self.con, "neglect_notify_days") or days
            self.push("sys", f"{nd}일 동안 오지 않았다.  호감 {pending}")
            db.put(self.con, "neglect_notify", 0)
            db.put(self.con, "neglect_notify_days", 0)
        if chilled:
            self.push("sys", f"관심 {chilled}")
        if broken:
            self.push("sys", f"지키지 않은 약속 {broken}건.  신뢰가 깎였다.")
        if danger:
            self.push("sys", f"{self.char.name}는 그 일을 알고 있다: {danger}")
        if recovered:
            self.push("delta", f"인내 +{recovered}  (시간이 지났다)")

        nm = self.char.name
        msg = (f"상대가 {days}일 만에 나타났다. 첫 마디를 건네라."
               if days >= 2 else
               f"상대가 단말 앞에 앉아 {nm}를 찾아왔다. 첫 마디를 건네라.")
        if danger:
            msg += f" {nm}는 상대가 '{danger}' 를 한 것을 알고 있고, 그게 마음에 걸린다."
        msg += ("\n오늘 상대가 한 일이나 지난번 대화 중 하나를 짧게 건드려도 좋다. "
                "다 언급하지는 마라. 한 마디면 된다.")
        prev = db.get(self.con, "last_greeting")
        if prev:
            msg += (f"\n\n지난번에 만났을 때 {nm}의 첫 마디는 '{prev}' 였다. "
                    "같은 말도, 같은 소재도 반복하지 마라. 다른 것을 골라라.")

        with self._thinking():
            got = self.ask(st, msg, clamp=1,
                           want_impression=stance.wants_impression(self.con))
        if not got["narration"] and not self.offline:
            got["narration"] = random.choice(self.char.greet_narr)
        self.speak(got, kind="greet")
        db.put(self.con, "last_greeting", got["line"])
        db.flag(self.con, "last_danger", "")

    def talk(self, text):
        if not text.strip():
            self.page()
            ui.dim("무슨 말을 할까?")
            return
        st = self.state()
        self.scan()
        boring = stance.check_boring(self.con, text)
        self.push("user", text)
        db.say(self.con, "user", text, "", self.sess)
        if boring:
            ib, pb = db.geti(self.con, "interest"), db.geti(self.con, "patience")
            ia = stance.move(self.con, "interest", config.INTEREST_BORING)
            pa = stance.move(self.con, "patience", config.PATIENCE_BORING)
            db.log(self.con, "boring", 0, 0, boring)
            bits = [f"{n} {d}" for n, d in
                    (("관심", ia - ib), ("인내", pa - pb)) if d]
            if bits:
                self.push("delta", " · ".join(bits) + f"  ({boring})")
        self.con.commit()
        self.redraw()
        msg = (f"[상대가 방금 한 말]\n{text}\n\n"
               f"{self.char.name}로서 응답하라.")
        want_imp = stance.wants_impression(self.con)
        with self._thinking():
            got = self.ask(st, msg, query=text, boring=boring,
                           want_impression=want_imp)
        self.speak(got)

    # ── 선물 ───────────────────────────────────────────────────────────
    def show_gifts(self):
        st = self.state()
        self.page()
        rows = []
        for k, (name, price, need, _, _) in scenes.gift_list(
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
                  for k, v in scenes.locked_gifts(self.char.gifts,
                                                  st.affection)]
        ui.shop(V.ShopView(title="상점 — 선물", rows=rows, locked=locked,
                           money=st.money,
                           currency_symbol=st.currency_symbol,
                           hint="/gift <이름>  으로 건넨다."))

    def gift(self, key):
        st = self.state()
        if not key:
            return self.show_gifts()
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
        why = stance.refuses(self.con, need=need, what=name)
        if why:
            self.push("sys", f"{name} 을(를) 내밀었다. "
                             f"({why} — {st.currency_name} 은 쓰이지 않았다.)")
            narr, line = stance.refusal_line(self.char, why)
            self.speak(_flat(narr or f"{self.char.name}는 받지 않았다.", line))
            return
        if not economy.spend(self.con, price, "gift", name):
            self.page()
            ui.notice(f"{st.currency_name}이 부족하다. "
                      f"({st.currency_symbol} {price} 필요 / "
                      f"보유 {st.currency_symbol} {st.money})", "danger")
            return

        self.con.execute(
            "INSERT INTO owned(player,char,item,count,given) VALUES(?,?,?,0,1) "
            "ON CONFLICT(player,char,item) DO UPDATE SET given=given+1",
            (db.PLAYER, self.char.id, key))
        again = self.con.execute(
            "SELECT given FROM owned WHERE player=? AND char=? AND item=?",
            (db.PLAYER, self.char.id, key)).fetchone()["given"]

        self.push("sys", f"{name} 을(를) 건넸다.  "
                         f"({st.currency_symbol} -{price})")
        self.con.commit()
        self.redraw()

        nm = self.char.name
        msg = (f"상대가 {nm}에게 '{name}' 을(를) 건넸다.\n"
               f"[이 물건이 {nm}에게 갖는 의미]\n{meaning}\n")
        if again > 1:
            msg += (f"\n주의: 이건 {again}번째로 같은 걸 받는 것이다. "
                    f"{nm}는 반복을 알아챈다. 감흥이 처음만 못하다.\n")
        msg += f"\n선물을 받은 {nm}로서 반응하라."

        clamp = max(1, base) if again == 1 else 1
        with self._thinking():
            got = self.ask(st, msg, clamp=clamp)
        if again > 1:
            got["affection_delta"] = min(got["affection_delta"], 1)
        recall.remember(self.con, "gift", f"{name}을(를) 받았다.", 2)
        self.speak(got, kind="gift")

    # ── 데이트 ─────────────────────────────────────────────────────────
    def show_dates(self):
        st = self.state()
        self.page()
        rows = [V.ShopRow(key=k, name=v[0], price=v[1], need=v[2],
                          affordable=st.money >= v[1])
                for k, v in scenes.date_list(self.char.dates, st.affection)]
        locked = [V.ShopRow(key=k, name=v[0], price=v[1], need=v[2],
                            locked=True)
                  for k, v in scenes.locked_dates(self.char.dates,
                                                  st.affection)]
        ui.shop(V.ShopView(title="갈 수 있는 곳", rows=rows, locked=locked,
                           money=st.money,
                           currency_symbol=st.currency_symbol,
                           hint="/date <이름>  으로 청한다."))

    def date(self, key):
        st = self.state()
        if not key:
            return self.show_dates()
        spot = self.char.dates.get(key)
        if not spot:
            self.page()
            ui.notice(f"'{key}' 라는 곳은 없다.", "danger")
            return
        name, price, need, setting = spot
        if st.affection < need:
            self.page()
            ui.notice(f"{self.char.name}는 아직 따라나서지 않을 것이다. "
                      f"(호감 {need} 필요)", "danger")
            return
        why = stance.refuses(self.con, need=need, what=name)
        if why:
            self.push("sys", f"{name} — 청했다. "
                             f"({why} — {st.currency_name} 은 쓰이지 않았다.)")
            narr, line = stance.refusal_line(self.char, why)
            self.speak(_flat(narr, line))
            return
        if not economy.spend(self.con, price, "date", name):
            self.page()
            ui.notice(f"{st.currency_name}이 부족하다. "
                      f"({st.currency_symbol} {price} 필요 / "
                      f"보유 {st.currency_symbol} {st.money})", "danger")
            return

        self.push("sys", f"──  {name}  ──  데이트  "
                         f"({st.currency_symbol} -{price})")
        self.con.commit()
        self.redraw()

        # 1막: 장면과 선택지
        sysp = persona.system_prompt(self.char, self.context(st))
        scene_rules = (
            "\n[이번 출력만 특별 규칙]\n"
            'JSON에 "choices" 배열을 추가한다. 상대가 고를 수 있는 행동/대사 3개.\n'
            "선택지는 서로 성격이 달라야 한다: 하나는 무난하게, 하나는 상대의 "
            "마음에 깊이 다가가되 위험하게, 하나는 엉뚱하거나 거리를 두는 것.\n"
            "선택지는 상대(플레이어)의 1인칭 행동/대사로 쓴다. 각 20자 이내.\n"
        )
        nm = self.char.name
        msg = (f"[장소] {name}\n{setting}\n\n"
               f"{nm}와 단둘이 이 장소에 왔다. 도착한 순간의 장면과 {nm}의 "
               "첫 마디를 쓰고, 상대가 고를 행동 3개를 제시하라.")
        with self._thinking():
            raw = llm.ask(self.con, sysp + scene_rules, msg,
                          offline=self.offline)
        got = llm.normalize(raw, clamp=2) if raw else None

        default_choices = ["옆에 조용히 앉는다", "무슨 생각을 하냐고 묻는다",
                           "말없이 하늘을 본다"]
        if not got:
            narr, line, emo = persona.fallback(self.char, st.stage_idx)
            got = {"narration": setting.split(".")[0] + ".", "line": line,
                   "emotion": emo, "affection_delta": 0, "inner": "",
                   "memory": "", "choices": list(default_choices)}
        elif not got["choices"]:
            # 장면은 살리고 선택지만 기본값으로 채운다
            got["choices"] = list(default_choices)
        self.speak(got, kind="date")

        for i, c in enumerate(got["choices"], 1):
            self.push("opt", f"{i}. {c}")
        self.redraw()
        from . import term
        pick = term.ask_line("  고른다 (번호, 또는 직접 입력) > ",
                             rgb=(111, 119, 131))
        if not pick:
            self.page()
            ui.dim("…아무것도 하지 않았다.")
            return
        if pick.isdigit() and 1 <= int(pick) <= len(got["choices"]):
            action = got["choices"][int(pick) - 1]
        else:
            action = pick
        self.push("user", action)
        db.say(self.con, "user", action, "", self.sess)
        self.redraw()

        # 2막: 판정
        msg2 = (f"[장소] {name}\n{setting}\n\n"
                f"[방금 {nm}가 한 말] {got['line']}\n"
                f"[상대가 고른 행동] {action}\n\n"
                f"이 행동에 대한 {nm}의 반응을 쓰라. 데이트의 마무리 장면이다.\n"
                f"행동이 진심이고 {nm}를 향한 것이면 크게 마음이 움직인다(+4~+8). "
                f"무난하면 +1~+3. 성의 없거나 {nm}를 도구 취급하면 음수(-5까지).")
        with self._thinking():
            raw2 = llm.ask(self.con, sysp, msg2, offline=self.offline)
        got2 = llm.normalize(raw2, clamp=8) if raw2 else None
        if not got2:
            narr, line, emo = persona.fallback(self.char, st.stage_idx)
            got2 = {"narration": narr, "line": line, "emotion": emo,
                    "affection_delta": 1, "inner": "", "memory": "",
                    "choices": []}
        recall.remember(self.con, "date",
                        f"{name}에 함께 갔다. 상대는 '{action}' 했다.", 2)
        self.speak(got2, kind="date")

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
        ui.status(V.StatusView(
            player=st.player, char_name=self.char.name,
            axes=[V.Axis("호감", st.affection, 40),
                  V.Axis("신뢰", st.trust, 40),
                  V.Axis("관심", st.interest, 40),
                  V.Axis("인내", st.patience, 40)],
            impression=db.get(con, "impression"),
            doubts=db.get(con, "doubts"),
            broken_promises=stance.check_broken_promises(con),
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
                    "WHERE player=? AND char=? "
                    "ORDER BY weight DESC, id DESC LIMIT 24",
                    (db.PLAYER, self.char.id))]
        ui.memory(V.MemoryView(char_name=self.char.name, rows=rows,
                               pending=recall.pending_count(self.con)))

    def worklog(self):
        """캐릭터가 보고 있는 근무 기록을 그대로 보여준다."""
        self.scan()
        self.page()
        digest = self.work.digest(self.con)
        ui.worklog(V.WorklogView(
            char_name=self.char.name,
            today=digest.splitlines() if digest else [],
            past=self.work.past_days(self.con, 5)))

    def help(self):
        self.page()
        w = world.active()
        ui.help(V.HelpView(
            rows=[("그냥 입력", "말을 건다"),
                  ("/talk <말>", "같음"),
                  ("/date", "갈 수 있는 곳 목록  ·  /date roof 처럼 지정"),
                  ("/gift", "선물 목록  ·  /gift plant 처럼 지정"),
                  ("/status", "근무 기록과 호감도 변화 내역"),
                  ("/memory", "기억하는 것들"),
                  ("/work", "단말로 보고 있는 근무 기록"),
                  ("/clear", "화면 정리"),
                  ("/quit", "나간다")],
            notes=[f"{w.currency_name}은 훅이 설치된 에이전트로 실제 작업을 "
                   f"할 때마다 쌓인다.",
                   "파일을 고치고, 커밋하고, 세션을 마무리하면 알게 된다."]))


def _flat(narr, line):
    """거절처럼 수치가 안 움직이는 발화 하나."""
    return {"narration": narr, "line": line, "emotion": "distant",
            "affection_delta": 0, "interest_delta": 0,
            "patience_delta": 0, "trust_delta": 0,
            "inner": "", "memory": "", "mood": "flat", "choices": []}


class _null:
    def __enter__(self): return None
    def __exit__(self, *a): return False
