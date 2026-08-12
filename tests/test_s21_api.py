from unittest.mock import MagicMock, patch
from app.s21_api import S21ApiClient, S21ApiError


@patch("requests.Session.post")
def test_authenticate_success(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "mocked_bearer_token",
        "expires_in": 3600,
    }
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = S21ApiClient("dummy_login", "dummy_pass")
    token = client.authenticate()

    assert token == "mocked_bearer_token"
    assert client.access_token == "mocked_bearer_token"


@patch("requests.Session.request")
@patch("requests.Session.post")
def test_get_coalition_participants_pagination(mock_post, mock_request):
    # Auth mock
    mock_auth_resp = MagicMock()
    mock_auth_resp.json.return_value = {"access_token": "mocked_token", "expires_in": 3600}
    mock_post.return_value = mock_auth_resp

    # API response mock with 2 pages
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.json.return_value = ["peer1", "peer2"]

    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.json.return_value = []

    mock_request.side_effect = [mock_resp1, mock_resp2]

    client = S21ApiClient("login", "pass", request_delay=0.0)
    logins = client.get_coalition_participants(604)

    assert logins == ["peer1", "peer2"]


@patch("requests.Session.request")
@patch("requests.Session.post")
def test_retry_on_500_error(mock_post, mock_request):
    mock_auth_resp = MagicMock()
    mock_auth_resp.json.return_value = {"access_token": "mocked_token", "expires_in": 3600}
    mock_post.return_value = mock_auth_resp

    mock_resp_500 = MagicMock()
    mock_resp_500.status_code = 500

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"className": "26_08_NN"}

    mock_request.side_effect = [mock_resp_500, mock_resp_200]

    client = S21ApiClient("login", "pass", request_delay=0.0)
    info = client.get_participant_info("peer1")

    assert info == {"className": "26_08_NN"}
    assert mock_request.call_count == 2


@patch("requests.Session.post")
def test_authenticate_null_expires_in(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "token_123",
        "expires_in": None,
    }
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = S21ApiClient("login", "pass")
    token = client.authenticate()
    assert token == "token_123"
    assert client.token_expires_at > 0


def test_s21_api_context_manager():
    with S21ApiClient("login", "pass") as client:
        assert client is not None
        mock_session = MagicMock()
        client.session = mock_session
    mock_session.close.assert_called_once()


@patch("requests.Session.request")
@patch("requests.Session.post")
def test_fast_fail_on_404_error(mock_post, mock_request):
    import pytest
    import requests

    mock_auth_resp = MagicMock()
    mock_auth_resp.json.return_value = {"access_token": "mocked_token", "expires_in": 3600}
    mock_post.return_value = mock_auth_resp

    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_resp_404.text = "Not found"
    mock_resp_404.raise_for_status.side_effect = requests.HTTPError("404 Not Found", response=mock_resp_404)

    mock_request.return_value = mock_resp_404

    client = S21ApiClient("login", "pass", request_delay=0.0, max_retries=3)
    with pytest.raises(S21ApiError, match="HTTP 404 error"):
        client.get_participant_info("unknown_peer")

    assert mock_request.call_count == 1


