"""Small JSON API client shared by the payment providers."""
import requests

from payments import ProviderError, ProviderUnavailable


class JsonApi:
    label = "API"

    def __init__(self, base_url, secret_key, http=None):
        self.base_url = base_url.rstrip("/")
        self.secret_key = secret_key
        self.http = http or requests.Session()

    def _ok(self, status_code, body) -> bool:
        raise NotImplementedError

    def _request(self, method, path, **kwargs):
        """Returns the parsed JSON body.

        Raises ProviderUnavailable when the outcome is unknown (network error,
        timeout, 5xx) and ProviderError when the API rejected the request.
        """
        try:
            resp = self.http.request(
                method,
                self.base_url + path,
                headers={"Authorization": f"Bearer {self.secret_key}"},
                timeout=20,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ProviderUnavailable(f"{self.label} {method} {path} failed: {exc}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"{self.label} {method} {path}: HTTP {resp.status_code}")
        if resp.status_code >= 400 or not self._ok(resp.status_code, body):
            raise ProviderError(body.get("message") or f"{self.label} {method} {path}: HTTP {resp.status_code}")
        return body


def to_minor_units(amount) -> int:
    """Naira -> kobo, cedis -> pesewas. Amounts are already rounded to 2 places."""
    return int((amount * 100).to_integral_value())
