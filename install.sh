#!/usr/bin/env bash
# EVA 단말 — 설치 + 세팅. 이것 하나로 끝난다.
#
#   ./install.sh              설치하고 세팅 마법사까지 (묻는다)
#   ./install.sh --yes        묻지 않고 찾은 대로 기본값으로
#   ./install.sh --no-hooks   eva 명령만 등록 (훅·세팅은 안 건드린다)
#   ./install.sh --uninstall  명령·훅·상태줄 제거 (저장 데이터는 남긴다)
#
# 하는 일:
#   1. python3 (3.9 이상) 확인
#   2. rich 설치 — pip --user 가 막힌 환경(PEP 668: Homebrew 파이썬 등)이면
#      저장소 폴더에 전용 가상환경을 만들어 거기 설치한다. 시스템은 안 건드린다
#   3. ~/.local/bin/eva 등록
#   4. eva setup — 에이전트 찾기 · 훅 · 상태줄 · 로컬 작업 폴더 · 대화 엔진
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="${HOME}/.local/bin"
LINK="${BIN}/eva"
OLD_LINK="${BIN}/rei"
DATA="${NERV_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/nerv-social-terminal}"
YES=""
MODE="full"
for arg in "$@"; do
    case "$arg" in
        --yes|-y) YES="--yes" ;;
        --no-hooks) MODE="bare" ;;
        --uninstall) MODE="uninstall" ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "모르는 옵션: $arg  (./install.sh --help)" >&2; exit 2 ;;
    esac
done

say() { printf '  %s\n' "$*"; }

# 우리가 만든 링크인가 — 어느 클론이든 nervterm 옆의 eva/rei 를 가리키는 것.
# 같은 이름의 남의 프로그램은 덮지도 지우지도 않는다.
ours() { [ -L "$1" ] && [ -d "$(dirname "$(readlink "$1")")/nervterm" ]; }
taken() { { [ -e "$1" ] || [ -L "$1" ]; } && ! ours "$1"; }
unlink_ours() { if ours "$1"; then rm -f "$1" && say "제거: $1"; fi; }

if [ "$MODE" = "uninstall" ]; then
    unlink_ours "$LINK"
    unlink_ours "$OLD_LINK"
    python3 "${ROOT}/install-hooks.py" --uninstall --agent all
    echo
    say "저장 데이터는 남겨 뒀다: ${DATA}"
    exit 0
fi

# 1) 파이썬
if ! command -v python3 >/dev/null 2>&1; then
    say "python3 가 필요하다. (macOS: brew install python · Debian/Ubuntu: apt install python3)" >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    say "python3 3.9 이상이 필요하다. 지금: $(python3 --version 2>&1)" >&2
    exit 1
fi

# 2) rich
if python3 -c "import rich" 2>/dev/null; then
    say "rich — 있음"
elif python3 -m pip install --user --quiet rich 2>/dev/null; then
    say "rich — 설치함 (pip --user)"
else
    # 시스템 파이썬이 pip 설치를 막는다(externally-managed-environment).
    # 남의 파이썬을 건드리지 않고, 게임 전용 가상환경을 만든다.
    say "rich — 시스템 파이썬에 설치할 수 없다. 전용 가상환경을 만든다: ${DATA}/venv"
    mkdir -p "$DATA"
    python3 -m venv "${DATA}/venv"
    "${DATA}/venv/bin/python3" -m pip install --quiet rich
    say "rich — 설치함 (가상환경)"
fi
PY=python3
python3 -c "import rich" 2>/dev/null || PY="${DATA}/venv/bin/python3"

# 3) eva 명령 등록 (옛 rei 링크는 제거)
mkdir -p "$BIN"
chmod +x "${ROOT}/eva"
if taken "$LINK"; then
    say "${LINK} 에 다른 프로그램이 있다 — 덮지 않았다. 옮기거나 지운 뒤 다시 돌려라." >&2
    exit 1
fi
ln -sfn "${ROOT}/eva" "$LINK"
unlink_ours "$OLD_LINK"
say "등록: $LINK -> ${ROOT}/eva"
if ! printf '%s' ":${PATH}:" | grep -q ":${BIN}:"; then
    echo
    say "주의: ${BIN} 이 PATH 에 없다. 셸 설정(~/.zshrc 등)에 아래를 추가하라."
    say "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# 4) 세팅
if [ "$MODE" = "full" ]; then
    echo
    cd "$ROOT"
    exec "$PY" -m nervterm setup $YES
fi
echo
say "명령만 등록했다. 세팅은  eva setup"
