import logging
from typing import Any
from app.s21_api import S21ApiClient

logger = logging.getLogger(__name__)


def _safe_float(val: Any) -> float:
    """Safely convert value to float, returning 0.0 if None or invalid."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val: Any) -> int:
    """Safely convert value to int, returning 0 if None or invalid."""
    if val is None:
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


class PeerValidator:
    def __init__(
        self,
        target_project_ids: list[int] | None = None,
        min_accepted_projects: int = 3,
        min_logtime: float = 0.0,
        target_class_names: list[str] | None = None,
    ):
        self.target_project_ids = (
            target_project_ids
            if target_project_ids is not None
            else [73187, 73188, 73189, 73328, 73190, 73191, 73192, 73193, 73194, 73195, 73196]
        )
        self.min_accepted_projects = min_accepted_projects
        self.min_logtime = min_logtime
        self.target_class_names = [tc.upper() for tc in target_class_names] if target_class_names else []

    def validate_peer(
        self,
        api_client: S21ApiClient,
        login: str,
        current_index: int | None = None,
        total_count: int | None = None,
    ) -> dict[str, Any]:
        """
        Gathers participant details via API client and evaluates whether the peer is
        VERIFIED (live student) or SUSPICIOUS (test/inactive account) based on target projects status.
        """
        prefix = f"[{current_index}/{total_count}] " if (current_index is not None and total_count is not None) else ""
        logger.info(f"{prefix}Starting validation for peer: {login}")

        # Fetch detailed info
        info = api_client.get_participant_info(login)
        class_name = info.get("className") if isinstance(info, dict) else None

        # Wave / className filter: if target_class_names is set, skip peers not belonging to the wave
        display_class = class_name if class_name else "Не указана"
        if self.target_class_names:
            peer_class = (class_name or "").strip().upper()
            is_match = peer_class in self.target_class_names
            expected_str = ", ".join(self.target_class_names)
            logger.info(
                f"[{login}] GET /v1/participants/{login} -> Wave className: '{display_class}' (Match: {'YES' if is_match else 'NO, expected ' + expected_str})"
            )
            if not is_match:
                logger.info(f"[{login}] RESULT: SKIPPED_WAVE | Skipping project & feedback checks (Wave mismatch)")
                return {
                    "login": login,
                    "status": "SKIPPED_WAVE",
                    "is_skipped": True,
                    "class_name": display_class,
                    "total_xp": 0,
                    "logtime": 0.0,
                    "accepted_projects_count": 0,
                    "suspicion_reasons": [f"Волна '{display_class}' не совпадает с целевой '{expected_str}'"],
                    "suspicion_reason_text": f"Пропущена волна: {display_class}",
                    "details": {"info": info},
                }
        else:
            logger.info(f"[{login}] GET /v1/participants/{login} -> Wave className: '{display_class}'")

        logtime = api_client.get_participant_logtime(login)
        xp_history = api_client.get_participant_xp_history(login)

        # Calculate total XP for reference
        total_xp = 0
        if isinstance(xp_history, list):
            for entry in xp_history:
                if isinstance(entry, dict):
                    raw_val = entry.get("expValue") or entry.get("value") or entry.get("xp") or entry.get("exp")
                    total_xp += _safe_int(raw_val)

        if total_xp == 0 and isinstance(info, dict):
            raw_val = info.get("expValue") or info.get("xp") or info.get("totalXp") or info.get("experience")
            total_xp = _safe_int(raw_val)

        # Check projects: count ACCEPTED projects among target_project_ids via GET /v1/participants/{login}/projects/{projectId}
        logger.info(f"[{login}] Checking {len(self.target_project_ids)} target projects status...")
        accepted_projects = []
        for pid in self.target_project_ids:
            proj_data = api_client.get_participant_project(login, pid)
            p_status = proj_data.get("status", "NOT_STARTED").upper() if isinstance(proj_data, dict) else "NOT_FOUND"
            title = proj_data.get("title") if isinstance(proj_data, dict) else str(pid)
            title = title or str(pid)
            logger.info(f"[{login}] GET project {pid} ({title}) -> Status: {p_status}")
            if p_status == "ACCEPTED":
                accepted_projects.append({"id": pid, "title": title})

        accepted_count = len(accepted_projects)
        logger.info(
            f"[{login}] Projects check complete: {accepted_count}/{len(self.target_project_ids)} ACCEPTED (Required min: {self.min_accepted_projects})"
        )

        # Fetch feedback data: /v1/participants/{login}/feedback
        feedback = api_client.get_participant_feedback(login)
        punctuality = _safe_float(feedback.get("averageVerifierPunctuality")) if isinstance(feedback, dict) else 0.0
        interest = _safe_float(feedback.get("averageVerifierInterest")) if isinstance(feedback, dict) else 0.0
        thoroughness = _safe_float(feedback.get("averageVerifierThoroughness")) if isinstance(feedback, dict) else 0.0
        friendliness = _safe_float(feedback.get("averageVerifierFriendliness")) if isinstance(feedback, dict) else 0.0

        has_feedback = (
            punctuality > 0 and interest > 0 and thoroughness > 0 and friendliness > 0
        )
        logger.info(
            f"[{login}] GET /v1/participants/{login}/feedback -> Punctuality: {punctuality:.2f}, Interest: {interest:.2f}, Thoroughness: {thoroughness:.2f}, Friendliness: {friendliness:.2f} (Valid: {'YES' if has_feedback else 'NO'})"
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
            f"[{login}] RESULT: {status} | Accepted Projects: {accepted_count}/{self.min_accepted_projects} | Feedback: {'Valid' if has_feedback else 'Invalid'} | Reason: {result['suspicion_reason_text']}"
        )
        return result


