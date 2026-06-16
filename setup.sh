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
DISTRO=""
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
step "1/7" "시스템 패키지 설치"

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

# ── STEP 2: Docker 확인 ──────────────────────────────────────────────────────
step "2/7" "Docker 확인 (Asterisk 컨테이너용)"

if command -v docker &>/dev/null; then
  DOCKER_VER=$(docker --version 2>&1 | head -1)
  ok "Docker 확인: $DOCKER_VER"
  DOCKER_OK=true
else
  warn "Docker가 설치되어 있지 않습니다. (Asterisk 자동 발신 기능에 필요)"
  DOCKER_OK=false
  case "$OS" in
    wsl|linux)
      read -rp "  지금 Docker를 자동 설치할까요? [Y/n] " ans
      if [[ "${ans,,}" != "n" ]]; then
        info "Docker 설치 중 (get.docker.com)..."
        curl -fsSL https://get.docker.com | sudo sh
        # 데몬 기동 (systemd 환경)
        if command -v systemctl &>/dev/null; then
          sudo systemctl enable --now docker 2>/dev/null || true
        else
          sudo service docker start 2>/dev/null || true
        fi
        # 현재 사용자 docker 그룹 추가 (다음 로그인부터 sudo 없이 사용)
        sudo usermod -aG docker "$USER" 2>/dev/null || true
        if sudo docker version &>/dev/null; then
          ok "Docker 설치 완료: $(sudo docker --version 2>&1 | head -1)"
          warn "그룹 적용을 위해 셸을 다시 열거나 'newgrp docker'를 실행하세요."
          DOCKER_OK=true
        else
          warn "Docker 데몬을 시작하지 못했습니다. 수동 확인이 필요합니다."
        fi
      else
        echo -e "  ${CYAN}나중에 수동 설치:${NC} curl -fsSL https://get.docker.com | sh"
      fi
      ;;
    macos)
      echo -e "  ${CYAN}Docker Desktop 설치:${NC} https://www.docker.com/products/docker-desktop"
      ;;
  esac
  echo ""
fi

# ── STEP 3: uv 설치 ──────────────────────────────────────────────────────────
step "3/7" "uv 패키지 매니저 설치 (Python 3.12 자동 관리)"

install_uv() {
  if command -v uv &>/dev/null; then
    UV_VER=$(uv --version 2>&1 | head -1)
    ok "uv 이미 설치됨: $UV_VER"
    return
  fi

  info "uv 설치 중..."
  curl -LsSf https://astral.sh/uv/install.sh | sh

  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

  if ! command -v uv &>/dev/null; then
    for f in "$HOME/.local/bin/env" "$HOME/.cargo/env"; do
      [[ -f "$f" ]] && source "$f" && break
    done
  fi

  ok "uv 설치 완료: $(uv --version 2>&1 | head -1)"
}

install_uv
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
require_cmd uv

# ── STEP 4: 저장소 클론 ──────────────────────────────────────────────────────
step "4/7" "저장소 클론"

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

# ── STEP 5: Python 3.12 + 의존성 ─────────────────────────────────────────────
step "5/7" "Python $PYTHON_VERSION 및 의존성 설치"

info "uv sync 실행 중 (Python $PYTHON_VERSION 자동 설치 + .venv 생성)..."
uv sync --python "$PYTHON_VERSION"

PYTHON_BIN=".venv/bin/python3"
[[ -f "$PYTHON_BIN" ]] || { err ".venv/bin/python3 를 찾을 수 없습니다."; exit 1; }

ok "Python 준비: $("$PYTHON_BIN" --version 2>&1)"

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

# ── STEP 6: .env 파일 생성 ────────────────────────────────────────────────────
step "6/7" "환경 변수 설정"

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

# 필요 디렉토리 생성
mkdir -p audio/agi_tmp audio/input audio/output logs cache/whisper
ok "디렉토리 생성 완료 (audio/, logs/, cache/)"

