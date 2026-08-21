#!/usr/bin/env bash
# EVA 단말 — 설치
#   ./install.sh              eva 명령 등록 + 훅 설치
#   ./install.sh --no-hooks   명령만 등록
#   ./install.sh --uninstall  둘 다 제거
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BIN="${HOME}/.local/bin"
LINK="${BIN}/eva"
OLD_LINK="${BIN}/rei"

case "${1:-}" in
  --uninstall)
      rm -f "$LINK" "$OLD_LINK" && echo "제거: $LINK"
      python3 "${ROOT}/install-hooks.py" --uninstall
      echo
      echo "저장 데이터는 남겨 뒀다: $(python3 -c "
import sys; sys.path.insert(0,'${ROOT}')
from reigame import config; print(config.db_path())")"
      exit 0 ;;
esac

# 1) eva 명령 등록 (옛 rei 링크는 제거)
mkdir -p "$BIN"
ln -sfn "${ROOT}/eva" "$LINK"
chmod +x "${ROOT}/eva"
rm -f "$OLD_LINK"
echo "등록: $LINK -> ${ROOT}/eva"

if ! printf '%s' ":${PATH}:" | grep -q ":${BIN}:"; then
    echo
    echo "  주의: ${BIN} 이 PATH 에 없다. 셸 설정에 아래를 추가하라."
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# 2) 의존성 확인
python3 -c "import rich" 2>/dev/null \
    || { echo "  rich 모듈이 필요하다: python3 -m pip install --user rich" >&2; exit 1; }

# 3) 훅 설치
if [ "${1:-}" != "--no-hooks" ]; then
    echo
    python3 "${ROOT}/install-hooks.py"
fi

echo
echo "이제 아무 디렉터리에서  eva  를 치면 된다."
