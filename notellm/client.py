from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .ir import OnbConfig


# ── Error Hierarchy ────────────────────────────────────────────────────────

class OnbError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class OnbAuthError(OnbError):
    pass


class OnbTransientError(OnbError):
    pass


class OnbNotFoundError(OnbError):
    pass


# ── Client ─────────────────────────────────────────────────────────────────

class OnbClient:
    def __init__(self, config: OnbConfig) -> None:
        self.config = config
        self._token: Optional[str] = None
        self._cache: dict[str, tuple[float, Any]] = {}

    def _ensure_token(self) -> str:
        if self._token:
            return self._token
        if self.config.password:
            self._token = self.config.password
            return self._token
        return ""

    # ── HTTP ───────────────────────────────────────────────────────────────

    def get(self, path: str, timeout: Optional[int] = None) -> Any:
        return self._call("GET", path, timeout=timeout)

    def post(self, path: str, body: Optional[dict] = None,
             timeout: Optional[int] = None, data: Optional[bytes] = None,
             ct: str = "application/json") -> Any:
        return self._call("POST", path, body=body, timeout=timeout, data=data, ct=ct)

    def delete(self, path: str, timeout: Optional[int] = None) -> Any:
        return self._call("DELETE", path, timeout=timeout)

    def _call(self, method: str, path: str, body: Optional[dict] = None,
              timeout: Optional[int] = None, data: Optional[bytes] = None,
              ct: str = "application/json") -> Any:
        t = timeout or self.config.tool_timeout
        url = f"{self.config.api_url}{path}"
        headers = {"Content-Type": ct}
        token = self._ensure_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        last_error: Optional[Exception] = None
        max_retries = self.config.max_retries

        for attempt in range(1, max_retries + 1):
            try:
                if method == "GET":
                    req = urllib.request.Request(url, headers=headers, method="GET")
                elif method == "DELETE":
                    req = urllib.request.Request(url, headers=headers, method="DELETE")
                else:
                    payload = data if data else (json.dumps(body).encode() if body else None)
                    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

                with urllib.request.urlopen(req, timeout=t) as resp:
                    raw = resp.read().decode()
                    if not raw:
                        return {}
                    return json.loads(raw)

            except urllib.error.HTTPError as e:
                body_text = e.read().decode()[:500]
                status = e.code
                try:
                    detail = json.loads(body_text).get("detail", body_text)
                except Exception:
                    detail = body_text

                if status == 401:
                    raise OnbAuthError(f"Auth failed: {detail}", status=status, body=body_text)
                elif status == 404:
                    raise OnbNotFoundError(f"Not found: {detail}", status=status, body=body_text)
                elif status in (429, 502, 503, 504) and attempt < max_retries:
                    time.sleep(self.config.retry_backoff * (2 ** (attempt - 1)))
                    last_error = OnbTransientError(detail, status=status, body=body_text)
                    continue
                else:
                    raise OnbError(f"HTTP {status}: {detail}", status=status, body=body_text)

            except urllib.error.URLError as e:
                reason = str(e.reason) if hasattr(e, 'reason') else str(e)
                if attempt < max_retries:
                    time.sleep(self.config.retry_backoff * (2 ** (attempt - 1)))
                    last_error = OnbTransientError(reason)
                    continue
                raise OnbError(f"Connection failed after {max_retries} retries: {reason}")

            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise OnbError(f"Response parse error: {e}")

            except OSError as e:
                if attempt < max_retries:
                    time.sleep(self.config.retry_backoff * (2 ** (attempt - 1)))
                    last_error = OnbTransientError(str(e))
                    continue
                raise OnbError(f"OS error after {max_retries} retries: {e}")

        raise OnbError(
            f"Request failed after {max_retries} retries: {last_error}" if last_error else "Unknown error"
        )

    # ── Caching ────────────────────────────────────────────────────────────

    def cached(self, key: str, ttl: int, fetcher) -> Any:
        now = time.time()
        if key in self._cache and (now - self._cache[key][0]) < ttl:
            return self._cache[key][1]
        val = fetcher()
        self._cache[key] = (now, val)
        return val

    def invalidate_cache(self, key: str = "") -> None:
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()