# ── STEP 7: 동작 확인 ────────────────────────────────────────────────────────
step "7/7" "동작 확인"

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

cd src
../.venv/bin/python3 -c "from app import app; print()" 2>/dev/null \
  && ok "Flask 앱 import 확인" \
  || warn "Flask 앱 import 실패 — 로그를 확인하세요."
cd ..

# VS Code 확장 설치 (code 명령어 있을 때만)
if command -v code &>/dev/null; then
  info "VS Code 확장 설치 중..."
  VSCODE_EXTS=(
    "bierner.markdown-mermaid"   # Markdown Preview Mermaid Support
  )
  for ext in "${VSCODE_EXTS[@]}"; do
    code --install-extension "$ext" --force &>/dev/null \
      && ok "VS Code 확장: $ext" \
      || warn "VS Code 확장 설치 실패: $ext"
  done
else
  warn "VS Code(code 명령어)를 찾을 수 없습니다. 다이어그램 미리보기를 위해 아래 명령어를 실행하세요:"
  echo -e "  ${CYAN}code --install-extension bierner.markdown-mermaid${NC}"
fi

# Asterisk Docker 빌드 (Docker 있을 때만)
if [[ "$DOCKER_OK" == "true" ]]; then
  # 방금 설치한 경우 docker 그룹이 현재 셸에 아직 적용 안 됨 → sudo 폴백
  DOCKER="docker"
  docker info &>/dev/null || DOCKER="sudo docker"
  info "Asterisk 이미지 빌드 중..."
  $DOCKER build -f Dockerfile.asterisk -t fds-asterisk . -q \
    && ok "Asterisk 이미지 빌드 완료 (fds-asterisk)" \
    || warn "Asterisk 이미지 빌드 실패 — 나중에 수동으로 빌드하세요."
else
  warn "Docker 없음 → Asterisk 자동 발신 기능 비활성화. run.sh로 Flask만 실행 가능."
fi

# ── 완료 메시지 ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  설치 완료!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}실행 방법:${NC}"
echo ""
echo -e "  ${BOLD}# Flask UI만 실행 (포트 충돌 자동 해소)${NC}"
echo -e "  bash run.sh"
echo -e "  → http://localhost:5000"
echo ""
if [[ "$DOCKER_OK" == "true" ]]; then
  echo -e "  ${BOLD}# Asterisk + Flask 전체 실행 (자동 발신 포함)${NC}"
  echo -e "  bash run.sh --with-asterisk"
  echo ""
  echo -e "  ${BOLD}# iPhone Linphone 연결 정보${NC}"
  # 같은 Wi-Fi(LAN)용 IP
  HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "서버IP")
  echo -e "  SIP 서버(같은 Wi-Fi):  ${CYAN}${HOST_IP}${NC}"
  # 외부망(LTE 등)용 Tailscale IP — 설치/로그인 돼 있으면 출력
  if command -v tailscale &>/dev/null; then
    TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
    [[ -n "$TS_IP" ]] && echo -e "  SIP 서버(외부망/LTE):  ${CYAN}${TS_IP}${NC}  ${YELLOW}← 폰에 Tailscale 앱 필요${NC}"
  else
    echo -e "  ${YELLOW}외부망(LTE) 연결은 Tailscale 필요: curl -fsSL https://tailscale.com/install.sh | sh${NC}"
  fi
  echo -e "  사용자:    ${CYAN}iphone${NC}"
  echo -e "  비밀번호:  ${CYAN}fds1234!${NC}"
  echo -e "  포트:      ${CYAN}5060${NC}"
  echo ""
fi

if ! grep -q 'local/bin' "$HOME/.bashrc" 2>/dev/null && \
   ! grep -q 'local/bin' "$HOME/.zshrc" 2>/dev/null; then
  echo -e "  ${YELLOW}※ uv 명령어를 항상 쓰려면 셸을 재시작하거나 아래 실행:${NC}"
  echo -e "  source \$HOME/.local/bin/env"
  echo ""
fi
