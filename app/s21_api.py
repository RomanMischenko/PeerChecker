import logging
import time
from typing import Any
import requests

logger = logging.getLogger(__name__)

AUTH_URL = "https://auth.21-school.ru/auth/realms/EduPowerKeycloak/protocol/openid-connect/token"
BASE_URL = "https://platform.21-school.ru/services/21-school/api"


class S21ApiError(Exception):
    """Base exception for School 21 API errors."""
    pass


class S21ApiClient:
    def __init__(
        self,
        login: str,
        password: str,
        base_url: str = BASE_URL,
        auth_url: str = AUTH_URL,
        request_delay: float = 0.3,
        max_retries: int = 3,
    ):
        self.login = login
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.access_token: str | None = None
        self.token_expires_at: float = 0.0
        self.session = requests.Session()

    def close(self) -> None:
        """Close underlying requests Session."""
        if self.session:
            self.session.close()

    def __enter__(self) -> "S21ApiClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def authenticate(self) -> str:
        """Authenticate against Keycloak and retrieve bearer token."""
        payload = {
            "client_id": "s21-open-api",
            "username": self.login,
            "password": self.password,
            "grant_type": "password",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        logger.info("Authenticating with S21 Keycloak...")
        try:
            resp = self.session.post(self.auth_url, data=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            raw_expires = data.get("expires_in")
            try:
                expires_in_val = float(raw_expires) if raw_expires is not None else 300.0
            except (ValueError, TypeError):
                expires_in_val = 300.0

            if not token:
                raise S21ApiError("No access_token found in Keycloak response.")

            self.access_token = token
            # Expire slightly earlier than TTL to be safe
            buffer_sec = min(30.0, max(5.0, expires_in_val / 2))
            self.token_expires_at = time.time() + expires_in_val - buffer_sec
            logger.info("Successfully authenticated with S21 Keycloak.")
            return token
        except requests.RequestException as e:
            logger.error(f"Authentication failed: {e}")
            raise S21ApiError(f"Failed to authenticate with Keycloak: {e}") from e

    def _ensure_token(self) -> str:
        """Ensure we have a valid token."""
        if not self.access_token or time.time() >= self.token_expires_at:
            return self.authenticate()
        return self.access_token

    def _request(self, method: str, endpoint: str, params: dict | None = None) -> Any:
        """Execute HTTP request with rate-limiting delay, 401 token refresh, and 429/5xx exponential backoff."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        for attempt in range(1, self.max_retries + 1):
            token = self._ensure_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }

            if self.request_delay > 0:
                time.sleep(self.request_delay)

            try:
                resp = self.session.request(method, url, headers=headers, params=params, timeout=20)

                # Handle 401 Unauthorized -> Force token refresh on next retry
                if resp.status_code == 401:
                    logger.warning("Token expired or unauthorized (401). Refreshing token...")
                    self.access_token = None
                    if attempt < self.max_retries:
                        continue

                # Handle 429 Too Many Requests or 5xx Server Errors -> Exponential backoff retry
                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt < self.max_retries:
                        wait_time = (2 ** attempt) * 0.5
                        logger.warning(
                            f"HTTP {resp.status_code} error for {endpoint}. Retrying in {wait_time:.1f}s (attempt {attempt}/{self.max_retries})..."
                        )
                        time.sleep(wait_time)
                        continue

                resp.raise_for_status()
                if resp.status_code == 204:
                    return None
                return resp.json()

            except requests.HTTPError as e:
                logger.error(f"HTTP error {resp.status_code} for {endpoint}: {resp.text}")
                raise S21ApiError(f"HTTP {resp.status_code} error: {e}") from e
            except requests.RequestException as e:
                if attempt == self.max_retries:
                    logger.error(f"Request exception for {endpoint}: {e}")
                    raise S21ApiError(f"Network error: {e}") from e
                wait_time = (2 ** attempt) * 0.5
                logger.warning(f"Network exception ({e}) for {endpoint}. Retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)

        raise S21ApiError(f"Failed request to {endpoint} after {self.max_retries} attempts.")

    def get_coalition_participants(self, coalition_id: int, stop_event: Any = None) -> list[str]:
        """
        Fetch all participant logins in a coalition using pagination (limit/offset).
        Endpoint: /v1/coalitions/{coalitionId}/participants
        """
        logins: list[str] = []
        limit = 1000
        offset = 0

        while True:
            if stop_event and stop_event.is_set():
                logger.info("Coalition participants fetch cancelled by stop event.")
                break

            endpoint = f"/v1/coalitions/{coalition_id}/participants"
            params = {"limit": limit, "offset": offset}
            logger.info(f"Fetching coalition {coalition_id} participants (limit={limit}, offset={offset})...")
            data = self._request("GET", endpoint, params=params)


            # Response can be list of logins/dicts or dict with 'logins'/'participants' key
            batch = []
            if isinstance(data, list):
                batch = data
            elif isinstance(data, dict):
                batch = data.get("logins") or data.get("participants") or data.get("items") or []

            if not batch:
                break

            for item in batch:
                if isinstance(item, str):
                    logins.append(item)
                elif isinstance(item, dict):
                    login = item.get("login") or item.get("username")
                    if login:
                        logins.append(login)

            if len(batch) < limit:
                break

            offset += limit

        logger.info(f"Retrieved total {len(logins)} participants for coalition {coalition_id}.")
        return logins

    def get_participant_info(self, login: str) -> dict[str, Any]:
        """Fetch basic participant info. Endpoint: /v1/participants/{login}"""
        data = self._request("GET", f"/v1/participants/{login}")
        return data if isinstance(data, dict) else {}

    def get_participant_logtime(self, login: str) -> float:
        """Fetch average weekly logtime hours. Endpoint: /v1/participants/{login}/logtime"""
        try:
            data = self._request("GET", f"/v1/participants/{login}/logtime")
            if isinstance(data, (int, float)):
                return float(data)
            if isinstance(data, dict):
                val = data.get("logtimeWeeklyAvgHours") or data.get("logtime") or data.get("value")
                if val is not None:
                    return float(val)
            return 0.0
        except S21ApiError as e:
            logger.warning(f"Could not fetch logtime for {login}: {e}")
            return 0.0

    def get_participant_project(self, login: str, project_id: int) -> dict[str, Any]:
        """Fetch participant project status by ID. Endpoint: /v1/participants/{login}/projects/{projectId}"""
        try:
            data = self._request("GET", f"/v1/participants/{login}/projects/{project_id}")
            return data if isinstance(data, dict) else {}
        except S21ApiError as e:
            logger.debug(f"Could not fetch project {project_id} for {login}: {e}")
            return {"_error": str(e)}

    def get_participant_feedback(self, login: str) -> dict[str, Any]:
        """Fetch average participant feedback points. Endpoint: /v1/participants/{login}/feedback"""
        try:
            data = self._request("GET", f"/v1/participants/{login}/feedback")
            return data if isinstance(data, dict) else {}
        except S21ApiError as e:
            logger.debug(f"Could not fetch feedback for {login}: {e}")
            return {}


