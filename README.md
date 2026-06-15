# FDS AI 상담사

부정거래 의심 시 고객에게 자동으로 전화를 걸어 본인확인 및 거래확인을 처리하는 AI 상담 시스템.

**스택**: Whisper (STT) · NVIDIA NIM GPT-OSS-120B (LLM) · Supertonic 3 (TTS) · Asterisk (SIP PBX)

> **WSL 환경 공존**: 같은 머신에 `tts_tuning` (TTS BMT POC) 프로젝트가 있습니다.
> `run.sh` 실행 시 tts_tuning Docker 컨테이너를 **자동으로 먼저 중지**합니다.
> tts_tuning으로 전환하려면 `/home/user/tts_tuning/run.sh up` 을 실행하면 됩니다.

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│  카드사 거래 서버  →  거래 정보 (amount / location / time / ...)  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   FDS 탐지 엔진      │  fds_detector.py
                    │   HIGH → 자동 발신   │  score ≥ 0.60
                    └──────────┬──────────┘
                               │ AMI Originate
                    ┌──────────▼──────────┐
                    │  Asterisk (Docker)  │  SIP :5060
                    │  caller.py → AMI    │  RTP :10000-20000
                    └──────────┬──────────┘
                               │ SIP / Linphone (iPhone)
                    ┌──────────▼──────────┐
                    │   fds_agi.py        │  AGI 브릿지
                    │   고객 음성 녹음     │
                    └──┬────────────────┬─┘
                       │                │
            ┌──────────▼──┐    ┌────────▼────────┐
            │  Whisper STT│    │  Supertonic TTS │
            │  한국어 인식 │    │  F1~F5 / M1~M5  │
            └──────────┬──┘    └────────┬────────┘
                       │                │
                    ┌──▼────────────────▼──┐
                    │  NVIDIA NIM LLM      │  gpt-oss-120b
                    │  PHASE 1 본인확인     │  temperature=0
                    │  PHASE 2 거래확인     │  temperature=0.7
                    └──────────────────────┘
```

---

## 상담 흐름

```
FDS 탐지 (위험도 판정)
    ↓
PHASE 1: 본인확인
    "안녕하세요. 카드사입니다. {고객명} 님이 맞으신가요?"
    ├─ 긍정 → 본인확인 → PHASE 2
    └─ 부정 → 통화실패 → 종료
    ↓
PHASE 2: 거래확인
    "{시간}에 {금액}원을 {거래처}에서 사용하셨나요?"
    ├─ 인정 → 정상거래
    ├─ 부정 → 부정거래확정 → 카드 긴급 중단
    └─ 불확실 → 거래확인불가 → 차단 후 재확인
```

---

## 파일 구조

```
fds-ai-advisor/
├── run.sh                              # 서버 실행 (Flask / Flask+Asterisk)
├── setup.sh                            # 신규 PC 환경 구축 (7단계)
├── pyproject.toml                      # uv 의존성 관리
├── docker-compose.fds.yml              # Asterisk + FDS 컨테이너 구성
├── Dockerfile.asterisk                 # Asterisk 컨테이너
├── Dockerfile.fds                      # FDS AI 앱 컨테이너
├── .env.example                        # 환경 변수 템플릿
├── .env                                # 실제 환경 변수 (gitignore)
├── asterisk/
│   └── etc/asterisk/
│       ├── sip.conf                    # iPhone Linphone 내선 등록
│       ├── extensions.conf             # 다이얼플랜 (AGI 연동)
│       ├── manager.conf                # AMI 계정
│       └── rtp.conf                    # RTP 포트 범위
└── src/
    ├── app.py                          # Flask 테스트 UI + API
    ├── caller.py                       # AMI 자동 발신 트리거
    ├── fds_agi.py                      # AGI 브릿지 (통화 흐름 제어)
    ├── fds_detailed_final.py           # 메인 상담 엔진
    ├── fds_detector.py                 # FDS 위험도 판정
    ├── fraud_suspicion_prompt_detailed.py
    ├── fds_fraud_advisor_prompt.py
    └── templates/
        └── index.html                  # 테스트 UI (다크 테마, 5탭)
```

---

## 설치

### 신규 PC — 자동 설치 (권장)

Ubuntu / Debian / macOS / WSL2 모두 지원.

```bash
git clone https://github.com/techgit01/fds-ai-advisor.git
cd fds-ai-advisor
bash setup.sh
```

`setup.sh` 7단계 자동 처리:

| 단계 | 내용 |
|------|------|
| 1 | FFmpeg, Git 등 시스템 패키지 설치 |
| 2 | Docker 확인 (없으면 설치 안내) |
| 3 | uv 설치 (Python 패키지 매니저) |
| 4 | 저장소 클론 또는 pull |
| 5 | Python 3.12 + 의존성 설치 |
| 6 | `.env` 파일 생성 + API 키 입력 |
| 7 | 동작 확인 + Asterisk 이미지 빌드 |

```bash
bash setup.sh   # 저장소 안에서 실행 시 clone 없이 pull만 수행
```

### 수동 설치

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
uv sync
cp .env.example .env && nano .env
```

