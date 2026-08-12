from unittest.mock import MagicMock
from app.validator import PeerValidator


def test_validate_peer_verified():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "active_peer"}
    mock_api.get_participant_logtime.return_value = 14.5
    mock_api.get_participant_xp_history.return_value = [{"expValue": 1250}]
    mock_api.get_participant_feedback.return_value = {
        "averageVerifierPunctuality": 4.5,
        "averageVerifierInterest": 4.8,
        "averageVerifierThoroughness": 5.0,
        "averageVerifierFriendliness": 4.9,
    }

    # Mock projects to return ACCEPTED for 3 projects
    def mock_get_project(login, pid):
        if pid in (73187, 73188, 73189):
            return {"status": "ACCEPTED", "title": f"Project_{pid}"}
        return {"status": "REGISTERED"}

    mock_api.get_participant_project.side_effect = mock_get_project

    validator = PeerValidator(target_project_ids=[73187, 73188, 73189, 73328], min_accepted_projects=3)
    result = validator.validate_peer(mock_api, "active_peer")

    assert result["status"] == "VERIFIED"
    assert result["total_xp"] == 1250
    assert result["accepted_projects_count"] == 3
    assert len(result["suspicion_reasons"]) == 0


def test_validate_peer_suspicious():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "test_acc"}
    mock_api.get_participant_logtime.return_value = 0.0
    mock_api.get_participant_xp_history.return_value = []
    mock_api.get_participant_feedback.return_value = {
        "averageVerifierPunctuality": 0,
        "averageVerifierInterest": 0,
        "averageVerifierThoroughness": 0,
        "averageVerifierFriendliness": 0,
    }

    # Mock projects to return ACCEPTED for only 1 project
    def mock_get_project(login, pid):
        if pid == 73187:
            return {"status": "ACCEPTED", "title": "D01T01"}
        return {"status": "REGISTERED"}

    mock_api.get_participant_project.side_effect = mock_get_project

    validator = PeerValidator(target_project_ids=[73187, 73188, 73189, 73328], min_accepted_projects=3)
    result = validator.validate_peer(mock_api, "test_acc")

    assert result["status"] == "SUSPICIOUS"
    assert result["accepted_projects_count"] == 1
    assert len(result["suspicion_reasons"]) > 0


