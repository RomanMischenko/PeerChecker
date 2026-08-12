from unittest.mock import MagicMock
from app.validator import PeerValidator


def test_validate_peer_verified():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "active_peer", "expValue": 1250}
    mock_api.get_participant_logtime.return_value = 14.5
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


def test_validate_peer_skipped_wave():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "other_wave_peer", "className": "24_01_NN"}

    validator = PeerValidator(target_class_names=["25_10_NN"])
    result = validator.validate_peer(mock_api, "other_wave_peer")

    assert result["status"] == "SKIPPED_PEERS"
    assert result["is_skipped"] is True
    # Ensure no project status API calls were made
    mock_api.get_participant_project.assert_not_called()
    mock_api.get_participant_feedback.assert_not_called()


def test_validate_peer_null_feedback():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "null_fb_peer"}
    mock_api.get_participant_logtime.return_value = 5.0
    mock_api.get_participant_xp_history.return_value = None
    mock_api.get_participant_feedback.return_value = {
        "averageVerifierPunctuality": None,
        "averageVerifierInterest": None,
        "averageVerifierThoroughness": None,
        "averageVerifierFriendliness": None,
    }
    mock_api.get_participant_project.return_value = {"status": "ACCEPTED"}

    validator = PeerValidator(target_project_ids=[73187, 73188, 73189], min_accepted_projects=3)
    result = validator.validate_peer(mock_api, "null_fb_peer")

    assert result["status"] == "SUSPICIOUS"
    assert "Оценки фидбека проверяющего равны 0" in result["suspicion_reason_text"]


def test_validate_peer_none_class_name():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "no_class_peer", "className": None}

    validator = PeerValidator(target_class_names=["26_08_NN"])
    result = validator.validate_peer(mock_api, "no_class_peer")

    assert result["status"] == "SKIPPED_PEERS"
    assert result["class_name"] == "Не указана"
    assert "Волна 'Не указана'" in result["suspicion_reasons"][0]


def test_validate_peer_wave_projects_unconfigured():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "unconf_peer", "className": "26_11_NN"}

    wave_projects = {"26_04_NN": [73187, 73188]}
    validator = PeerValidator(wave_projects=wave_projects)
    result = validator.validate_peer(mock_api, "unconf_peer")

    assert result["status"] == "SKIPPED_PEERS"
    assert result["is_skipped"] is True
    assert "не имеет настроенных проектов" in result["suspicion_reasons"][0]


def test_validate_peer_project_api_error():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "err_peer", "className": "26_04_NN"}
    mock_api.get_participant_project.return_value = {"_error": "HTTP 404 error"}

    wave_projects = {"26_04_NN": [73187]}
    validator = PeerValidator(wave_projects=wave_projects)
    result = validator.validate_peer(mock_api, "err_peer")

    assert result["status"] == "SKIPPED_PEERS"
    assert result["is_skipped"] is True
    assert "Ошибка API при проверке проекта" in result["suspicion_reasons"][0]