---

## 실행

### Flask UI만 (STT / TTS / FDS 탐지 / 시뮬레이션)

```bash
bash run.sh
# → tts_tuning 컨테이너 자동 중지 후 Flask 시작
# → http://localhost:5000

PORT=8080 bash run.sh   # 포트 변경
```

### Flask + Asterisk 전체 (iPhone 자동 발신 포함)

```bash
bash run.sh --with-asterisk
```

실행 시 자동으로:
- tts_tuning Docker 컨테이너 중지
- Asterisk 이미지 빌드 (최초 1회)
- 컨테이너 시작 (SIP :5060 · AMI :5038 · RTP :10000-20000)
- iPhone Linphone 연결 정보 출력

### Docker Compose

```bash
docker compose -f docker-compose.fds.yml up -d
docker compose -f docker-compose.fds.yml down
```

---

## 프로젝트 전환 (fds-ai-advisor ↔ tts_tuning)

두 프로젝트는 메모리 절약을 위해 **한 번에 하나만** 실행합니다.
각 `run.sh`가 상대 프로젝트를 자동 감지하여 중지합니다.

```bash
# fds 작업
cd /home/user/fds-ai-advisor && bash run.sh
# → tts_tuning Docker 컨테이너 자동 중지 후 fds Flask 기동

# TTS BMT 작업으로 전환
cd /home/user/tts_tuning && ./run.sh up
# → fds 컨테이너 + 포트 5000 프로세스 자동 중지 후 TTS 기동
```

---

## 핸드폰 설정 (SIP 소프트폰)

### iPhone (iOS)

1. App Store → **Linphone** 검색 후 설치 (무료)
2. 앱 실행 → **계정 사용** → **SIP 계정 등록**

```
사용자 이름:   iphone
SIP 도메인:    서버 IP  ← setup.sh 완료 후 출력됨
비밀번호:      fds1234!
전송 방식:     UDP
포트:          5060
```

3. 상단에 초록 점(●) 표시되면 등록 완료

> **주의**: iOS 백그라운드 제한으로 앱이 닫혀 있으면 수신이 안 됩니다.

### 삼성 (Android)

1. Play 스토어 → **Linphone** 설치
2. 위와 동일하게 SIP 계정 등록
3. **배터리 최적화 해제**: 설정 → 앱 → Linphone → 배터리 → **제한 없음**

### 집 밖 사용 (Tailscale VPN, 무료)

```bash
# 서버 (WSL2)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip   # → 100.x.x.x
```

iPhone / 삼성에 Tailscale 앱 설치 → 같은 계정 로그인 → Linphone SIP 도메인을 Tailscale IP로 변경.

---

## 자동 발신 테스트

```bash
curl -X POST http://localhost:5000/api/call \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "홍길동",
    "risk_level": "high",
    "transaction": {
      "amount": 2500000,
      "merchant": "Amazon USA",
      "location": "뉴욕, 미국",
      "time": "2026-06-16 03:45"
    }
  }'
```

Linphone이 울리고 → 수신하면 → AI 상담사가 한국어로 상담을 진행합니다.

---

## Flask 테스트 UI

```
http://localhost:5000
```

| 탭 | 기능 |
|----|------|
| **FDS 탐지** | 거래 정보 입력 → 위험도 판정 (API 키 불필요) |
| **시뮬레이션** | PHASE 1/2 전체 상담 SSE 스트리밍 + TTS 음성 재생 |
| **STT** | 마이크 녹음 / 파일 업로드 → Whisper 한국어 인식 |
| **TTS** | 텍스트 + 목소리 선택 → 음성 생성 · 재생 · 다운로드 |
| **로그** | 최근 통화 로그 20건 |

### 전체 API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/detect` | FDS 위험도 판정 |
| POST | `/api/simulate` | 시뮬레이션 (SSE 스트리밍) |
| POST | `/api/stt` | 음성 → 텍스트 (Whisper) |
| POST | `/api/tts` | 텍스트 → 음성 WAV (44kHz, 브라우저용) |
| POST | `/api/agi/tts` | 텍스트 → 음성 WAV (8kHz, Asterisk AGI용) |
| POST | `/api/agi/judge` | LLM 판정 (PHASE 1/2) |
| POST | `/api/call` | iPhone으로 자동 발신 (AMI) |
| GET  | `/api/models/status` | Whisper / TTS 모델 로드 상태 |
| GET  | `/api/logs` | 최근 통화 로그 |

