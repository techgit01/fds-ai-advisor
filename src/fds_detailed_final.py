#!/usr/bin/env python3
"""
FDS AI 상담사 - 상세 프롬프트 통합 최종 버전 (버그 수정)

수정 사항:
- AsyncOpenAI 사용으로 event loop 블로킹 해소
- 판정/대화 LLM 호출 분리 (temperature 최적화)
- 프롬프트 인젝션 방지 입력 sanitize
- [결과:...] 파싱 로직 강화 (부정 컨텍스트 처리)
- 로그 개인정보 익명화
- LLM 재시도 로직 추가 (3회 exponential backoff)
- 실제 Whisper STT 연동 (하드코딩 제거)
- 음성 파일 경로 순회 공격 방지
- TTS sample_rate 동적 사용 (44100 하드코딩 제거)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
import openai
import scipy.io.wavfile as wavfile
import whisper
from supertonic import TTS as SupertonicTTS

from fraud_suspicion_prompt_detailed import FraudSuspicionPrompt
from fds_detector import detect_fraud_risk

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 판정 전용 간결 프롬프트 — 긴 프롬프트는 LLM attention을 희석시켜 형식 준수율이 낮아짐
_JUDGE_IDENTITY_PROMPT = """고객 응답: "{response}"

판정 (태그만 출력):
- 본인 확인됨: [결과:본인확인]
- 본인 아님/불명확/미응답: [결과:통화실패]"""

_JUDGE_TRANSACTION_PROMPT = """고객 응답: "{response}"

