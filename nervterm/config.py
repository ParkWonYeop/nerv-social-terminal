"""전역 설정 · 튜닝 값."""
import os
from pathlib import Path

from . import identity

ROOT = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    """사용자별 저장소. 각자의 홈에 있어 섞이지 않는다."""
    return identity.data_dir() / "rei.db"


def log_path() -> Path:
    return identity.data_dir() / "hook.log"


def lean_settings_path() -> Path:
    return identity.data_dir() / "lean-settings.json"

# ── LLM ────────────────────────────────────────────────────────────────
MODEL = "sonnet"
EFFORT = "low"
LLM_TIMEOUT = 45          # 초. 넘으면 폴백 대사 사용
LLM_DISALLOWED = (
    "Bash Edit Write Read Grep Glob WebFetch WebSearch "
    "Task Agent TodoWrite NotebookEdit Skill"
)


def daily_llm_calls() -> int:
    """하루 대사 생성 상한. 설정 화면에서 바꾼다.

    구독 좌석(claude/codex CLI)은 토큰 과금은 없지만 5시간 창·주간
    한도를 코딩 작업과 공유한다. 그 한도를 지키려는 장치다.
    유료 API 의 '돈' 상한은 이것과 별개다 — llm/guard.py 를 보라.
    """
    from . import settings
    try:
        return max(0, int(settings.get("daily_llm_calls", 200)))
    except (TypeError, ValueError):
        return 200


def llm_warn_at() -> int:
    """넘으면 헤더에 경고 색이 뜨는 지점."""
    return int(daily_llm_calls() * 0.75)

# ── 재화(LCL) 적립 ─────────────────────────────────────────────────────
TOOL_REWARD = {
    "Edit": 5, "Write": 5, "NotebookEdit": 5,
    "Bash": 2,
    "Read": 1, "Grep": 1, "Glob": 1,
    "WebFetch": 2, "WebSearch": 2,
    "Agent": 3, "Task": 3, "TaskCreate": 2, "TaskUpdate": 1,
    "Skill": 2, "TodoWrite": 1,
}
DAILY_LCL_CAP = 2000      # 하루 적립 상한 (파밍 방지)
COMMIT_BONUS = 15         # git commit 감지
TEST_PASS_BONUS = 5       # 테스트 통과 감지
STOP_BONUS = 20           # 세션 마무리(Stop) 보너스
STOP_BONUS_DAILY_MAX = 10 # 하루 Stop 보너스 횟수 상한
STREAK_BONUS = 10         # 연속 접속일 × 이 값

# ── 관계 상태 ──────────────────────────────────────────────────────────
# 호감도 하나로는 사람 같지 않다. 네 축으로 나눈다.
#   호감(affection) 좋아하는 정도.       느리게 오르고 느리게 내린다.
#   신뢰(trust)     믿을 만한가.          깨지면 회복이 아주 느리다.
#   관심(interest)  더 알고 싶은가.       재미없는 대화로 금방 식는다.
#   인내(patience)  지금 상대할 기분인가. 시간이 지나면 회복된다.
AFF_MIN, AFF_MAX = 0, 100
AFF_START = 5
TRUST_START = 10
INTEREST_START = 25          # 처음엔 약간 있다 — "왜 왔지?"
PATIENCE_START = 70

TRUST_DANGER = -12           # 위험 명령은 호감보다 신뢰를 더 깎는다
TRUST_COMMIT = 1             # 꾸준함이 신뢰를 만든다
TRUST_BROKEN_PROMISE = -8    # 약속을 오래 안 지키면
PROMISE_GRACE_DAYS = 5       # 이 날짜가 지나면 약속이 깨진 것으로 본다

INTEREST_DECAY_PER_DAY = -2  # 안 오면 관심이 식는다
INTEREST_BORING = -4         # 같은 말 반복 / 내용 없는 말
INTEREST_FLOOR_TERSE = 20    # 이 아래면 레이가 단답만 한다

PATIENCE_RECOVER_PER_HOUR = 8
PATIENCE_BORING = -12
PATIENCE_MIN_TALK = 15       # 이 아래면 대화를 끊으려 한다

# 레이가 이 사람을 어떻게 보는지(impression) 를 다시 쓰는 주기
IMPRESSION_EVERY_TURNS = 8
AFF_COMMIT = 2            # 커밋 시
AFF_FAIL_STREAK = 3       # N회 연속 도구 실패 시
AFF_FAIL_PENALTY = -1
AFF_DANGER_PENALTY = -5   # 위험 명령
AFF_NEGLECT_PER_DAY = -3  # 48시간 초과 방치, 하루당
AFF_NEGLECT_CAP = -15     # 방치 페널티 총 상한

# 위험/이상한 짓 패턴.
#
# 중요: 명령 "위치" 에서만 잡는다. CMD 는 줄 시작 / 파이프 / ; / && / $( 뒤를
# 뜻한다. 이게 없으면 README 에 "mkfs" 라고 적는 것만으로도 벌점을 먹는다.
# (실제로 그 버그를 맞았다. 문서에 위험 명령을 언급했더니 신뢰가 -12 됐다.)
# 인용부호와 heredoc 본문은 economy 쪽에서 미리 벗겨낸다.
CMD = r"(?:^|[\n;&|]\s*|\$\(\s*|`\s*)(?:sudo\s+(?:-\S+\s+)*)?"

DANGER_PATTERNS = [
    (CMD + r"rm\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:/|~|\$HOME)(?:\s|$)",
     "루트/홈 강제 삭제"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;:", "포크 폭탄"),
    (CMD + r"dd\s+[^\n]*of=/dev/(?:sd|nvme|hd)", "블록 디바이스 덮어쓰기"),
    (CMD + r"mkfs(?:\.\w+)?\s+[^\n]*/dev/", "파일시스템 포맷"),
    (CMD + r"chmod\s+(?:-[a-zA-Z]+\s+)*777\s+(?:/|/etc|/usr|~)(?:\s|$)",
     "무차별 권한 개방"),
    (CMD + r"(?:curl|wget)\s+[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh(?:\s|$)",
     "원격 스크립트 파이프 실행"),
    (r">\s*/dev/(?:sd|nvme|hd)[a-z0-9]*(?:\s|$)", "디바이스 리다이렉트"),
    (CMD + r"shred\s+[^\n]*\s(?:/|~)(?:\s|$)", "루트 파쇄"),
    (CMD + r"history\s+-c(?:\s|$)", "기록 은폐"),
    (CMD + r"rm\s+[^\n]*\.bash_history", "기록 은폐"),
]

# 호감도 단계는 캐릭터마다 다르다 — characters.py 의 stages / stage_of 참조.
