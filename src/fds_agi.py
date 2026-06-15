#!/usr/bin/env python3
"""
FDS AGI 브릿지 — Asterisk 통화 흐름 제어
Asterisk가 통화 연결 시 이 스크립트를 직접 실행합니다.
통신: stdin/stdout (AGI 프로토콜)
AI 처리: fds-ai-advisor Flask API 호출 (HTTP)
"""

import os
import re
import sys
from pathlib import Path

import requests

FDS_API   = os.getenv("FDS_API_URL", "http://127.0.0.1:5000")
AUDIO_TMP = Path(os.getenv("AUDIO_TMP", "/audio/agi_tmp"))
AUDIO_TMP.mkdir(parents=True, exist_ok=True)

ADVISOR_VOICE = os.getenv("TTS_VOICE", "F1")


# ── AGI 프로토콜 래퍼 ─────────────────────────────────────────────────────────

class AGI:
    def __init__(self):
        self.env = self._read_env()

    def _read_env(self) -> dict:
        env = {}
        while True:
            line = sys.stdin.readline()
            if not line or line.strip() == "":
                break
            if ": " in line:
                k, _, v = line.strip().partition(": ")
                env[k] = v
        return env

    def _cmd(self, cmd: str) -> str:
        sys.stdout.write(cmd + "\n")
        sys.stdout.flush()
        return sys.stdin.readline().strip()

    def verbose(self, msg: str):
        self._cmd(f'VERBOSE "{msg}" 1')
        sys.stderr.write(f"[FDS-AGI] {msg}\n")

    def answer(self):
        return self._cmd("ANSWER")

    def hangup(self):
        return self._cmd("HANGUP")

    def wait(self, sec: int):
        return self._cmd(f"EXEC Wait {sec}")

    def stream_file(self, path: str, escape: str = "#") -> str:
        """음성 파일 재생 (경로는 확장자 없이)"""
        return self._cmd(f'STREAM FILE "{path}" "{escape}"')

    def record_file(self, path: str, fmt: str = "wav",
                    timeout: int = 8000, silence: int = 3) -> str:
        """음성 녹음 — silence 초 침묵 또는 timeout ms 후 종료"""
        return self._cmd(
            f'RECORD FILE "{path}" {fmt} "#" {timeout} s={silence}'
        )

    def get_var(self, name: str) -> str:
        res = self._cmd(f"GET VARIABLE {name}")
        m = re.search(r"\((.+?)\)", res)
        return m.group(1) if m else ""

    def set_var(self, name: str, value: str):
        self._cmd(f'SET VARIABLE {name} "{value}"')


# ── AI 처리 (HTTP → fds-ai-advisor) ──────────────────────────────────────────

def tts(text: str, out_path: Path, voice: str = ADVISOR_VOICE) -> bool:
    """텍스트 → Asterisk 호환 8kHz WAV"""
    try:
        r = requests.post(
            f"{FDS_API}/api/agi/tts",
            json={"text": text, "voice": voice},
            timeout=30,
        )
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return True
    except Exception as e:
        sys.stderr.write(f"[TTS 오류] {e}\n")
        return False


def stt(wav_path: Path) -> str:
    """WAV → 텍스트 (Whisper)"""
    try:
        with open(wav_path, "rb") as f:
            r = requests.post(
                f"{FDS_API}/api/stt",
                files={"audio": ("audio.wav", f)},
                timeout=60,
            )
        r.raise_for_status()
        return r.json().get("transcript", "").strip()
    except Exception as e:
        sys.stderr.write(f"[STT 오류] {e}\n")
        return ""


