# -*- coding: utf-8 -*-
"""세팅 마법사 — `eva setup`. install.sh 가 마지막에 부른다.

한 번에 끝낸다:

    1. 무엇으로 일하나       설치된 에이전트를 찾아 보상·근무 기록 대상을 고른다
    2. 훅·상태줄              고른 에이전트에 훅을 단다(Claude 는 상태줄도)
    3. 로컬 작업 폴더         로컬 에이전트를 골랐으면 git 저장소를 찾을 곳
    4. 대화 엔진              쓸 수 있는 것을 찾아 연결 시험까지 하고 고른다

`eva setup --yes` 는 묻지 않고 찾은 대로 기본값을 쓴다. TTY 가 아니어도
그렇다. 몇 번을 다시 돌려도 된다 — 훅은 중복되지 않고, 이미 고른 것은
기본값으로 다시 제안한다.
"""
import os
import shutil
import subprocess
import sys

from . import agents, characters, config, db, llm, local, settings, ui, world
from .hangul import josa

ROOT = config.ROOT


class Wizard:
    def __init__(self, *, assume_yes=False):
        from . import term
        self.term = term
        self.auto = assume_yes or not term.is_tty()
        self.engine = ""          # 이번에 연결 시험을 통과한 엔진

    # ── 묻기 ───────────────────────────────────────────────────────────
    def yes(self, question, default=True) -> bool:
        if self.auto:
            return default
        mark = "Y/n" if default else "y/N"
        got = self.term.ask_line(f"  {question} [{mark}] > ")
        if got is None:
            raise KeyboardInterrupt
        got = got.strip().lower()
        if not got:
            return default
        return got in ("y", "yes", "ㅇ", "ㅇㅇ", "예", "응", "네")

    def text(self, question, default="") -> str:
        if self.auto:
            return default
        got = self.term.ask_line(f"  {question} (엔터면 {default}) > ")
        if got is None:
            raise KeyboardInterrupt
        return got.strip() or default

    def step(self, n, title):
        ui.blank()
        ui.notice(f"[{n}/4] {title}", "info")

    # ── 1. 에이전트 ────────────────────────────────────────────────────
    @staticmethod
    def detect():
        """{에이전트 id: (찾았나, 근거)}"""
        home = os.path.expanduser("~")
        found = {}
        claude = shutil.which("claude")
        found["claude"] = (bool(claude or os.path.isdir(f"{home}/.claude")),
                           claude or "~/.claude")
        codex = shutil.which("codex")
        codex_home = agents.get("codex").home()
        found["codex"] = (bool(codex or codex_home.is_dir()),
                          codex or str(codex_home))
        ollama = shutil.which("ollama")
        found["local"] = (bool(ollama or os.path.isdir(f"{home}/.ollama")),
                          ollama or "~/.ollama")
        return found

    def pick_agents(self):
        self.step(1, "무엇으로 일하나 — 보상·근무 기록 대상")
        ui.dim("체크한 것만 근무 기록을 보고, 거기서 한 작업이 재화가 된다.")
        found = self.detect()
        table = settings.get("agents", {}) or {}
        chosen = []
        for agent in agents.AGENTS:
            have, where = found[agent.id]
            # 찾았거나 이미 켜 둔 것은 '예' 가 기본
            default = have or bool(table.get(agent.id))
            ui.line(self.term.pad(agent.label, 28)
                    + ("찾음 — " + where if have else "없음"),
                    "plain" if have else "dim")
            if not have and not table.get(agent.id):
                settings.put(f"agents.{agent.id}", False)
                continue
            on = self.yes(f"{josa(agent.label, '을/를')} 쓸까?", default)
            settings.put(f"agents.{agent.id}", on)
            if on:
                chosen.append(agent)
        if not chosen:
            ui.notice("아무것도 고르지 않았다 — 재화가 쌓이지 않는다. "
                      "나중에 설정 → 보상·근무 기록 대상.", "warn")
        return chosen

    # ── 2. 훅 ──────────────────────────────────────────────────────────
    def install_hooks(self, chosen):
        self.step(2, "훅 · 상태줄")
        hooked = [a for a in chosen if a.needs_hook]
        if not hooked:
            ui.dim("훅이 필요한 에이전트를 고르지 않았다.")
            return
        for agent in hooked:
            args = [sys.executable, str(ROOT / "install-hooks.py"),
                    "--agent", agent.id]
            if agent.id == "claude" and self.yes(
                    "Claude Code 하단에 상태줄도 붙일까? "
                    "(이미 다른 상태줄이 있으면 덮지 않는다)", True):
                args.append("--statusline")
            proc = subprocess.run(args, capture_output=True, text=True)
            lines = [x for x in (proc.stdout or "").splitlines()
                     if x.strip().startswith(("+", "·", "백업", "저장",
                                              "Codex", "  ·"))]
            for line in lines[-8:]:
                ui.dim(line.strip())
            if proc.returncode == 0 and agent.hook_installed():
                ui.notice(f"{agent.label} — 훅 설치됨. 새 세션부터 적립된다.",
                          "good")
            else:
                ui.notice(f"{agent.label} — 훅을 못 달았다: "
                          f"{(proc.stderr or proc.stdout).strip()[-120:]}",
                          "danger")
            if agent.id == "codex":
                ui.dim("Codex 는 다음 실행 때 이 훅을 신뢰할지 묻는다. "
                       "승인해야 적립된다.")

    # ── 3. 로컬 ────────────────────────────────────────────────────────
    def setup_local(self, chosen):
        self.step(3, "로컬 작업 폴더")
        if not any(a.id == "local" for a in chosen):
            ui.dim("로컬 에이전트를 고르지 않았다.")
            return
        current = ", ".join(settings.get("local.roots") or ["~"])
        raw = self.text("git 저장소를 찾을 작업 폴더 (쉼표로 여럿)", current)
        dirs = [d.strip() for d in raw.split(",") if d.strip()] or ["~"]
        settings.put("local.roots", dirs)
        repos = local.find_repos([os.path.expanduser(d) for d in dirs])
        ui.notice(f"git 저장소 {len(repos)}개를 지켜본다. 지금부터 생기는 "
                  "커밋이 재화가 된다.", "good" if repos else "warn")
        for r in repos[:5]:
            ui.dim(r)
        if len(repos) > 5:
            ui.dim(f"… 외 {len(repos) - 5}개")
        with db.session() as con:
            db.init(con)
            db.put(con, "local_since", "")          # 지금부터 센다
            db.put(con, "local_repos_at", "")

    # ── 4. 대화 엔진 ───────────────────────────────────────────────────
    def pick_engine(self):
        self.step(4, "대화 엔진 — 캐릭터가 어느 모델로 말하나")
        order = ["claude-cli", "codex-cli", "ollama"]
        current = settings.get("llm.provider") or llm.DEFAULT_ID
        if current in order:
            order.remove(current)
            order.insert(0, current)
        options = []
        for pid in order:
            cand = llm.candidate(pid)
            ok, why = cand.available()
            if pid == "ollama" and ok and not cand.model:
                ok, why = False, "설치된 모델이 없다"
            options.append((cand, ok, why))
            ui.line(self.term.pad(cand.label, 28)
                    + ("쓸 수 있다" if ok else why), "plain" if ok else "dim")

        ollama = next(c for c, _, _ in options if c.id == "ollama")
        if (shutil.which("ollama") and not ollama.model
                and not any(ok for _, ok, _ in options)
                and self.yes("쓸 수 있는 엔진이 없다. 로컬 모델 "
                             "exaone3.5:7.8b(4.8GB)를 받을까?", False)):
            subprocess.run(["ollama", "pull", "exaone3.5:7.8b"])
            from .llm.http import Ollama
            Ollama._picked = None              # 자동 선택을 다시 하게
            options = [(c, *c.available()) for c, _, _ in options]

        with db.session() as con:
            db.init(con)
            for cand, ok, why in options:
                if not ok:
                    continue
                if not self.yes(f"{cand.label} 로 말하게 할까? (연결 시험을 한다)",
                                True):
                    continue
                ui.dim(f"{cand.label} — 연결 시험 중…")
                done, detail = llm.switch(con, provider_id=cand.id)
                if done:
                    ui.notice(f"{cand.label} 로 정했다.  「{detail}」", "good")
                    self.engine = f"{cand.label} · {cand.model or '기본 모델'}"
                    return
                ui.notice(f"연결 시험 실패 — {detail}", "danger")
        ui.notice("대화 엔진을 정하지 못했다. 사전 작성 대사로 돈다 — "
                  "나중에 설정 → LLM 연결.", "warn")

    # ── 전체 ───────────────────────────────────────────────────────────
    def run(self) -> int:
        ui.blank()
        ui.notice("EVA 단말 — 세팅", "info")
        ui.dim("한 번에 끝낸다. 다시 돌려도 된다(중복되지 않는다).")
        if self.auto:
            ui.dim("묻지 않고 찾은 대로 기본값을 쓴다.")
        with db.session() as con:
            db.init(con)                       # 저장소를 만들고 승격한다
        chosen = self.pick_agents()
        self.install_hooks(chosen)
        self.setup_local(chosen)
        self.pick_engine()
        self.summary()
        return 0

    def summary(self):
        ui.blank()
        ui.notice("끝났다.", "good")
        table = settings.get("agents", {}) or {}
        on = [a.label for a in agents.AGENTS if table.get(a.id)]
        ui.line(f"보상 대상   {', '.join(on) or '없음'}")
        ui.line("대화 엔진   " + (self.engine or
                                 "정하지 못했다 — 사전 작성 대사로 돈다"),
                "plain" if self.engine else "warn")
        ui.line(f"캐릭터     {len(characters.ENABLED)}명 · 세계 {world.active().name}")
        ui.blank()
        ui.dim("이제 아무 디렉터리에서  eva")
        ui.dim("Claude Code 안에서는  ! eva  또는  ! eva say 안녕")
        ui.dim("바꾸려면  eva --settings   ·   다시 세팅  eva setup")


def main(argv) -> int:
    w = world.load()
    ui.load(w)
    try:
        return Wizard(assume_yes="--yes" in argv or "-y" in argv).run()
    except KeyboardInterrupt:
        ui.blank()
        ui.notice("그만뒀다. 지금까지 고른 것은 저장됐다 — eva setup 으로 이어 간다.",
                  "warn")
        return 1

