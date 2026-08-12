from unittest.mock import MagicMock
from app.validator import PeerValidator


def test_validate_peer_verified():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "active_peer"}
    mock_api.get_participant_logtime.return_value = 14.5
    mock_api.get_participant_xp_history.return_value = [{"value": 1250}]
    mock_api.get_participant_points.return_value = {"points": 5}

    validator = PeerValidator(min_xp=0, min_logtime=0.0)
    result = validator.validate_peer(mock_api, "active_peer")

    assert result["status"] == "VERIFIED"
    assert result["total_xp"] == 1250
    assert result["logtime"] == 14.5
    assert len(result["suspicion_reasons"]) == 0


def test_validate_peer_suspicious():
    mock_api = MagicMock()
    mock_api.get_participant_info.return_value = {"login": "test_acc"}
    mock_api.get_participant_logtime.return_value = 0.0
    mock_api.get_participant_xp_history.return_value = []
    mock_api.get_participant_points.return_value = {}

    validator = PeerValidator(min_xp=0, min_logtime=0.0)
    result = validator.validate_peer(mock_api, "test_acc")

    assert result["status"] == "SUSPICIOUS"
    assert result["total_xp"] == 0
    assert result["logtime"] == 0.0
    assert len(result["suspicion_reasons"]) > 0
