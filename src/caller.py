#!/usr/bin/env python3
"""
FDS 자동 발신 — AMI(Asterisk Manager Interface)로 iPhone(Linphone)에 전화 걸기
"""

import json
import os
import socket
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ASTERISK_HOST   = os.getenv("ASTERISK_HOST",        "127.0.0.1")
ASTERISK_PORT   = int(os.getenv("ASTERISK_AMI_PORT", 5038))
ASTERISK_USER   = os.getenv("ASTERISK_AMI_USER",    "fds")
ASTERISK_SECRET = os.getenv("ASTERISK_AMI_SECRET",  "fdsmanager123")


def _send(sock: socket.socket, lines: list[str]):
    msg = "\r\n".join(lines) + "\r\n\r\n"
    sock.sendall(msg.encode("utf-8"))
    time.sleep(0.15)


def _recv(sock: socket.socket, timeout: float = 3.0) -> str:
    sock.settimeout(timeout)
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\r\n\r\n" in buf:
                break
    except socket.timeout:
        pass
    return buf.decode("utf-8", errors="ignore")


def call(transaction: dict, customer_name: str = "고객",
         risk_level: str = "high") -> dict:
    """
    iPhone(Linphone SIP 내선)으로 발신.

    transaction 필드:
        amount, merchant, location, time, risk_level

    returns: {"success": bool, "message": str}
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((ASTERISK_HOST, ASTERISK_PORT))

            # 배너 수신
            s.recv(1024)

            # 로그인
            _send(s, [
                "Action: Login",
                f"Username: {ASTERISK_USER}",
                f"Secret: {ASTERISK_SECRET}",
                "ActionID: fds-login",
            ])
            resp = _recv(s)
            if "Success" not in resp:
                return {"success": False, "message": f"AMI 로그인 실패: {resp[:200]}"}

            # SIP/iphone 으로 발신 → 수신 시 fds-outbound 컨텍스트 실행
            _send(s, [
                "Action: Originate",
                "Channel: SIP/iphone",
                "Context: fds-outbound",
                "Exten: s",
                "Priority: 1",
                "Timeout: 30000",
                "CallerID: FDS 상담사 <0000>",
                "Async: true",
                "ActionID: fds-originate",
                f"Variable: FDS_CUSTOMER_NAME={customer_name}",
                f"Variable: FDS_AMOUNT={transaction.get('amount', '')}",
                f"Variable: FDS_MERCHANT={transaction.get('merchant', '')}",
                f"Variable: FDS_LOCATION={transaction.get('location', '')}",
                f"Variable: FDS_TIME={transaction.get('time', '')}",
                f"Variable: FDS_RISK_LEVEL={risk_level}",
            ])
            resp = _recv(s)
            ok = "Success" in resp or "Queued" in resp

            _send(s, ["Action: Logoff"])

            return {
                "success": ok,
                "message": "발신 성공 — iPhone이 울립니다" if ok else f"발신 실패: {resp[:200]}",
            }

    except ConnectionRefusedError:
        return {"success": False, "message": "Asterisk에 연결할 수 없습니다. Docker가 실행 중인지 확인하세요."}
    except Exception as e:
        return {"success": False, "message": str(e)}


if __name__ == "__main__":
    result = call(
        transaction={
            "amount":   150000,
            "merchant": "Amazon USA",
            "location": "뉴욕, 미국",
            "time":     "2026-06-16 03:45",
        },
        customer_name="테스트 고객",
        risk_level="high",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