판정 (태그만 출력):
- 거래 인정: [결과:정상거래]
- 거래 부정: [결과:부정거래확정]
- 불확실/모름: [결과:거래확인불가]"""

# 허용된 음성 입력 디렉토리 (경로 순회 공격 방지)
_ALLOWED_AUDIO_DIR = Path("audio/input").resolve()


class FDSAIAdvisorDetailed:
    LLM_MODEL = "openai/gpt-oss-120b-instruct"

    def __init__(self, nim_api_key: str, whisper_device: str = "cpu"):
        logger.info("=" * 70)
        logger.info("FDS AI 상담사 (상세 프롬프트 버전)")
        logger.info("=" * 70)

        # AsyncOpenAI — async 함수에서 동기 클라이언트를 쓰면 event loop 블로킹 발생
        self.llm_client = openai.AsyncOpenAI(
            api_key=nim_api_key,
            base_url="https://integrate.api.nvidia.com/v1",
        )

        logger.info("[1/2] Whisper large-v3 모델 로드...")
        self.whisper_model = whisper.load_model("large-v3", device=whisper_device)
        logger.info("✓ Whisper 로드 완료")

        logger.info("[2/2] Supertonic 3 TTS 초기화...")
        try:
            self.supertonic_tts = SupertonicTTS(auto_download=True)
            logger.info("✓ Supertonic 3 로드 완료")
        except Exception as e:
            logger.error(f"Supertonic 로드 실패: {e}")
            self.supertonic_tts = None

        logger.info("=" * 70)
        logger.info("모든 컴포넌트 준비 완료\n")

    async def handle_fraud_suspicion(
        self,
        customer_name: str,
        customer_id: str,
        card_last4: str,
        transaction: Dict,
        bank_name: str = "카드사",
        audio_input_path: Optional[str] = None,
    ) -> Dict:
        """부정거래 의심 전화 처리

        Args:
            audio_input_path: 고객 음성 파일 경로. None이면 콘솔 입력(테스트 모드).
        """
        print(f"\n{'█' * 70}")
        print(f"█ [부정거래 의심 전화 상담]")
        print(f"█ 고객: {customer_name} | 카드: {card_last4}")
        print(f"█ 거래: {transaction['amount']:,}원 @ {transaction['merchant']}")
        print(f"{'█' * 70}\n")

        result = {
            "customer_name": customer_name,
            "customer_id": customer_id,
            "card_last4": card_last4,
            "transaction": transaction,
            "bank_name": bank_name,
            "status": "processing",
            "result": None,
            "phase1_result": None,
            "phase2_result": None,
            "conversation": [],
            "fds_analysis": None,
            "timestamp": datetime.now().isoformat(),
            "model": "GPT-OSS-120B",
        }

        try:
            fds_result = detect_fraud_risk({
                "amount": transaction.get("amount"),
                "location": transaction.get("location"),
                "time": transaction.get("time"),
                "merchant": transaction.get("merchant"),
                "type": transaction.get("type", "국내결제"),
                "is_abroad": transaction.get("is_abroad", False),
                "customer_avg_amount": transaction.get("customer_avg_amount", 500000),
            })
            result["fds_analysis"] = fds_result
            print(f"🔍 FDS 분석 결과: {fds_result['risk_level'].upper()}")
            if fds_result["reasons"]:
                print(f"   신호: {', '.join(fds_result['reasons'])}\n")

            phase1_result = await self._phase1_identity_verification(
                result, customer_name, bank_name, audio_input_path
            )
            result["phase1_result"] = phase1_result

            if phase1_result != "본인확인":
                result["status"] = "success"
                result["result"] = "통화실패"
                self._print_call_summary(result)
                self._save_log(result)
                return result

            phase2_result = await self._phase2_transaction_verification(
                result, customer_name, transaction, fds_result, bank_name, audio_input_path
            )
            result["phase2_result"] = phase2_result
            result["status"] = "success"
            result["result"] = phase2_result if phase2_result in ("정상거래", "부정거래확정") else "거래확인불가"

            final_msg = self._get_final_message(phase2_result)
            audio_path = await self._synthesize_speech(final_msg, customer_id)
            if audio_path:
                result["audio_output"] = audio_path

            self._save_log(result)
            self._print_call_summary(result)
            return result

        except Exception as e:
            logger.error(f"오류: {e}")
            result["status"] = "error"
            result["result"] = str(e)
            self._save_log(result)
            return result

    async def _get_customer_voice(self, audio_path: Optional[str]) -> str:
        """음성 파일 → 텍스트 변환. audio_path가 없으면 테스트용 콘솔 입력."""
        if audio_path:
            safe_path = Path(audio_path).resolve()
            if not str(safe_path).startswith(str(_ALLOWED_AUDIO_DIR)):
                raise ValueError(f"허용되지 않은 음성 파일 경로: {audio_path}")
            result = self.whisper_model.transcribe(str(safe_path), language="ko")
            return result["text"].strip()
        return input("[테스트] 고객 응답 입력: ")

    @staticmethod
    def _sanitize_customer_input(text: str) -> str:
        """프롬프트 인젝션 방지 — 고객이 결과 태그나 시스템 지시어를 발화하는 경우 제거"""
        text = re.sub(r"\[결과:[^\]]+\]", "", text)
        text = re.sub(
            r"(ignore|system|override|forget|disregard).{0,30}(previous|instruction|prompt)",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text[:500]

    async def _phase1_identity_verification(
        self,
        result: Dict,
        customer_name: str,
        bank_name: str,
        audio_path: Optional[str],
    ) -> str:
        greeting_prompt = FraudSuspicionPrompt.generate_phase1_prompt(
            customer_name=customer_name, bank_name=bank_name
        )
        advisor_msg = await self._call_llm_dialogue(greeting_prompt)
        result["conversation"].append({
            "phase": 1, "role": "상담사", "message": advisor_msg,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"📱 상담사: {advisor_msg}")

        customer_raw = await self._get_customer_voice(audio_path)
        customer_msg = self._sanitize_customer_input(customer_raw)
        result["conversation"].append({
            "phase": 1, "role": "고객", "message": customer_msg,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"📞 고객: {customer_msg}\n")

        verdict = await self._judge_identity(customer_msg)
        result["conversation"].append({
            "phase": 1, "role": "시스템", "message": verdict,
            "timestamp": datetime.now().isoformat(),
        })

        if verdict == "본인확인":
            print("✓ [결과:본인확인]\n")
            return "본인확인"

        farewell = f"[결과:통화실패] 실례했습니다. {bank_name}였습니다. 감사합니다."
        result["conversation"].append({
            "phase": 1, "role": "상담사", "message": farewell,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"📱 상담사: {farewell}\n")
        print("✗ [결과:통화실패]\n")
        return "통화실패"

    async def _phase2_transaction_verification(
        self,
        result: Dict,
        customer_name: str,
        transaction: Dict,
        fds_result: Dict,
        bank_name: str,
        audio_path: Optional[str],
    ) -> str:
        question_prompt = FraudSuspicionPrompt.generate_phase2_prompt(
            customer_name=customer_name,
            transaction_amount=transaction.get("amount"),
            merchant_name=transaction.get("merchant"),
            transaction_time=transaction.get("time"),
            transaction_location=transaction.get("location"),
            fraud_risk_level=fds_result["risk_level"],
            bank_name=bank_name,
        )
        advisor_msg = await self._call_llm_dialogue(question_prompt)
        result["conversation"].append({
            "phase": 2, "role": "상담사", "message": advisor_msg,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"📱 상담사: {advisor_msg}")

        customer_raw = await self._get_customer_voice(audio_path)
        customer_msg = self._sanitize_customer_input(customer_raw)
        result["conversation"].append({
            "phase": 2, "role": "고객", "message": customer_msg,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"📞 고객: {customer_msg}\n")

        verdict = await self._judge_transaction(customer_msg)
        result["conversation"].append({
            "phase": 2, "role": "시스템", "message": verdict,
            "timestamp": datetime.now().isoformat(),
        })

        if verdict == "정상거래":
            response_msg = "감사합니다. 확인되었습니다. 불편을 드려 죄송합니다. 향후 유사 거래는 자동으로 허가하도록 조정하겠습니다."
            print("✓ [결과:정상거래]\n")
        elif verdict == "부정거래확정":
            response_msg = "안타깝게도 부정거래로 판정됩니다. 즉시 해당 거래를 취소하고, 신용카드를 긴급 중단하겠습니다. 새로운 카드를 발급해드리겠습니다."
            print("⚠️ [결과:부정거래확정]\n")
        else:
            response_msg = "그렇다면 안전을 위해 일단 해당 거래를 차단하고, 나중에 확인 후 연락드리는 방식으로 진행하겠습니다."
            print("❓ [결과:거래확인불가]\n")

        result["conversation"].append({
            "phase": 2, "role": "상담사", "message": response_msg,
            "timestamp": datetime.now().isoformat(),
        })
        print(f"📱 상담사: {response_msg}\n")
        return verdict

    async def _judge_identity(self, customer_response: str) -> str:
        """본인확인 판정 — temperature=0으로 일관성 최대화"""
        prompt = _JUDGE_IDENTITY_PROMPT.format(response=customer_response)
        llm_output = await self._call_llm_judge(prompt)
        # 부정 컨텍스트("[결과:본인확인]이 아닙니다" 등)를 잘못 통과하지 않도록 체크
        negative_words = ["아님", "아닙니다", "거부", "실패", "통화실패"]
        if "[결과:본인확인]" in llm_output and not any(w in llm_output for w in negative_words):
            return "본인확인"
        return "통화실패"

    async def _judge_transaction(self, customer_response: str) -> str:
        """거래확인 판정 — temperature=0으로 일관성 최대화"""
        prompt = _JUDGE_TRANSACTION_PROMPT.format(response=customer_response)
        llm_output = await self._call_llm_judge(prompt)
        if "[결과:정상거래]" in llm_output:
            return "정상거래"
        elif "[결과:부정거래확정]" in llm_output:
            return "부정거래확정"
        return "거래확인불가"

    async def _call_llm_dialogue(self, prompt: str) -> str:
        """대화 생성용 LLM 호출 — temperature=0.7, 최대 256 토큰"""
        return await self._call_llm_raw(prompt, temperature=0.7, max_tokens=256)

    async def _call_llm_judge(self, prompt: str) -> str:
        """판정 전용 LLM 호출 — temperature=0, 짧은 응답(64 토큰)"""
        return await self._call_llm_raw(prompt, temperature=0.0, max_tokens=64)

    async def _call_llm_raw(self, prompt: str, temperature: float, max_tokens: int) -> str:
        last_error = None
        for attempt in range(3):
            try:
                response = await self.llm_client.chat.completions.create(
                    model=self.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": "당신은 신용카드사의 FDS 담당 AI 상담사입니다."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=0.9,
                    timeout=30,
                )
                return response.choices[0].message.content
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                last_error = e
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"LLM 호출 오류: {e}")
                return "죄송하지만 일시적인 기술적 문제가 발생했습니다."
        logger.error(f"LLM 3회 재시도 실패: {last_error}")
        return "죄송하지만 일시적인 기술적 문제가 발생했습니다."

    async def _synthesize_speech(self, text: str, customer_id: str) -> Optional[str]:
        if not self.supertonic_tts:
            return None
        try:
            # supertonic.synthesize()는 동기 블로킹 — run_in_executor로 event loop 보호
            loop = asyncio.get_event_loop()
            audio_data, sample_rate = await loop.run_in_executor(
                None, lambda: self.supertonic_tts.synthesize(text=text, lang="ko")
            )
            output_dir = Path("audio/output")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"response_{customer_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
            wavfile.write(str(output_path), sample_rate, audio_data)
            return str(output_path)
        except Exception as e:
            logger.error(f"TTS 오류: {e}")
            return None

    def _get_final_message(self, phase2_result: str) -> str:
        if phase2_result == "정상거래":
            return "감사합니다. 거래가 승인되었습니다."
        elif phase2_result == "부정거래확정":
            return "신용카드가 긴급 중단되었습니다. 새로운 카드를 발급해드리겠습니다."
        return "거래를 검토 중입니다. 나중에 연락하겠습니다."

    def _print_call_summary(self, result: Dict):
        print(f"\n{'█' * 70}")
        print(f"█ [통화 완료] 결과: {result['result']}")
        if result["phase1_result"]:
            print(f"█ PHASE 1: {result['phase1_result']}")
        if result["phase2_result"]:
            print(f"█ PHASE 2: {result['phase2_result']}")
        if result["fds_analysis"]:
            print(f"█ FDS 위험도: {result['fds_analysis']['risk_level'].upper()}")
        print(f"{'█' * 70}\n")

    def _save_log(self, result: Dict):
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        filename = log_dir / f"call_{result['customer_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        log_data = result.copy()
        # 개인정보 익명화
        name = log_data.get("customer_name", "")
        log_data["customer_name"] = (name[0] + "*" * (len(name) - 1)) if name else ""
        log_data["card_last4"] = "****"
        cid = log_data.get("customer_id", "")
        log_data["customer_id"] = hashlib.sha256(cid.encode()).hexdigest()[:16]

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ 로그 저장: {filename}")


async def main():
    nim_api_key = os.getenv("NVIDIA_API_KEY")
    if not nim_api_key or nim_api_key == "nvapi-your-key":
        print("\n❌ 오류: NVIDIA_API_KEY를 설정해주세요")
        print("  .env 파일: NVIDIA_API_KEY=nvapi-...")
        return

    advisor = FDSAIAdvisorDetailed(nim_api_key=nim_api_key, whisper_device="cpu")

    result = await advisor.handle_fraud_suspicion(
        customer_name="김철수",
        customer_id="CUST_20240615_001",
        card_last4="1234",
        transaction={
            "amount": 150000,
            "merchant": "Amazon USA",
            "location": "뉴욕, 미국",
            "time": "2024-06-15 03:45",
        },
        bank_name="카드사",
        audio_input_path=None,  # None → 테스트 콘솔 입력 모드
    )

    print("\n" + "=" * 70)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
