#!/usr/bin/env bash
# FDS AI 상담사 — 실행 스크립트
#
# 사용법:
#   bash run.sh                  # Flask UI만 실행
#   bash run.sh --with-asterisk  # Flask + Asterisk 전체 실행
#   PORT=8080 bash run.sh        # 포트 변경

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}!${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*" >&2; }

PORT=${PORT:-5000}
WITH_ASTERISK=false
TTS_COMPOSE="/home/user/tts_tuning/docker-compose.yml"
TTS_DIR="/home/user/tts_tuning"

for arg in "$@"; do
  case "$arg" in
    --with-asterisk) WITH_ASTERISK=true ;;
    --help|-h)
      echo "사용법: bash run.sh [--with-asterisk] [PORT=포트번호]"
      echo ""
      echo "  --with-asterisk   Asterisk(SIP) 컨테이너도 함께 실행"
      echo "  PORT=8080         Flask 포트 변경 (기본: 5000)"
      exit 0
      ;;
  esac
done

# ── 저장소 루트 확인 ──────────────────────────────────────────────────────────
if [[ ! -f "pyproject.toml" ]]; then
  err "fds-ai-advisor 루트 디렉토리에서 실행하세요."
  exit 1
fi

# ── 상대 프로젝트(tts_tuning) 중지 ───────────────────────────────────────────
_tts_containers=$(docker ps --format '{{.Names}}' 2>/dev/null \
  | grep -E "^tts_" || true)
if [ -n "$_tts_containers" ]; then
  info "tts_tuning 컨테이너 중지 중..."
  if [ -f "$TTS_COMPOSE" ]; then
    docker compose -f "$TTS_COMPOSE" --project-directory "$TTS_DIR" down 2>/dev/null || true
  else
    echo "$_tts_containers" | xargs -r docker stop 2>/dev/null || true
  fi
  ok "tts_tuning 중지 완료"
fi

# ── Asterisk 실행 (--with-asterisk 옵션) ─────────────────────────────────────
if [[ "$WITH_ASTERISK" == "true" ]]; then
  if ! command -v docker &>/dev/null; then
    err "Docker가 설치되어 있지 않습니다. setup.sh를 먼저 실행하세요."
    exit 1
  fi

  info "Asterisk 컨테이너 시작 중..."

  # 이미지가 없으면 빌드
  if ! docker image inspect fds-asterisk &>/dev/null; then
    info "Asterisk 이미지 빌드 중 (최초 1회)..."
    docker build -f Dockerfile.asterisk -t fds-asterisk . -q
    ok "이미지 빌드 완료"
  fi

  # 기존 컨테이너 정리
  docker rm -f fds-asterisk 2>/dev/null && info "기존 Asterisk 컨테이너 제거" || true

  # AGI 스크립트 실행 권한 부여
  chmod +x src/fds_agi.py

  # 커스텀 설정 파일만 개별 overlay 마운트.
  # (디렉토리 전체를 마운트하면 이미지의 기본 설정 113개가 사라져
  #  stasis.conf 등 누락 → "Stasis initialization failed"로 Asterisk가 죽음)
  CONF_MOUNTS=()
  for f in "$(pwd)/asterisk/etc/asterisk/"*.conf; do
    CONF_MOUNTS+=(-v "${f}:/etc/asterisk/$(basename "$f"):ro")
  done

  # 컨테이너 실행
  docker run -d \
    --name fds-asterisk \
    --network host \
    "${CONF_MOUNTS[@]}" \
    -v "$(pwd)/src/fds_agi.py:/usr/share/asterisk/agi-bin/fds_agi.py" \
    -v "$(pwd)/audio:/audio" \
    -e "FDS_API_URL=http://127.0.0.1:${PORT}" \
    -e "AUDIO_TMP=/audio/agi_tmp" \
    -e "BANK_NAME=${BANK_NAME:-카드사}" \
    -e "TTS_VOICE=${TTS_VOICE:-F1}" \
    fds-asterisk

  ok "Asterisk 시작 완료 (SIP :5060, AMI :5038, RTP :10000-20000)"

  # iPhone Linphone 연결 정보 출력
  HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "서버IP")
  echo ""
  echo -e "  ${BOLD}iPhone Linphone 설정${NC}"
  echo -e "  ────────────────────────────────"
  echo -e "  SIP 서버(같은 Wi-Fi):  ${CYAN}${HOST_IP}${NC}"
  # 외부망(LTE 등)은 Tailscale IP로 연결 — 폰에도 Tailscale 앱 필요
  if command -v tailscale &>/dev/null; then
    TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
    if [[ -n "$TS_IP" ]]; then
      echo -e "  SIP 서버(외부망/LTE):  ${CYAN}${TS_IP}${NC}  ${YELLOW}← 폰에 Tailscale 앱 ON${NC}"
    else
      echo -e "  ${YELLOW}외부망 연결: 'sudo tailscale up'으로 로그인 후 표시됩니다.${NC}"
    fi
  else
    echo -e "  ${YELLOW}외부망(LTE) 연결은 Tailscale 필요 → README의 'Tailscale' 참고${NC}"
  fi
  echo -e "  사용자:    ${CYAN}iphone${NC}"
  echo -e "  비밀번호:  ${CYAN}fds1234!${NC}"
  echo -e "  포트:      ${CYAN}5060 (UDP)${NC}"
  echo ""
fi

# ── 포트 충돌 해소 ───────────────────────────────────────────────────────────
if lsof -ti:"$PORT" &>/dev/null; then
  info "포트 $PORT 사용 중 → 기존 프로세스 종료"
  lsof -ti:"$PORT" | xargs kill -9
  sleep 0.5
fi

# ── 필요 디렉토리 생성 ───────────────────────────────────────────────────────
mkdir -p audio/agi_tmp audio/input audio/output logs

# ── Python 실행 파일 탐색 ────────────────────────────────────────────────────
if [[ -f ".venv/bin/python3" ]]; then
  PYTHON=".venv/bin/python3"
elif command -v uv &>/dev/null; then
  PYTHON="uv run python"
else
  err ".venv 또는 uv를 찾을 수 없습니다. setup.sh를 먼저 실행하세요."
  exit 1
fi

# ── Flask 실행 ───────────────────────────────────────────────────────────────
echo ""
ok "Flask 테스트 UI 시작"
echo -e "  → ${CYAN}http://localhost:${PORT}${NC}"
if [[ "$WITH_ASTERISK" == "true" ]]; then
  echo -e "  → Asterisk 발신: ${CYAN}POST /api/call${NC}"
fi
echo ""

export PORT
exec $PYTHON src/app.py
