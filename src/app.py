#!/usr/bin/env python3
"""
FDS AI 상담사 - Flask 테스트 UI
"""

import asyncio
import io
import json
import os
import queue
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

sys.path.insert(0, str(Path(__file__).parent))
from fds_detector import detect_fraud_risk

load_dotenv(Path(__file__).parent.parent / ".env")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

# ─── 모델 캐시 (요청마다 재로드 방지) ──────────────────────────────────────────
_whisper_model = None
_supertonic_tts = None
_model_lock = threading.Lock()

TTS_VOICES = {
    "F1": "여성 1 (차분)",
    "F2": "여성 2 (밝음)",
    "F3": "여성 3 (부드러움)",
    "F4": "여성 4 (또렷함)",
    "F5": "여성 5 (친근함)",
    "M1": "남성 1 (낮음)",
    "M2": "남성 2 (신뢰감)",
    "M3": "남성 3 (중립)",
    "M4": "남성 4 (젊음)",
    "M5": "남성 5 (전문적)",
}

def get_whisper():
    global _whisper_model
    with _model_lock:
        if _whisper_model is None:
            import whisper
            size = os.getenv("WHISPER_MODEL", "base")
            _whisper_model = whisper.load_model(size)
    return _whisper_model

def get_tts():
    global _supertonic_tts
    with _model_lock:
        if _supertonic_tts is None:
            from supertonic import TTS as SupertonicTTS
            _supertonic_tts = SupertonicTTS(auto_download=True)
    return _supertonic_tts


# ─── 기본 라우트 ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", voices=TTS_VOICES)


@app.route("/api/models/status")
def api_model_status():
    return jsonify({
        "whisper": _whisper_model is not None,
        "tts": _supertonic_tts is not None,
        "whisper_model": os.getenv("WHISPER_MODEL", "base"),
    })


# ─── FDS 탐지 ────────────────────────────────────────────────────────────────

@app.route("/api/detect", methods=["POST"])
def api_detect():
    data = request.json or {}
    try:
        amount = int(data.get("amount", 0))
        customer_avg = int(data.get("customer_avg_amount", 500000))
    except (ValueError, TypeError):
        return jsonify({"error": "금액은 숫자여야 합니다."}), 400

    result = detect_fraud_risk({
        "amount": amount,
        "location": data.get("location", ""),
        "time": data.get("time", ""),
        "merchant": data.get("merchant", ""),
        "type": data.get("type", "국내결제"),
        "is_abroad": data.get("is_abroad", False),
        "customer_avg_amount": customer_avg,
    })
    return jsonify(result)


# ─── STT (음성 → 텍스트) ─────────────────────────────────────────────────────

