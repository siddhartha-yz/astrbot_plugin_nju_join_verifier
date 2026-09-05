from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin

import aiohttp

VerifyResult = Literal[
    "match",
    "mismatch",
    "exists",
    "not_found",
    "invalid",
    "rate_limited",
]
_ALLOWED_RESULTS = {
    "match",
    "mismatch",
    "exists",
    "not_found",
    "invalid",
    "rate_limited",
}


class VerifierError(RuntimeError):
    pass


class VerifierAuthenticationError(VerifierError):
    pass


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    csrf: str
    captcha_token: str
    captcha_answer: str


def _hidden_value(html: str, name: str) -> str:
    match = re.search(
        rf'<input[^>]+name=["\']{re.escape(name)}["\'][^>]+value=["\']([^"\']+)',
        html,
        re.IGNORECASE,
    )
    if not match:
        # Attribute order can differ.
        match = re.search(
            rf'<input[^>]+value=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
            html,
            re.IGNORECASE,
        )
    if not match:
        raise VerifierAuthenticationError(f"missing login field: {name}")
    return match.group(1)


def _solve_math(question: str) -> str:
    match = re.search(r"(-?\d+)\s*([+\-×x*/÷])\s*(-?\d+)", question)
    if not match:
        raise VerifierAuthenticationError("unsupported captcha")
    left, op, right = int(match.group(1)), match.group(2), int(match.group(3))
    if op == "+":
        value = left + right
    elif op == "-":
        value = left - right
    elif op in {"×", "x", "*"}:
        value = left * right
    elif op in {"/", "÷"}:
        if right == 0 or left % right:
            raise VerifierAuthenticationError("non-integral captcha")
        value = left // right
    else:  # pragma: no cover
        raise VerifierAuthenticationError("unsupported captcha")
    return str(value)


def parse_login_challenge(html: str) -> LoginChallenge:
    csrf = _hidden_value(html, "csrf")
    captcha_token = _hidden_value(html, "captcha_token")
    match = re.search(
        r'class=["\']captcha-question["\'][^>]*>(.*?)</',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise VerifierAuthenticationError("missing captcha question")
    question = re.sub(r"<[^>]+>", "", match.group(1))
    return LoginChallenge(csrf, captcha_token, _solve_math(question))


def parse_verify_csrf(html: str) -> str:
    patterns = (
        r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    raise VerifierAuthenticationError("missing verification csrf token")


class IdentityVerifier:
    """Session client for the university identity-check service.

    Credentials and cookies remain in memory. Raw student data is never logged by
    this class.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 20.0,
        min_interval_seconds: float = 1.5,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("verifier base_url must use https://")
        self.base_url = base_url.rstrip("/") + "/"
        self.username = username
        self.password = password
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._verify_csrf: str | None = None
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._verify_csrf = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"User-Agent": "AstrBot-NJU-Join-Verifier/0.4.1"},
            )
        return self._session

    async def _login(self) -> None:
        session = await self._ensure_session()
        login_url = urljoin(self.base_url, "login")
        try:
            async with session.get(login_url, allow_redirects=True) as response:
                if response.status != 200:
                    raise VerifierAuthenticationError(f"login page status {response.status}")
                challenge = parse_login_challenge(await response.text())

            form = {
                "csrf": challenge.csrf,
                "username": self.username,
                "password": self.password,
                "captcha_token": challenge.captcha_token,
                "captcha": challenge.captcha_answer,
            }
            async with session.post(login_url, data=form, allow_redirects=True) as response:
                html = await response.text()
                if response.status != 200 or response.url.path.rstrip("/").endswith("/login"):
                    raise VerifierAuthenticationError("login rejected")
                self._verify_csrf = parse_verify_csrf(html)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise VerifierError(
                f"verification network error: {type(exc).__name__}"
            ) from exc

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.min_interval_seconds - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = time.monotonic()

    async def verify(self, *, student_id: str, name: str) -> VerifyResult:
        async with self._lock:
            for attempt in range(2):
                if not self._verify_csrf:
                    await self._login()
                session = await self._ensure_session()
                await self._throttle()
                url = urljoin(self.base_url, "api/verify")
                payload = {
                    "csrf": self._verify_csrf,
                    "student_id": student_id,
                    "name": name,
                }
                try:
                    async with session.post(url, json=payload, allow_redirects=False) as response:
                        if response.status in {401, 403} or response.status in {
                            301,
                            302,
                            303,
                            307,
                            308,
                        }:
                            self._verify_csrf = None
                            if attempt == 0:
                                continue
                            raise VerifierAuthenticationError("verification session expired")
                        if response.status >= 500:
                            raise VerifierError(f"verification service status {response.status}")
                        try:
                            data = await response.json(content_type=None)
                        except Exception as exc:
                            raise VerifierError("verification service returned non-json") from exc
                except (TimeoutError, aiohttp.ClientError) as exc:
                    raise VerifierError(
                        f"verification network error: {type(exc).__name__}"
                    ) from exc

                result = data.get("result") if isinstance(data, dict) else None
                if result not in _ALLOWED_RESULTS:
                    raise VerifierError("verification service returned unknown result")
                return result  # type: ignore[return-value]

            raise VerifierAuthenticationError("verification login retry exhausted")
