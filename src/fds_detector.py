"""
FDS (부정거래 탐지) 로직
거래 특성을 분석해 부정거래 의심도를 판정
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class FDSSignal:
    """부정거래 신호"""
    signal_name: str
    confidence: float
    description: str


class FDSDetector:
    """부정거래 탐지기"""

    AMOUNT_THRESHOLD_HIGH = 2000000

    HIGH_RISK_LOCATIONS = [
        "미국", "중국", "러시아", "필리핀", "나이지리아"
    ]

    HIGH_RISK_CATEGORIES = [
        "도박", "카지노", "경마", "스포츠도박",
        "암호화폐", "비트코인", "선물", "외환거래",
        "성인용품", "불법약물", "무기"
    ]

    SUSPICIOUS_HOURS = range(0, 6)  # 자정 ~ 06:00

    @staticmethod
    def detect_fraud_risk(transaction_data: Dict) -> Dict:
        detector = FDSDetector()
        signals: List[FDSSignal] = []
        risk_score = 0.0

        amount_signal = detector._check_amount(transaction_data)
        if amount_signal:
            signals.append(amount_signal)
            risk_score += 0.25

        location_signal = detector._check_location(transaction_data)
        if location_signal:
            signals.append(location_signal)
            # 고위험 지역은 일반 해외 거래보다 높은 가중치
            risk_score += 0.35 if location_signal.signal_name == "고위험 지역" else 0.25

        time_signal = detector._check_time(transaction_data)
        if time_signal:
            signals.append(time_signal)
            risk_score += 0.15

        category_signal = detector._check_category(transaction_data)
        if category_signal:
            signals.append(category_signal)
            risk_score += 0.20

        if risk_score >= 0.60:
            risk_level = "high"
        elif risk_score >= 0.35:
            risk_level = "medium"
        else:
            risk_level = "low"

        recommendations = detector._generate_recommendations(risk_level, signals)

        signal_summary = {
            "amount": any("거래액" in s.signal_name for s in signals),
            "location": any("지역" in s.signal_name or "거래" in s.signal_name for s in signals),
            "time": any("시간" in s.signal_name or "새벽" in s.signal_name for s in signals),
            "category": any("카테고리" in s.signal_name for s in signals),
        }

        return {
            "risk_level": risk_level,
            "risk_score": round(min(risk_score, 1.0), 2),
            "reasons": [s.description for s in signals],
            "signals": signal_summary,
            "signal_details": [
                {"name": s.signal_name, "confidence": s.confidence, "description": s.description}
                for s in signals
            ],
            "recommendations": recommendations,
        }

    @staticmethod
    def _check_amount(transaction_data: Dict) -> Optional[FDSSignal]:
        amount = transaction_data.get("amount", 0)
        customer_avg = transaction_data.get("customer_avg_amount", 500000)

        if customer_avg > 0 and amount >= customer_avg * 3:
            return FDSSignal(
                signal_name="비정상 거래액",
                confidence=0.85,
                description=f"거래액({amount:,}원)이 평소 거래액({customer_avg:,}원)보다 {amount // customer_avg}배 높음",
            )
        if amount >= FDSDetector.AMOUNT_THRESHOLD_HIGH:
            return FDSSignal(
                signal_name="대액 거래",
                confidence=0.60,
                description=f"대액 거래({amount:,}원) 감지",
            )
        return None

    @staticmethod
    def _check_location(transaction_data: Dict) -> Optional[FDSSignal]:
        location = transaction_data.get("location", "")
        is_abroad = transaction_data.get("is_abroad", False)

        # 고위험 지역을 먼저 체크 (신뢰도 0.90 > 해외 거래 0.70)
        # 원본 코드는 is_abroad 체크가 먼저 있어 고위험 지역 코드가 절대 실행되지 않았음
        for risk_location in FDSDetector.HIGH_RISK_LOCATIONS:
            if risk_location in location:
                return FDSSignal(
                    signal_name="고위험 지역",
                    confidence=0.90,
                    description=f"고위험 지역({location})에서의 거래",
                )

        if is_abroad or "해외" in location:
            return FDSSignal(
                signal_name="해외 거래",
                confidence=0.70,
                description=f"해외 거래({location}) 감지",
            )

        return None

    @staticmethod
    def _check_time(transaction_data: Dict) -> Optional[FDSSignal]:
        time_str = transaction_data.get("time", "")
        if not time_str:
            return None
        try:
            transaction_time = datetime.strptime(str(time_str)[:16], "%Y-%m-%d %H:%M")
            hour = transaction_time.hour
            if hour in FDSDetector.SUSPICIOUS_HOURS:
                return FDSSignal(
                    signal_name="새벽 거래",
                    confidence=0.75,
                    description=f"새벽 시간({hour:02d}:{transaction_time.minute:02d})의 거래",
                )
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _check_category(transaction_data: Dict) -> Optional[FDSSignal]:
        merchant = transaction_data.get("merchant", "")
        transaction_type = transaction_data.get("type", "")
        combined_text = f"{merchant} {transaction_type}".lower()

        for high_risk_cat in FDSDetector.HIGH_RISK_CATEGORIES:
            if high_risk_cat in combined_text:
                return FDSSignal(
                    signal_name="고위험 카테고리",
                    confidence=0.95,
                    description=f"고위험 카테고리({high_risk_cat}) 거래",
                )
        return None

    @staticmethod
    def _generate_recommendations(risk_level: str, signals: List[FDSSignal]) -> List[str]:
        if risk_level == "high":
            return ["본인 확인 필수", "거래 즉시 취소 제안", "신용카드 긴급 중단", "신규 카드 발급", "카드 분실 여부 확인"]
        elif risk_level == "medium":
            return ["거래 세부사항 확인", "고객 확인 후 진행 여부 결정", "거래 취소 옵션 안내"]
        else:
            return ["거래 승인", "향후 유사 거래 자동 허가 설정", "고객 불편 사과"]


def detect_fraud_risk(transaction_data: Dict) -> Dict:
    return FDSDetector.detect_fraud_risk(transaction_data)
