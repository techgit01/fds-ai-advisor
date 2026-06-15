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
├── docker-compose.fds.yml
├── Dockerfile.fds
├── .env.example
└── src/
    ├── fds_detailed_final.py          # 메인 실행 파일
    ├── fds_detector.py                # FDS 위험도 판정
    ├── fraud_suspicion_prompt_detailed.py  # PHASE 1/2 프롬프트
    ├── fds_fraud_advisor_prompt.py    # 기본 프롬프트
    └── requirements_supertonic.txt
```

---

## 설치 및 실행

### 사전 요구사항

- NVIDIA_API_KEY ([build.nvidia.com](https://build.nvidia.com) → API Keys → Generate, 무료)
- FFmpeg (`sudo apt install ffmpeg`)
- uv — Python 3.12와 가상환경을 자동으로 설치합니다

### uv 자동 설치 (권장)

**Python 3.12와 모든 의존성을 자동으로 설치합니다.**

```bash
# uv 설치 (없는 경우)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # 또는 새 터미널 열기

# 프로젝트 클론 후
cd ~/fds-ai-advisor

# Python 3.12 + 의존성 자동 설치 (한 번에)
uv sync

# 환경 변수 설정
cp .env.example .env
nano .env  # NVIDIA_API_KEY 입력

# 실행
uv run python src/fds_detailed_final.py
```

`uv sync`는 Python 3.12가 없으면 자동으로 다운로드하고 `.venv`를 생성합니다.

### Docker 실행

```bash
# 환경 변수 설정
cp .env.example .env
nano .env  # NVIDIA_API_KEY 입력

# 빌드 및 실행
docker-compose -f docker-compose.fds.yml up

# 백그라운드
docker-compose -f docker-compose.fds.yml up -d

# 로그
docker-compose -f docker-compose.fds.yml logs -f
```

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
    "customer_avg_amount": 500000,  # 평소 거래액
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
        audio_input_path=None,  # None → 콘솔 입력(테스트), 경로 지정 → Whisper STT
    )
    # result["result"]: "통화실패" | "정상거래" | "부정거래확정" | "거래확인불가"

asyncio.run(main())
```

**음성 파일 사용 시**: `audio_input_path`는 `audio/input/` 디렉토리 내부 경로만 허용 (경로 순회 방지).

---

## 출력 파일

| 경로 | 내용 |
|------|------|
| `logs/call_{id}_{timestamp}.json` | 통화 기록 (개인정보 익명화됨) |
| `audio/output/response_{id}_{timestamp}.wav` | TTS 음성 응답 |

로그의 개인정보는 자동 익명화됩니다: 이름 첫 글자만, 카드번호 `****`, 고객ID SHA-256 해시.

---

## 환경 변수 (`.env`)

```bash
# 필수
NVIDIA_API_KEY=nvapi-...

# 선택 (기본값 사용 가능)
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cpu
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
─────────────────────────────
총 통화 처리:            5초 이내

메모리: ~1.8GB (Whisper ~1GB + 나머지 ~800MB)
```

---

## 문제 해결

**`ModuleNotFoundError: No module named 'openai'`**
```bash
# 가상환경 활성화 확인
which python  # → .venv/bin/python 이어야 함
uv sync
```

**`NVIDIA_API_KEY not found`**
```bash
cat .env | grep NVIDIA_API_KEY
```

**Whisper 모델 다운로드 실패**
```bash
rm -rf cache/whisper
docker-compose -f docker-compose.fds.yml restart
```

**메모리 부족**
- Docker Desktop → Resources → Memory: 4GB 이상으로 설정
- `.wslconfig`에 `memory=4GB` 추가

**포트 충돌**
```yaml
# docker-compose.fds.yml
ports:
  - "8001:8000"  # 8000 → 8001로 변경
```

---

## 주요 설계 결정

- **판정/대화 LLM 분리**: 판정 호출은 `temperature=0` (일관성), 대화 생성은 `temperature=0.7` (자연스러움)
- **프롬프트 인젝션 방지**: 고객 음성에서 `[결과:...]` 태그와 시스템 지시어 제거 후 LLM 전달
- **비동기 처리**: `openai.AsyncOpenAI` 사용, TTS는 `run_in_executor`로 event loop 보호
- **네트워크 재시도**: `APIConnectionError`/`APITimeoutError` 시 최대 3회 exponential backoff
