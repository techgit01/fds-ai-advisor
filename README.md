# FDS AI 상담사

부정거래 의심 시 고객에게 자동으로 전화를 걸어 본인확인 및 거래확인을 처리하는 AI 상담 시스템.

**스택**: Whisper (STT) + NVIDIA NIM GPT-OSS-120B (LLM) + Supertonic 3 (TTS)

---

## 상담 흐름

```
FDS 탐지 (위험도 판정)
    ↓
PHASE 1: 본인확인
    "안녕하세요. 카드사입니다. {고객명} 님이 맞으신가요?"
    ├─ 긍정 응답 → [결과:본인확인] → PHASE 2
    └─ 부정/불명확 → [결과:통화실패] → 종료
    ↓
PHASE 2: 거래확인
    "{시간}에 {금액}원을 {거래처}에서 사용하셨나요?"
    ├─ 인정 → [결과:정상거래]
    ├─ 부정 → [결과:부정거래확정] → 카드 긴급 중단
    └─ 불확실 → [결과:거래확인불가] → 거래 차단 후 재확인 예약
```

---

## 파일 구조

```
fds-ai-advisor/
├── README.md
├── run.sh                             # 서버 실행 (포트 충돌 자동 해소)
├── setup.sh                           # 신규 PC 환경 구축
├── pyproject.toml                     # uv 의존성 관리
├── .env.example                       # 환경 변수 템플릿
├── .env                               # 실제 환경 변수 (gitignore)
├── docker-compose.fds.yml
├── Dockerfile.fds
└── src/
    ├── app.py                         # Flask 테스트 UI 서버
    ├── fds_detailed_final.py          # 메인 실행 파일
    ├── fds_detector.py                # FDS 위험도 판정
    ├── fraud_suspicion_prompt_detailed.py  # PHASE 1/2 프롬프트
    ├── fds_fraud_advisor_prompt.py    # 기본 프롬프트
    ├── templates/
    │   └── index.html                 # 테스트 UI (다크 테마)
    └── requirements_supertonic.txt
```

---

## 설치 및 실행

### 신규 PC — 자동 설치 (권장)

Ubuntu / Debian / macOS / WSL2 모두 지원.

```bash
git clone https://github.com/techgit01/fds-ai-advisor.git
cd fds-ai-advisor
bash setup.sh
```

`setup.sh`가 자동으로 처리합니다:
1. FFmpeg, Git 등 시스템 패키지 설치
2. uv 설치 (Python 패키지 매니저)
3. Python 3.12 + 의존성 설치 (`.venv` 생성)
4. `.env` 파일 생성 + NVIDIA API 키 입력 안내
5. FDS 탐지 동작 확인 (smoke test)

이미 저장소 안에 있어도 실행 가능합니다:

```bash
bash setup.sh   # 저장소 내부에서 실행해도 정상 동작
```

### 수동 설치

```bash
# uv 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# 의존성 설치 (Python 3.12 자동 다운로드)
uv sync

# 환경 변수 설정
cp .env.example .env
nano .env  # NVIDIA_API_KEY 입력
```

### Docker 실행

```bash
cp .env.example .env
nano .env  # NVIDIA_API_KEY 입력

docker-compose -f docker-compose.fds.yml up
docker-compose -f docker-compose.fds.yml up -d   # 백그라운드
docker-compose -f docker-compose.fds.yml logs -f # 로그
```

---

## Flask 테스트 UI

브라우저에서 모든 기능을 직접 테스트할 수 있는 다크 테마 웹 UI.

### 실행

```bash
# 포트 충돌 자동 해소 후 실행
bash run.sh

# 포트 변경
PORT=8080 bash run.sh
```

→ http://localhost:5000

### 탭 구성

| 탭 | 기능 |
|----|------|
| **FDS 탐지** | 거래 정보 입력 → 위험도 판정 (API 키 불필요) |
| **시뮬레이션** | PHASE 1/2 전체 상담 흐름 SSE 스트리밍 + TTS 음성 재생 |
| **STT** | 마이크 녹음 또는 파일 업로드 → Whisper 한국어 인식 |
| **TTS** | 텍스트 입력 + 목소리 선택 → 음성 생성 · 재생 · 다운로드 |
| **로그** | 최근 통화 로그 20건 조회 |

### API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/detect` | FDS 위험도 판정 |
| POST | `/api/simulate` | 전체 시뮬레이션 (SSE 스트리밍) |
| POST | `/api/stt` | 음성 → 텍스트 (Whisper) |
| POST | `/api/tts` | 텍스트 → 음성 WAV (Supertonic) |
| GET  | `/api/models/status` | Whisper/TTS 모델 로드 상태 |
| GET  | `/api/logs` | 최근 통화 로그 |

#### STT 예시

```bash
curl -X POST http://localhost:5000/api/stt \
  -F "audio=@recording.wav"
# → {"transcript": "네, 맞습니다.", "segments": [...], "language": "ko"}
```

#### TTS 예시

```bash
curl -X POST http://localhost:5000/api/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "안녕하세요.", "voice": "F1", "speed": 1.0}' \
  -o output.wav
```