---

## FDS 위험도 판정

| 신호 | 가중치 | 조건 |
|------|--------|------|
| 비정상 거래액 | +0.25 | 평소의 3배 이상 또는 200만원↑ |
| 고위험 지역 | +0.35 | 미국 / 중국 / 러시아 / 필리핀 / 나이지리아 |
| 해외 거래 | +0.25 | is_abroad=True 또는 location에 해외 포함 |
| 새벽 거래 | +0.15 | 00:00 ~ 05:59 |
| 고위험 카테고리 | +0.20 | 도박 / 암호화폐 / 성인용품 |

**위험도**: score ≥ 0.60 → HIGH / ≥ 0.35 → MEDIUM / 미만 → LOW

```python
from fds_detector import detect_fraud_risk

result = detect_fraud_risk({
    "amount": 2500000,
    "location": "뉴욕, 미국",
    "time": "2024-06-15 03:45",
    "merchant": "Amazon USA",
    "is_abroad": True,
    "customer_avg_amount": 500000,
})
# → {"risk_level": "high", "risk_score": 0.75, "reasons": [...]}
```

---

## 환경 변수 (`.env`)

```bash
# 필수
NVIDIA_API_KEY=nvapi-...

# STT
WHISPER_MODEL=large-v3     # base(139MB) / large-v3(권장)
WHISPER_DEVICE=cpu         # cuda (GPU 있을 때)

# LLM
NIM_MODEL=openai/gpt-oss-120b-instruct

# TTS
SUPERTONIC_LANGUAGE=ko
TTS_VOICE=F1               # F1~F5(여성) / M1~M5(남성)

# FDS
BANK_NAME=카드사

# Asterisk AMI
ASTERISK_HOST=127.0.0.1
ASTERISK_AMI_PORT=5038
ASTERISK_AMI_USER=fds
ASTERISK_AMI_SECRET=fdsmanager123
```

---

## 출력 파일

| 경로 | 내용 |
|------|------|
| `logs/call_{id}_{ts}.json` | 통화 기록 (개인정보 익명화) |
| `audio/output/*.wav` | TTS 음성 응답 |
| `audio/agi_tmp/*.wav` | AGI 임시 파일 (통화 후 삭제 가능) |

로그 익명화: 이름 첫 글자만 · 카드번호 `****` · 고객 ID SHA-256 해시

---

## 성능

```
FDS 판정:                < 0.1초
Asterisk 발신 → 수신:   3 ~ 10초
PHASE 1 (TTS+녹음+STT): 5 ~ 8초
PHASE 2 (TTS+녹음+STT): 5 ~ 8초
──────────────────────────────────
총 통화 처리:           15 ~ 30초

메모리: ~1.8GB (Whisper ~1GB + TTS ~800MB)
```

---

## 문제 해결

**포트 이미 사용 중**
```bash
bash run.sh   # 자동으로 기존 프로세스 종료 후 재시작
```

**`.env` 파일 없음**
```bash
bash setup.sh   # 자동 생성 + API 키 입력 안내
# 또는
cp .env.example .env && nano .env
```

**Linphone이 등록 안 됨**
```bash
docker logs fds-asterisk -f
docker exec fds-asterisk asterisk -rx "sip reload"
docker exec fds-asterisk asterisk -rx "sip show peers"
```

**집 밖에서 SIP 연결 안 됨**
```bash
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
# iPhone에도 Tailscale 앱 설치 → 같은 계정 로그인
```

**`ModuleNotFoundError`**
```bash
uv sync
```

**메모리 부족 (WSL2)**
```ini
# ~/.wslconfig
[wsl2]
memory=4GB
```

---

## 주요 설계 결정

- **AGI 브릿지 분리**: Asterisk(경량)는 통화 제어만, AI 처리는 Flask API로 위임 → 컨테이너 경량화
- **8kHz 리샘플링**: Supertonic 44100Hz 출력을 Asterisk G.711(8kHz)에 맞게 scipy로 변환
- **판정/대화 LLM 분리**: 판정 `temperature=0` (일관성), 대화 `temperature=0.7` (자연스러움)
- **프롬프트 인젝션 방지**: 고객 음성에서 `[결과:...]` 태그 및 시스템 지시어 제거
- **비동기 처리**: `AsyncOpenAI` 사용, TTS는 `run_in_executor`로 event loop 보호
- **네트워크 재시도**: `APIConnectionError` / `APITimeoutError` 시 최대 3회 exponential backoff
- **모델 지연 로딩**: Whisper / Supertonic 첫 요청 시 1회만 로드, 이후 캐시 사용
