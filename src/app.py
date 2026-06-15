#!/usr/bin/env python3
"""
FDS AI 상담사 - Flask 테스트 UI
"""

import asyncio
import json
import os
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

# 현재 파일 기준 import 경로 설정
sys.path.insert(0, str(Path(__file__).parent))

from fds_detector import detect_fraud_risk

load_dotenv(Path(__file__).parent.parent / ".env")

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False


# ─── FDS 탐지 전용 (API 키 불필요) ───────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/detect", methods=["POST"])
def api_detect():
    """FDS 위험도 판정 — NVIDIA API 키 불필요"""
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


# ─── 전체 시뮬레이션 (SSE 스트리밍) ──────────────────────────────────────────

def _run_simulation(payload: dict, event_queue: queue.Queue):
    """백그라운드 스레드에서 비동기 시뮬레이션 실행"""
    import openai
    from fraud_suspicion_prompt_detailed import FraudSuspicionPrompt

    nim_api_key = payload.get("nim_api_key") or os.getenv("NVIDIA_API_KEY", "")
    if not nim_api_key or nim_api_key.startswith("nvapi-your"):
        event_queue.put(("error", "NVIDIA_API_KEY가 설정되지 않았습니다."))
        return

    customer_name    = payload.get("customer_name", "고객")
    bank_name        = payload.get("bank_name", "카드사")
    phase1_response  = payload.get("phase1_response", "네, 맞습니다.")
    phase2_response  = payload.get("phase2_response", "아니요, 제가 안 했어요.")

    transaction = {
        "amount":              int(payload.get("amount", 0)),
        "merchant":            payload.get("merchant", ""),
        "location":            payload.get("location", ""),
        "time":                payload.get("time", ""),
        "type":                payload.get("type", "국내결제"),
        "is_abroad":           payload.get("is_abroad", False),
        "customer_avg_amount": int(payload.get("customer_avg_amount", 500000)),
    }

    async def run():
        client = openai.AsyncOpenAI(
            api_key=nim_api_key,
            base_url="https://integrate.api.nvidia.com/v1",
        )

        async def call_llm(prompt: str, temperature: float = 0.7, max_tokens: int = 256) -> str:
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

        import re

        def sanitize(text: str) -> str:
            text = re.sub(r"\[결과:[^\]]+\]", "", text)
            return text[:500]

        # ── FDS 판정 ──────────────────────────────────────────────────────────
        event_queue.put(("step", "FDS 위험도 판정 중..."))
        fds = detect_fraud_risk(transaction)
        event_queue.put(("fds", json.dumps(fds, ensure_ascii=False)))

        # ── PHASE 1: 본인확인 ─────────────────────────────────────────────────
        event_queue.put(("step", "PHASE 1: 본인확인"))

        greeting_prompt = FraudSuspicionPrompt.generate_phase1_prompt(
            customer_name=customer_name, bank_name=bank_name
        )
        greeting = await call_llm(greeting_prompt, temperature=0.7, max_tokens=128)
        event_queue.put(("advisor", greeting))

        safe_p1 = sanitize(phase1_response)
        event_queue.put(("customer", safe_p1))

        judge_p1 = f"""고객 응답: "{safe_p1}"\n\n판정 (태그만 출력):\n- 본인 확인됨: [결과:본인확인]\n- 본인 아님/불명확: [결과:통화실패]"""
        verdict_p1 = await call_llm(judge_p1, temperature=0.0, max_tokens=32)

        negative_words = ["아님", "아닙니다", "거부", "실패", "통화실패"]
        if "[결과:본인확인]" in verdict_p1 and not any(w in verdict_p1 for w in negative_words):
            event_queue.put(("result_phase1", "본인확인"))
        else:
            farewell = f"실례했습니다. {bank_name}였습니다. 감사합니다."
            event_queue.put(("advisor", farewell))
            event_queue.put(("result_phase1", "통화실패"))
            event_queue.put(("final_result", "통화실패"))
            return

        # ── PHASE 2: 거래확인 ─────────────────────────────────────────────────
        event_queue.put(("step", "PHASE 2: 거래확인"))

        question_prompt = FraudSuspicionPrompt.generate_phase2_prompt(
            customer_name=customer_name,
            transaction_amount=transaction["amount"],
            merchant_name=transaction["merchant"],
            transaction_time=transaction["time"],
            transaction_location=transaction["location"],
            fraud_risk_level=fds["risk_level"],
            bank_name=bank_name,
        )
        question = await call_llm(question_prompt, temperature=0.7, max_tokens=128)
        event_queue.put(("advisor", question))

        safe_p2 = sanitize(phase2_response)
        event_queue.put(("customer", safe_p2))

        judge_p2 = f"""고객 응답: "{safe_p2}"\n\n판정 (태그만 출력):\n- 거래 인정: [결과:정상거래]\n- 거래 부정: [결과:부정거래확정]\n- 불확실/모름: [결과:거래확인불가]"""
        verdict_p2 = await call_llm(judge_p2, temperature=0.0, max_tokens=32)

        if "[결과:정상거래]" in verdict_p2:
            final = "정상거래"
            reply = "감사합니다. 확인되었습니다. 불편을 드려 죄송합니다."
        elif "[결과:부정거래확정]" in verdict_p2:
            final = "부정거래확정"
            reply = "부정거래로 판정됩니다. 즉시 카드를 긴급 중단하고 새 카드를 발급해드리겠습니다."
        else:
            final = "거래확인불가"
            reply = "안전을 위해 거래를 차단하고 나중에 재확인하겠습니다."

        event_queue.put(("advisor", reply))
        event_queue.put(("result_phase2", final))
        event_queue.put(("final_result", final))

    try:
        asyncio.run(run())
    except Exception as e:
        event_queue.put(("error", str(e)))
    finally:
        event_queue.put(("done", ""))


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    """전체 상담 시뮬레이션 — SSE 스트리밍"""
    payload = request.json or {}
    event_queue: queue.Queue = queue.Queue()

    thread = threading.Thread(target=_run_simulation, args=(payload, event_queue), daemon=True)
    thread.start()

    def generate():
        while True:
            try:
                event_type, data = event_queue.get(timeout=60)
                yield f"event: {event_type}\ndata: {data}\n\n"
                if event_type in ("done", "error"):
                    break
            except queue.Empty:
                yield "event: error\ndata: 시간 초과\n\n"
                break

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─── 로그 조회 ───────────────────────────────────────────────────────────────

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
