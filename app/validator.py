import logging
from typing import Any
from app.s21_api import S21ApiClient

logger = logging.getLogger(__name__)


class PeerValidator:
    def __init__(
        self,
        target_project_ids: list[int] | None = None,
        min_accepted_projects: int = 3,
        min_logtime: float = 0.0,
    ):
        self.target_project_ids = (
            target_project_ids
            if target_project_ids is not None
            else [73187, 73188, 73189, 73328, 73190, 73191, 73192, 73193, 73194, 73195, 73196]
        )
        self.min_accepted_projects = min_accepted_projects
        self.min_logtime = min_logtime

    def validate_peer(self, api_client: S21ApiClient, login: str) -> dict[str, Any]:
        """
        Gathers participant details via API client and evaluates whether the peer is
        VERIFIED (live student) or SUSPICIOUS (test/inactive account) based on target projects status.
        """
        logger.info(f"Validating peer: {login}")

        # Fetch detailed info & logtime for details card
        info = api_client.get_participant_info(login)
        logtime = api_client.get_participant_logtime(login)
        xp_history = api_client.get_participant_xp_history(login)

        # Calculate total XP for reference
        total_xp = 0
        if isinstance(xp_history, list):
            for entry in xp_history:
                if isinstance(entry, dict):
                    xp_val = entry.get("expValue") or entry.get("value") or entry.get("xp") or entry.get("exp", 0)
                    if isinstance(xp_val, (int, float)):
                        total_xp += int(xp_val)

        if total_xp == 0 and isinstance(info, dict):
            xp_val = info.get("expValue") or info.get("xp") or info.get("totalXp") or info.get("experience", 0)
            if isinstance(xp_val, (int, float)):
                total_xp = int(xp_val)

        # Check projects: count ACCEPTED projects among target_project_ids via GET /v1/participants/{login}/projects/{projectId}
        accepted_projects = []
        for pid in self.target_project_ids:
            proj_data = api_client.get_participant_project(login, pid)
            p_status = proj_data.get("status", "").upper() if isinstance(proj_data, dict) else ""
            if p_status == "ACCEPTED":
                title = proj_data.get("title") or str(pid)
                accepted_projects.append({"id": pid, "title": title})

        accepted_count = len(accepted_projects)


        # Fetch feedback data: /v1/participants/{login}/feedback

        feedback = api_client.get_participant_feedback(login)
        punctuality = float(feedback.get("averageVerifierPunctuality", 0)) if isinstance(feedback, dict) else 0.0
        interest = float(feedback.get("averageVerifierInterest", 0)) if isinstance(feedback, dict) else 0.0
        thoroughness = float(feedback.get("averageVerifierThoroughness", 0)) if isinstance(feedback, dict) else 0.0
        friendliness = float(feedback.get("averageVerifierFriendliness", 0)) if isinstance(feedback, dict) else 0.0

        has_feedback = (
            punctuality > 0 and interest > 0 and thoroughness > 0 and friendliness > 0
        )

        reasons = []

        # Criterion 1: Target Projects count in ACCEPTED status (must be >= min_accepted_projects)
        if accepted_count < self.min_accepted_projects:
            reasons.append(
                f"Сдано проектов из списка: {accepted_count} (требуется минимум {self.min_accepted_projects})"
            )

        # Criterion 2: Peer Feedback scores must be non-zero
        if not has_feedback:
            reasons.append("Оценки фидбека проверяющего равны 0 (учетная запись не получала фидбеков)")

        status = "VERIFIED" if not reasons else "SUSPICIOUS"

        details = {
            "info": info,
            "accepted_count": accepted_count,
            "accepted_projects": accepted_projects,
            "feedback": feedback,
            "xp_history_count": len(xp_history) if isinstance(xp_history, list) else 0,
        }

        result = {
            "login": login,
            "status": status,
            "total_xp": total_xp,
            "logtime": logtime,
            "accepted_projects_count": accepted_count,
            "feedback": feedback,
            "suspicion_reasons": reasons,
            "suspicion_reason_text": "; ".join(reasons) if reasons else "Прошел проверку (проекты сданы, фидбек есть)",
            "details": details,
        }


        logger.info(
            f"Peer {login} validated as {status} (Accepted Projects={accepted_count}/{self.min_accepted_projects}, XP={total_xp})"
        )
        return result

