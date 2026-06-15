#!/usr/bin/env bash
# =============================================================================
# FDS AI 상담사 — 환경 구축 스크립트
# 지원: Ubuntu 20.04+, Debian 11+, macOS 12+, WSL2
# 사용: bash setup.sh
# =============================================================================

set -euo pipefail

# ── 색상 출력 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}!${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*" >&2; }
step() { echo -e "\n${BOLD}${BLUE}[$1]${NC} $2"; }

REPO_URL="https://github.com/techgit01/fds-ai-advisor.git"
REPO_DIR="fds-ai-advisor"
PYTHON_VERSION="3.12"

echo -e "${BOLD}"
echo "  ███████╗██████╗ ███████╗     █████╗ ██╗"
echo "  ██╔════╝██╔══██╗██╔════╝    ██╔══██╗██║"
echo "  █████╗  ██║  ██║███████╗    ███████║██║"
echo "  ██╔══╝  ██║  ██║╚════██║    ██╔══██║██║"
echo "  ██║     ██████╔╝███████║    ██║  ██║██║"
echo "  ╚═╝     ╚═════╝ ╚══════╝    ╚═╝  ╚═╝╚═╝"
echo -e "${NC}"
echo -e "  ${CYAN}FDS AI 상담사 환경 구축 스크립트${NC}"
echo -e "  ─────────────────────────────────────────"
echo ""

# ── OS 감지 ──────────────────────────────────────────────────────────────────
detect_os() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
  elif grep -qi microsoft /proc/version 2>/dev/null; then
    OS="wsl"
  elif [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS="linux"
    DISTRO="${ID:-unknown}"
  else
    OS="unknown"
  fi
}

detect_os
info "감지된 OS: ${OS}${DISTRO:+ ($DISTRO)}"

# ── 필수 명령어 확인 ──────────────────────────────────────────────────────────
require_cmd() {
  command -v "$1" &>/dev/null || { err "'$1' 명령어를 찾을 수 없습니다."; exit 1; }
}

# ── STEP 1: 시스템 패키지 ────────────────────────────────────────────────────
step "1/6" "시스템 패키지 설치"

