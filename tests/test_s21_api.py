from unittest.mock import MagicMock, patch
from app.s21_api import S21ApiClient


@patch("requests.post")
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


@patch("requests.request")
@patch("requests.post")
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
