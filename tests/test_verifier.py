import asyncio

import pytest

from nju_join_verifier.verifier import (
    IdentityVerifier,
    VerifierAuthenticationError,
    VerifierError,
    parse_login_challenge,
    parse_verify_csrf,
)


def test_login_challenge():
    html = '<input name="csrf" value="a"><input name="captcha_token" value="b"><span class="captcha-question">5 x 16 =</span>'
    c = parse_login_challenge(html)
    assert (c.csrf, c.captcha_token, c.captcha_answer) == ("a", "b", "80")


def test_verify_csrf():
    assert parse_verify_csrf('<meta name="csrf-token" content="v">') == "v"


def test_bad_captcha():
    html = '<input name="csrf" value="a"><input name="captcha_token" value="b"><span class="captcha-question">?</span>'
    with pytest.raises(VerifierAuthenticationError):
        parse_login_challenge(html)


class TimeoutContext:
    async def __aenter__(self):
        raise TimeoutError

    async def __aexit__(self, exc_type, exc, tb):
        return False


class LoginTimeoutSession:
    def get(self, *args, **kwargs):
        return TimeoutContext()


class VerifyTimeoutSession:
    def post(self, *args, **kwargs):
        return TimeoutContext()


def _verifier() -> IdentityVerifier:
    return IdentityVerifier(
        base_url="https://example.test",
        username="user",
        password="password",
        min_interval_seconds=0,
    )


def test_login_timeout_becomes_verifier_error():
    verifier = _verifier()

    async def ensure_session():
        return LoginTimeoutSession()

    verifier._ensure_session = ensure_session  # type: ignore[method-assign]
    with pytest.raises(VerifierError, match="TimeoutError"):
        asyncio.run(verifier.verify(student_id="12345678", name="张三"))


def test_verify_timeout_becomes_verifier_error():
    verifier = _verifier()
    verifier._verify_csrf = "csrf"

    async def ensure_session():
        return VerifyTimeoutSession()

    verifier._ensure_session = ensure_session  # type: ignore[method-assign]
    with pytest.raises(VerifierError, match="TimeoutError"):
        asyncio.run(verifier.verify(student_id="12345678", name="张三"))