@app.route("/api/stt", methods=["POST"])
def api_stt():
    """
    음성 파일 → 텍스트 변환 (Whisper)
    multipart/form-data: audio=<file>
    """
    if "audio" not in request.files:
        return jsonify({"error": "audio 파일이 없습니다."}), 400

    audio_file = request.files["audio"]
    suffix = Path(audio_file.filename or "audio.webm").suffix or ".webm"

    # 임시 파일에 저장 (Whisper는 파일 경로 필요)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        model = get_whisper()
        result = model.transcribe(tmp_path, language="ko", fp16=False)
        transcript = result["text"].strip()
        segments = [
            {"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
            for s in result.get("segments", [])
        ]
        return jsonify({
            "transcript": transcript,
            "segments": segments,
            "language": result.get("language", "ko"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ─── TTS (텍스트 → 음성) ─────────────────────────────────────────────────────

@app.route("/api/tts", methods=["POST"])
def api_tts():
    """
    텍스트 → WAV 음성 생성 (Supertonic)
    JSON: { text, voice, speed }
    """
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text가 비어있습니다."}), 400
    if len(text) > 500:
        return jsonify({"error": "텍스트는 500자 이하여야 합니다."}), 400

    voice_name = data.get("voice", "F1")
    if voice_name not in TTS_VOICES:
        return jsonify({"error": f"유효하지 않은 목소리입니다. 가능: {list(TTS_VOICES.keys())}"}), 400

    speed = float(data.get("speed", 1.0))
    speed = max(0.5, min(2.0, speed))  # 0.5x ~ 2.0x 범위 제한

    try:
        tts = get_tts()
        style = tts.get_voice_style(voice_name)
        audio, _dur = tts.synthesize(text, voice_style=style, speed=speed, lang="ko")

        # (1, N) → (N,) 로 변환 후 WAV 버퍼에 쓰기
        audio_1d = audio.squeeze()
        buf = io.BytesIO()
        wavfile.write(buf, tts.sample_rate, audio_1d)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="audio/wav",
            as_attachment=False,
            download_name="tts_output.wav",
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── 전체 시뮬레이션 (SSE) ────────────────────────────────────────────────────

def _run_simulation(payload: dict, eq: queue.Queue):
    import openai, re
    from fraud_suspicion_prompt_detailed import FraudSuspicionPrompt

    nim_api_key = payload.get("nim_api_key") or os.getenv("NVIDIA_API_KEY", "")
    if not nim_api_key or nim_api_key.startswith("nvapi-your"):
        eq.put(("error", "NVIDIA_API_KEY가 설정되지 않았습니다."))
        return

    customer_name   = payload.get("customer_name", "고객")
    bank_name       = payload.get("bank_name", "카드사")
    phase1_response = payload.get("phase1_response", "네, 맞습니다.")
    phase2_response = payload.get("phase2_response", "아니요, 제가 안 했어요.")
    tts_voice       = payload.get("tts_voice", "")  # 비어있으면 TTS 생략

    transaction = {
        "amount":              int(payload.get("amount", 0)),
        "merchant":            payload.get("merchant", ""),
        "location":            payload.get("location", ""),
        "time":                payload.get("time", ""),
        "type":                payload.get("type", "국내결제"),
        "is_abroad":           payload.get("is_abroad", False),
        "customer_avg_amount": int(payload.get("customer_avg_amount", 500000)),
    }

    def sanitize(text: str) -> str:
        text = re.sub(r"\[결과:[^\]]+\]", "", text)
        return text[:500]

    def tts_speak(text: str):
        """TTS 음성 생성 후 base64로 SSE 전송"""
        if not tts_voice:
            return
        try:
            import base64
            tts = get_tts()
            style = tts.get_voice_style(tts_voice)
            audio, _ = tts.synthesize(text, voice_style=style, lang="ko")
            buf = io.BytesIO()
            wavfile.write(buf, tts.sample_rate, audio.squeeze())
            b64 = base64.b64encode(buf.getvalue()).decode()
            eq.put(("tts_audio", b64))
        except Exception as e:
            eq.put(("tts_error", str(e)))

    async def run():
        client = openai.AsyncOpenAI(
            api_key=nim_api_key,
            base_url="https://integrate.api.nvidia.com/v1",
        )

        async def call_llm(prompt: str, temperature=0.7, max_tokens=256) -> str:
            try:
                resp = await client.chat.completions.create(
                    model="openai/gpt-oss-120b-instruct",
                    messages=[
                        {"role": "system", "content": "당신은 신용카드사의 FDS 담당 AI 상담사입니다."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=30,
                )
                return resp.choices[0].message.content
            except Exception as e:
                return f"[LLM 오류: {e}]"

        # FDS 판정
        eq.put(("step", "FDS 위험도 판정 중..."))
        fds = detect_fraud_risk(transaction)
        eq.put(("fds", json.dumps(fds, ensure_ascii=False)))

        # PHASE 1
        eq.put(("step", "PHASE 1: 본인확인"))
        greeting = await call_llm(
            FraudSuspicionPrompt.generate_phase1_prompt(customer_name=customer_name, bank_name=bank_name),
            temperature=0.7, max_tokens=128,
        )
        eq.put(("advisor", greeting))
        threading.Thread(target=tts_speak, args=(greeting,), daemon=True).start()

        safe_p1 = sanitize(phase1_response)
        eq.put(("customer", safe_p1))

        verdict_p1 = await call_llm(
            f'고객 응답: "{safe_p1}"\n\n판정:\n- 본인확인: [결과:본인확인]\n- 실패: [결과:통화실패]',
            temperature=0.0, max_tokens=32,
        )
        negative = ["아님", "아닙니다", "거부", "실패", "통화실패"]
        if "[결과:본인확인]" in verdict_p1 and not any(w in verdict_p1 for w in negative):
            eq.put(("result_phase1", "본인확인"))
        else:
            farewell = f"실례했습니다. {bank_name}였습니다. 감사합니다."
            eq.put(("advisor", farewell))
            threading.Thread(target=tts_speak, args=(farewell,), daemon=True).start()
            eq.put(("result_phase1", "통화실패"))
            eq.put(("final_result", "통화실패"))
            return

        # PHASE 2
        eq.put(("step", "PHASE 2: 거래확인"))
        question = await call_llm(
            FraudSuspicionPrompt.generate_phase2_prompt(
                customer_name=customer_name,
                transaction_amount=transaction["amount"],
                merchant_name=transaction["merchant"],
                transaction_time=transaction["time"],
                transaction_location=transaction["location"],
                fraud_risk_level=fds["risk_level"],
                bank_name=bank_name,
            ),
            temperature=0.7, max_tokens=128,
        )
        eq.put(("advisor", question))
        threading.Thread(target=tts_speak, args=(question,), daemon=True).start()

        safe_p2 = sanitize(phase2_response)
        eq.put(("customer", safe_p2))

        verdict_p2 = await call_llm(
            f'고객 응답: "{safe_p2}"\n\n판정:\n- 정상거래: [결과:정상거래]\n- 부정거래: [결과:부정거래확정]\n- 불확실: [결과:거래확인불가]',
            temperature=0.0, max_tokens=32,
        )

        if "[결과:정상거래]" in verdict_p2:
            final, reply = "정상거래", "감사합니다. 확인되었습니다. 불편을 드려 죄송합니다."
        elif "[결과:부정거래확정]" in verdict_p2:
            final, reply = "부정거래확정", "부정거래로 판정됩니다. 즉시 카드를 긴급 중단하고 새 카드를 발급해드리겠습니다."
        else:
            final, reply = "거래확인불가", "안전을 위해 거래를 차단하고 나중에 재확인하겠습니다."

        eq.put(("advisor", reply))
        threading.Thread(target=tts_speak, args=(reply,), daemon=True).start()
        eq.put(("result_phase2", final))
        eq.put(("final_result", final))

    try:
        asyncio.run(run())
    except Exception as e:
        eq.put(("error", str(e)))
    finally:
        eq.put(("done", ""))


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    payload = request.json or {}
    eq: queue.Queue = queue.Queue()
    threading.Thread(target=_run_simulation, args=(payload, eq), daemon=True).start()

    def generate():
        while True:
            try:
                evt, data = eq.get(timeout=90)
                yield f"event: {evt}\ndata: {data}\n\n"
                if evt in ("done", "error"):
                    break
            except queue.Empty:
                yield "event: error\ndata: 시간 초과\n\n"
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── 로그 ────────────────────────────────────────────────────────────────────

# ─── AGI TTS (Asterisk 호환 8kHz mono WAV) ───────────────────────────────────

@app.route("/api/agi/tts", methods=["POST"])
def api_agi_tts():
    """
    Asterisk AGI용 TTS — 8000Hz mono int16 WAV 반환
    JSON: { text, voice }
    """
    from scipy.signal import resample_poly

    data  = request.json or {}
    text  = data.get("text", "").strip()
    voice = data.get("voice", "F1")

    if not text:
        return jsonify({"error": "text가 비어있습니다."}), 400
    if voice not in TTS_VOICES:
        return jsonify({"error": "유효하지 않은 목소리입니다."}), 400

    try:
        tts_engine = get_tts()
        style = tts_engine.get_voice_style(voice)
        audio, _ = tts_engine.synthesize(text, voice_style=style, lang="ko")

        # (1, N) → float32 mono
        audio_f = audio.squeeze().astype(np.float32)
        peak = np.abs(audio_f).max()
        if peak > 0:
            audio_f /= peak

        # 44100 → 8000 Hz 리샘플 (441/80)
        audio_8k = resample_poly(audio_f, 80, 441).astype(np.float32)
        audio_8k_int = (audio_8k * 32767).clip(-32768, 32767).astype(np.int16)

        buf = io.BytesIO()
        wavfile.write(buf, 8000, audio_8k_int)
        buf.seek(0)
        return send_file(buf, mimetype="audio/wav", download_name="agi_tts.wav")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── AGI Judge (LLM 판정) ─────────────────────────────────────────────────────

@app.route("/api/agi/judge", methods=["POST"])
def api_agi_judge():
    """
    PHASE 1/2 판정 — 고객 응답 텍스트 → verdict 반환
    JSON: { phase, transcript, context }
    """
    import openai

    data       = request.json or {}
    phase      = int(data.get("phase", 1))
    transcript = data.get("transcript", "").strip()
    ctx        = data.get("context", {})

    api_key = os.getenv("NVIDIA_API_KEY", "")
    if not api_key or api_key.startswith("nvapi-your"):
        return jsonify({"error": "NVIDIA_API_KEY 미설정"}), 500

    if phase == 1:
        prompt = (
            f'고객 응답: "{transcript}"\n\n'
            "고객이 본인임을 확인하면 confirmed, 아니면 denied 한 단어로만 답하세요."
        )
    else:
        prompt = (
            f'고객 응답: "{transcript}"\n\n'
            "고객이 본인 거래라고 하면 legitimate, "
            "부정 거래라고 하면 fraud, "
            "불확실하면 uncertain — 한 단어로만 답하세요."
        )

    try:
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
        )
        resp = client.chat.completions.create(
            model=os.getenv("NIM_MODEL", "openai/gpt-oss-120b-instruct"),
            messages=[
                {"role": "system", "content": "FDS 판정 AI. 지시된 단어 하나로만 답하세요."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0,
            max_tokens=16,
        )
        answer = resp.choices[0].message.content.strip().lower()

        if phase == 1:
            verdict = "confirmed" if "confirmed" in answer else "denied"
        else:
            if "legitimate" in answer:
                verdict = "legitimate"
            elif "fraud" in answer:
                verdict = "fraud"
            else:
                verdict = "uncertain"

        return jsonify({"verdict": verdict, "raw": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── 자동 발신 트리거 ─────────────────────────────────────────────────────────

@app.route("/api/call", methods=["POST"])
def api_call():
    """
    iPhone(Linphone)으로 FDS 자동 발신
    JSON: { customer_name, transaction, risk_level }
    """
    try:
        from caller import call as ami_call
    except ImportError:
        return jsonify({"error": "caller.py를 찾을 수 없습니다."}), 500

    data   = request.json or {}
    result = ami_call(
        transaction   = data.get("transaction", {}),
        customer_name = data.get("customer_name", "고객"),
        risk_level    = data.get("risk_level",    "high"),
    )
    return jsonify(result)


# ─── 로그 ────────────────────────────────────────────────────────────────────

@app.route("/api/logs")
def api_logs():
    log_dir = Path(__file__).parent.parent / "logs"
    if not log_dir.exists():
        return jsonify([])
    logs = []
    for f in sorted(log_dir.glob("call_*.json"), reverse=True)[:20]:
        try:
            logs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return jsonify(logs)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
