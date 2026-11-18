"""A fake JSON API for provider tests: routes (method, path) to canned responses."""


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeApi:
    def __init__(self, base):
        self.base = base
        self.calls = []
        self.routes = {}

    def route(self, method, path, body, status_code=200):
        """body may be a dict, or an exception to raise (e.g. requests.Timeout)."""
        self.routes[(method, path)] = (status_code, body)

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        assert url.startswith(self.base), f"{url} is not under {self.base}"
        path = url[len(self.base):]
        self.calls.append({"method": method, "path": path, "params": params, "json": json, "headers": headers})
        if (method, path) not in self.routes:
            raise AssertionError(f"Unexpected call {method} {path}")
        status_code, body = self.routes[(method, path)]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(status_code, body)

    def calls_to(self, method, path):
        return [c for c in self.calls if c["method"] == method and c["path"] == path]