**TTS 목소리**: F1~F5 (여성), M1~M5 (남성) / **속도**: 0.5x ~ 2.0x

---

## FDS 위험도 판정 (`fds_detector.py`)

| 신호 | 가중치 | 조건 |
|------|--------|------|
| 비정상 거래액 | +0.25 | 평소의 3배 이상 또는 200만원↑ |
| 고위험 지역 | +0.35 | 미국/중국/러시아/필리핀/나이지리아 |
| 해외 거래 | +0.25 | is_abroad=True 또는 location에 "해외" |
| 새벽 거래 | +0.15 | 00:00 ~ 05:59 |
| 고위험 카테고리 | +0.20 | 도박/암호화폐/성인용품 등 |

**위험도**: score ≥ 0.60 → HIGH / ≥ 0.35 → MEDIUM / 미만 → LOW

```python
from fds_detector import detect_fraud_risk

result = detect_fraud_risk({
    "amount": 2500000,
    "location": "뉴욕, 미국",
    "time": "2024-06-15 03:45",
    "merchant": "Amazon USA",
    "type": "해외결제",
    "is_abroad": True,
    "customer_avg_amount": 500000,
})
# → {"risk_level": "high", "risk_score": 0.75, "reasons": [...], ...}
```

---

## 메인 API (`fds_detailed_final.py`)

```python
import asyncio
from fds_detailed_final import FDSAIAdvisorDetailed

async def main():
    advisor = FDSAIAdvisorDetailed(
        nim_api_key="nvapi-...",
        whisper_device="cpu",   # GPU 있으면 "cuda"
    )

    result = await advisor.handle_fraud_suspicion(
        customer_name="김철수",
        customer_id="CUST_001",
        card_last4="1234",
        transaction={
            "amount": 150000,
            "merchant": "Amazon USA",
            "location": "뉴욕, 미국",
            "time": "2024-06-15 03:45",
            "type": "해외결제",
            "is_abroad": True,
            "customer_avg_amount": 500000,
        },
        bank_name="카드사",
        audio_input_path=None,  # None → 콘솔 입력, 경로 지정 → Whisper STT
    )
    # result["result"]: "통화실패" | "정상거래" | "부정거래확정" | "거래확인불가"

asyncio.run(main())
```

음성 파일 사용 시 `audio_input_path`는 `audio/input/` 디렉토리 내 경로만 허용 (경로 순회 방지).

---

## 출력 파일

| 경로 | 내용 |
|------|------|
| `logs/call_{id}_{timestamp}.json` | 통화 기록 (개인정보 익명화됨) |
| `audio/output/response_{id}_{timestamp}.wav` | TTS 음성 응답 |

로그 익명화: 이름 첫 글자만 · 카드번호 `****` · 고객ID SHA-256 해시

---

## 환경 변수 (`.env`)

```bash
# 필수
NVIDIA_API_KEY=nvapi-...

# 선택 (기본값 사용 가능)
WHISPER_MODEL=large-v3   # base(139MB) / large-v3(권장, 고정밀)
WHISPER_DEVICE=cpu       # cuda (GPU 있을 때)
NIM_MODEL=openai/gpt-oss-120b-instruct
SUPERTONIC_LANGUAGE=ko
BANK_NAME=카드사
LOG_LEVEL=INFO
```

---

## 모델 선택

`NIM_MODEL` 환경변수로 LLM 변경:

| 모델 | 특징 |
|------|------|
| `openai/gpt-oss-120b-instruct` | 기본, 빠름 (1.5~2초) |
| `nvidia/llama-3.1-nemotron-super-128b-instruct` | 고성능 |
| `mistralai/mistral-nemotron-super-128b-instruct` | 초고속 |

---

## 성능

```
STT (Whisper large-v3):  1~2초
FDS 판정:                <100ms
LLM (NIM):               1.5~2초 × 2 calls
TTS (Supertonic 3):      0.1~0.2초
─────────────────────────────────
총 통화 처리:            5초 이내

메모리: ~1.8GB (Whisper ~1GB + 나머지 ~800MB)
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

**`ModuleNotFoundError`**
```bash
uv sync   # 또는
.venv/bin/pip install -r src/requirements_supertonic.txt
```

**`NVIDIA_API_KEY not found`**
```bash
grep NVIDIA_API_KEY .env
```

**Whisper 모델 다운로드 실패**
```bash
rm -rf cache/whisper
```

**메모리 부족 (WSL2)**
```ini
# ~/.wslconfig
[wsl2]
memory=4GB
```

---

## 주요 설계 결정

- **판정/대화 LLM 분리**: 판정 호출은 `temperature=0` (일관성), 대화 생성은 `temperature=0.7` (자연스러움)
- **프롬프트 인젝션 방지**: 고객 음성에서 `[결과:...]` 태그와 시스템 지시어 제거 후 LLM 전달
- **비동기 처리**: `openai.AsyncOpenAI` 사용, TTS는 `run_in_executor`로 event loop 보호
- **네트워크 재시도**: `APIConnectionError` / `APITimeoutError` 시 최대 3회 exponential backoff
- **모델 지연 로딩**: Whisper / Supertonic은 첫 요청 시 1회만 로드, 이후 캐시 사용