def judge(phase: int, transcript: str, ctx: dict = None) -> str:
    """LLM 판정 → verdict 문자열"""
    try:
        r = requests.post(
            f"{FDS_API}/api/agi/judge",
            json={"phase": phase, "transcript": transcript, "context": ctx or {}},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("verdict", "unknown")
    except Exception as e:
        sys.stderr.write(f"[Judge 오류] {e}\n")
        return "unknown"


def sanitize(text: str) -> str:
    text = re.sub(r"\[결과:[^\]]+\]", "", text)
    return text[:500].strip()


# ── 메인 통화 흐름 ────────────────────────────────────────────────────────────

def main():
    agi = AGI()
    call_id   = agi.env.get("agi_uniqueid", "unknown").replace(".", "_")
    bank_name = os.getenv("BANK_NAME", "카드사")

    agi.verbose(f"통화 시작 — ID: {call_id}")
    agi.answer()
    agi.wait(1)

    # caller.py 가 SetVar 로 전달한 거래 정보
    customer = agi.get_var("FDS_CUSTOMER_NAME") or "고객"
    amount   = agi.get_var("FDS_AMOUNT")   or "알 수 없음"
    merchant = agi.get_var("FDS_MERCHANT") or "알 수 없음"
    tx_time  = agi.get_var("FDS_TIME")     or "알 수 없음"
    location = agi.get_var("FDS_LOCATION") or "알 수 없음"
    risk     = agi.get_var("FDS_RISK_LEVEL") or "high"

    # ── PHASE 1: 본인확인 ────────────────────────────────────────────────────
    agi.verbose("PHASE 1: 본인확인")

    p1_txt = (
        f"안녕하세요, {customer} 고객님. {bank_name} FDS 담당자입니다. "
        f"고객님 명의 카드에서 의심 거래가 감지되어 연락드렸습니다. "
        f"본인이 맞으시면 네, 아니시면 아니오라고 말씀해 주세요."
    )
    p1_wav = AUDIO_TMP / f"p1_greeting_{call_id}"
    if tts(p1_txt, Path(str(p1_wav) + ".wav")):
        agi.stream_file(str(p1_wav))

    p1_rec = AUDIO_TMP / f"p1_rec_{call_id}"
    agi.record_file(str(p1_rec), timeout=8000, silence=3)

    p1_text = sanitize(stt(Path(str(p1_rec) + ".wav")))
    agi.verbose(f"PHASE 1 응답: {p1_text}")

    p1_verdict = judge(1, p1_text)
    agi.verbose(f"PHASE 1 판정: {p1_verdict}")

    if p1_verdict != "confirmed":
        fw_txt = f"실례했습니다. {bank_name}였습니다. 감사합니다."
        fw_wav = AUDIO_TMP / f"farewell_{call_id}"
        if tts(fw_txt, Path(str(fw_wav) + ".wav")):
            agi.stream_file(str(fw_wav))
        agi.set_var("FDS_RESULT", "통화실패")
        agi.verbose("결과: 통화실패")
        agi.hangup()
        return

    # ── PHASE 2: 거래확인 ────────────────────────────────────────────────────
    agi.verbose("PHASE 2: 거래확인")

    try:
        amount_fmt = f"{int(amount):,}원"
    except Exception:
        amount_fmt = f"{amount}원"

    p2_txt = (
        f"감사합니다. {tx_time}에 {location}의 {merchant}에서 "
        f"{amount_fmt} 결제가 시도되었습니다. "
        f"본인 거래가 맞으시면 네, 아니시면 아니오라고 말씀해 주세요."
    )
    p2_wav = AUDIO_TMP / f"p2_question_{call_id}"
    if tts(p2_txt, Path(str(p2_wav) + ".wav")):
        agi.stream_file(str(p2_wav))

    p2_rec = AUDIO_TMP / f"p2_rec_{call_id}"
    agi.record_file(str(p2_rec), timeout=8000, silence=3)

    p2_text = sanitize(stt(Path(str(p2_rec) + ".wav")))
    agi.verbose(f"PHASE 2 응답: {p2_text}")

    ctx = {
        "customer_name": customer, "amount": amount,
        "merchant": merchant,      "time": tx_time,
        "location": location,      "risk_level": risk,
    }
    p2_verdict = judge(2, p2_text, ctx)
    agi.verbose(f"PHASE 2 판정: {p2_verdict}")

    if p2_verdict == "legitimate":
        reply  = "감사합니다. 정상 거래로 확인되었습니다. 불편을 드려 죄송합니다."
        result = "정상거래"
    elif p2_verdict == "fraud":
        reply  = (
            "부정 거래로 확인됩니다. 즉시 카드를 긴급 중단하고 "
            "새 카드를 발급해드리겠습니다. 잠시만 기다려 주세요."
        )
        result = "부정거래확정"
    else:
        reply  = "안전을 위해 거래를 일시 차단하겠습니다. 추가 확인이 필요한 경우 재연락드리겠습니다."
        result = "거래확인불가"

    rp_wav = AUDIO_TMP / f"reply_{call_id}"
    if tts(reply, Path(str(rp_wav) + ".wav")):
        agi.stream_file(str(rp_wav))

    agi.set_var("FDS_RESULT", result)
    agi.verbose(f"최종 결과: {result}")
    agi.hangup()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"[AGI 치명적 오류] {e}\n")
        sys.exit(1)
