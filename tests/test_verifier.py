import pytest

from nju_join_verifier.verifier import (
    VerifierAuthenticationError,
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