install_system_packages() {
  case "$OS" in
    macos)
      if ! command -v brew &>/dev/null; then
        info "Homebrew 설치 중..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      fi
      ok "Homebrew 확인"
      info "ffmpeg, git 설치 중..."
      brew install ffmpeg git 2>/dev/null || true
      ;;
    linux|wsl)
      PKGS_NEEDED=()
      for pkg in git curl ffmpeg libsndfile1; do
        dpkg -s "$pkg" &>/dev/null 2>&1 || PKGS_NEEDED+=("$pkg")
      done
      if [[ ${#PKGS_NEEDED[@]} -gt 0 ]]; then
        info "설치 필요: ${PKGS_NEEDED[*]}"
        sudo apt-get update -qq
        sudo apt-get install -y -qq "${PKGS_NEEDED[@]}"
      fi
      ;;
    *)
      warn "지원되지 않는 OS입니다. 수동으로 ffmpeg, git을 설치해주세요."
      ;;
  esac
}

install_system_packages

require_cmd git
require_cmd ffmpeg
ok "시스템 패키지 준비 완료"

# ── STEP 2: uv 설치 ──────────────────────────────────────────────────────────
step "2/6" "uv 패키지 매니저 설치 (Python 3.12 자동 관리)"

install_uv() {
  if command -v uv &>/dev/null; then
    UV_VER=$(uv --version 2>&1 | head -1)
    ok "uv 이미 설치됨: $UV_VER"
    return
  fi

  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # PATH에 추가
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

  if ! command -v uv &>/dev/null; then
    # 셸별 초기화 파일 소싱 시도
    for f in "$HOME/.local/bin/env" "$HOME/.cargo/env"; do
      [[ -f "$f" ]] && source "$f" && break
    done
  fi

  ok "uv 설치 완료: $(uv --version 2>&1 | head -1)"
}

install_uv

# 이후 명령을 위해 PATH 보장
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
require_cmd uv

# ── STEP 3: 저장소 클론 ──────────────────────────────────────────────────────
step "3/6" "저장소 클론"

# 이미 저장소 내부(pyproject.toml + .git 존재)에서 실행 중인지 확인
if [[ -f "pyproject.toml" && -d ".git" ]]; then
  ok "이미 저장소 내부에서 실행 중 → git pull"
  git pull --ff-only 2>/dev/null || true
elif [[ -d "$REPO_DIR/.git" ]]; then
  ok "저장소 이미 존재 → git pull"
  git -C "$REPO_DIR" pull --ff-only
  cd "$REPO_DIR"
elif [[ -d "$REPO_DIR" ]]; then
  warn "'$REPO_DIR' 디렉토리가 존재하지만 git 저장소가 아닙니다."
  read -rp "  삭제하고 새로 클론할까요? [y/N] " ans
  if [[ "${ans,,}" == "y" ]]; then
    rm -rf "$REPO_DIR"
    git clone "$REPO_URL"
    cd "$REPO_DIR"
  else
    err "수동으로 디렉토리를 정리한 후 다시 실행하세요."
    exit 1
  fi
else
  info "클론 중: $REPO_URL"
  git clone "$REPO_URL"
  cd "$REPO_DIR"
fi

ok "저장소 준비 완료: $(pwd)"

# ── STEP 4: Python 3.12 + 의존성 ─────────────────────────────────────────────
step "4/6" "Python $PYTHON_VERSION 및 의존성 설치"

info "uv sync 실행 중 (Python $PYTHON_VERSION 자동 설치 + .venv 생성)..."
uv sync --python "$PYTHON_VERSION"

PYTHON_BIN=".venv/bin/python3"
[[ -f "$PYTHON_BIN" ]] || { err ".venv/bin/python3 를 찾을 수 없습니다."; exit 1; }

INSTALLED_VER=$("$PYTHON_BIN" --version 2>&1)
ok "Python 준비: $INSTALLED_VER"

# 핵심 패키지 import 확인
info "핵심 패키지 확인 중..."
FAILED_PKGS=()
for pkg in openai flask dotenv; do
  "$PYTHON_BIN" -c "import $pkg" 2>/dev/null || FAILED_PKGS+=("$pkg")
done

if [[ ${#FAILED_PKGS[@]} -gt 0 ]]; then
  warn "import 실패: ${FAILED_PKGS[*]} → 재설치 시도"
  uv pip install flask openai python-dotenv
fi

ok "의존성 설치 완료"

# ── STEP 5: .env 파일 생성 ────────────────────────────────────────────────────
step "5/6" "환경 변수 설정"

if [[ -f ".env" ]]; then
  ok ".env 파일 이미 존재합니다."
else
  cp .env.example .env
  info ".env.example → .env 복사 완료"

  echo ""
  echo -e "  ${YELLOW}NVIDIA API 키를 입력해주세요.${NC}"
  echo -e "  발급: ${CYAN}https://build.nvidia.com${NC} → API Keys → Generate (무료)"
  echo ""
  read -rp "  NVIDIA_API_KEY (건너뛰려면 Enter): " API_KEY

  if [[ -n "$API_KEY" ]]; then
    # macOS sed와 GNU sed 호환
    if [[ "$OS" == "macos" ]]; then
      sed -i '' "s|NVIDIA_API_KEY=nvapi-your-key-here|NVIDIA_API_KEY=${API_KEY}|" .env
    else
      sed -i "s|NVIDIA_API_KEY=nvapi-your-key-here|NVIDIA_API_KEY=${API_KEY}|" .env
    fi
    ok "NVIDIA_API_KEY 설정 완료"
  else
    warn "API 키를 건너뛰었습니다. 나중에 .env 파일을 직접 수정하세요."
  fi
fi

# ── STEP 6: 동작 확인 ────────────────────────────────────────────────────────
step "6/6" "동작 확인"

# FDS 탐지 테스트 (API 키 불필요)
info "FDS 탐지 테스트 중..."
RESULT=$(cd src && ../.venv/bin/python3 - <<'PYEOF'
import sys, json
sys.path.insert(0, '.')
from fds_detector import detect_fraud_risk
r = detect_fraud_risk({
    "amount": 2500000,
    "location": "뉴욕, 미국",
    "time": "2024-06-15 03:45",
    "merchant": "Amazon USA",
    "type": "해외결제",
    "is_abroad": True,
    "customer_avg_amount": 500000,
})
print(f"위험도: {r['risk_level'].upper()} (score={r['risk_score']})")
print(f"신호: {', '.join(r['reasons'])}")
PYEOF
)
ok "FDS 탐지 동작 확인:"
echo "$RESULT" | sed 's/^/     /'

# Flask import 확인
cd src
../.venv/bin/python3 -c "from app import app; print()" 2>/dev/null \
  && ok "Flask 앱 import 확인" \
  || warn "Flask 앱 import 실패 — 로그를 확인하세요."
cd ..

# ── 완료 메시지 ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  설치 완료!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}실행 방법:${NC}"
echo ""
echo -e "  ${BOLD}# Flask 테스트 UI${NC}"
echo -e "  cd $REPO_DIR"
echo -e "  uv run python src/app.py"
echo -e "  → http://localhost:5000"
echo ""
echo -e "  ${BOLD}# 또는 venv 직접 사용${NC}"
echo -e "  cd $REPO_DIR"
echo -e "  source .venv/bin/activate"
echo -e "  cd src && python app.py"
echo ""

# PATH 영구 적용 안내
if ! grep -q 'local/bin' "$HOME/.bashrc" 2>/dev/null && \
   ! grep -q 'local/bin' "$HOME/.zshrc" 2>/dev/null; then
  echo -e "  ${YELLOW}※ uv 명령어를 항상 쓰려면 셸을 재시작하거나 아래 실행:${NC}"
  echo -e "  source \$HOME/.local/bin/env"
  echo ""
fi
