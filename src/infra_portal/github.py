"""The two GitHub calls the OAuth flow needs.

The access token is used once to read the user's public profile and is then
dropped. The app never stores it and asks for no scopes.
"""

from dataclasses import dataclass

import httpx

from infra_portal.config import Settings, get_settings

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"


class GitHubError(Exception):
    pass


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str
    name: str | None
    avatar_url: str | None


class GitHubClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=10, transport=self._transport)

    def exchange_code(self, code: str) -> str:
        """Trade the one-time code from the callback for an access token."""
        with self._client() as client:
            response = client.post(
                TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._settings.github_client_id,
                    "client_secret": self._settings.github_client_secret,
                    "code": code,
                    "redirect_uri": self._settings.github_callback_url,
                },
            )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            # GitHub answers 200 with an "error" field for a bad or reused code.
            raise GitHubError(payload.get("error_description") or "no access token returned")
        return token

    def fetch_user(self, token: str) -> GitHubUser:
        with self._client() as client:
            response = client.get(
                USER_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                },
            )
        response.raise_for_status()
        data = response.json()
        return GitHubUser(
            id=int(data["id"]),
            login=str(data["login"]),
            name=data.get("name"),
            avatar_url=data.get("avatar_url"),
        )


def get_github_client() -> GitHubClient:
    """FastAPI dependency. Tests replace it with a fake."""
    return GitHubClient(get_settings())
