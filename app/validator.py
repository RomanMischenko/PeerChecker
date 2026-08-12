import logging
from typing import Any
from app.s21_api import S21ApiClient

logger = logging.getLogger(__name__)


class PeerValidator:
    def __init__(self, min_xp: int = 0, min_logtime: float = 0.0):
        self.min_xp = min_xp
        self.min_logtime = min_logtime

    def validate_peer(self, api_client: S21ApiClient, login: str) -> dict[str, Any]:
        """
        Gathers participant details via API client and evaluates whether the peer is
        VERIFIED (live student) or SUSPICIOUS (test/inactive account).
        
        Returns dict containing:
        - login: str
        - status: 'VERIFIED' | 'SUSPICIOUS'
        - total_xp: int
        - logtime: float
        - suspicion_reasons: list[str]
        - details: dict
        """
        logger.info(f"Validating peer: {login}")

        # Fetch detailed info
        info = api_client.get_participant_info(login)
        logtime = api_client.get_participant_logtime(login)
        xp_history = api_client.get_participant_xp_history(login)
        points = api_client.get_participant_points(login)

        # Calculate total XP from history or profile info
        total_xp = 0
        if isinstance(xp_history, list):
            for entry in xp_history:
                if isinstance(entry, dict):
                    xp_val = entry.get("value") or entry.get("xp") or entry.get("exp", 0)
                    if isinstance(xp_val, (int, float)):
                        total_xp += int(xp_val)

        if total_xp == 0 and isinstance(info, dict):
            xp_val = info.get("xp") or info.get("totalXp") or info.get("experience", 0)
            if isinstance(xp_val, (int, float)):
                total_xp = int(xp_val)

        reasons = []

        # Criterion 1: XP
        if total_xp <= self.min_xp:
            reasons.append(f"XP равен {total_xp} (порог > {self.min_xp})")

        # Criterion 2: Logtime
        if logtime <= self.min_logtime:
            reasons.append(f"Логтайм равен {logtime:.2f} ч/нед (порог > {self.min_logtime})")

        # Criterion 3: Activity / XP history non-empty
        has_activity = len(xp_history) > 0 or bool(points)
        if not has_activity and total_xp <= 0 and logtime <= 0:
            reasons.append("Отсутствуют записи об активности и проверках")

        status = "VERIFIED" if not reasons else "SUSPICIOUS"

        details = {
            "info": info,
            "xp_history_count": len(xp_history) if isinstance(xp_history, list) else 0,
            "points": points,
        }

        result = {
            "login": login,
            "status": status,
            "total_xp": total_xp,
            "logtime": logtime,
            "suspicion_reasons": reasons,
            "suspicion_reason_text": "; ".join(reasons) if reasons else "Прошел проверку",
            "details": details,
        }

        logger.info(f"Peer {login} validated as {status} (XP={total_xp}, logtime={logtime:.2f})")
        return result
