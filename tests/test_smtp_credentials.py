"""The digest email died on the SMTP login, not the body or the recipients.

Four mornings running, the daily sync reported:

    ! Email send failed: 'ascii' codec can't encode character '\\xa0' in
      position 30: ordinal not in range(128)

while Slack went out fine, so nobody saw the digest was missing. Two earlier
fixes went at the message body and then the recipient list, because the error
names neither. It was neither. smtplib's AUTH PLAIN sends the login as one
string, "\\0user\\0password", and ascii-encodes the lot (smtplib.SMTP.auth,
via auth_plain), so an invisible character in *either* field fails the whole
send with an offset measured across both.

Position 30 puts it just past a 24-character login address, which is where the
first group separator of a Gmail app password lands. Google displays those
passwords as four groups of four for readability and ignores the separators on
submission, so pasting one in with non-breaking spaces is enough to do this.

The fake SMTP below reproduces smtplib's own ascii encoding rather than
standing in for it, so these tests fail if the fix is removed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
import notifier

NBSP = "\xa0"
APP_PASSWORD = f"abcd{NBSP}efgh{NBSP}ijkl{NBSP}mnop"   # as pasted from Google


class FakeSMTP:
    """Enough of smtplib to reproduce the real failure mode."""

    last = None

    def __init__(self, host, port):
        self.sent = []
        FakeSMTP.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, user, password):
        # smtplib.SMTP.auth() does exactly this with auth_plain's output.
        ("\0%s\0%s" % (user, password)).encode("ascii")
        self.user = user
        self.password = password

    def send_message(self, msg, to_addrs=None):
        for addr in (to_addrs or []):
            addr.encode("ascii")          # smtplib.sendmail encodes each one
        self.sent.append((msg, to_addrs))


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setattr(notifier.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "SMTP_FROM", "coaching@jstcompete.co.uk")
    monkeypatch.setattr(config, "SMTP_USER", "")
    monkeypatch.setattr(config, "SMTP_PASSWORD", APP_PASSWORD)
    monkeypatch.setattr(config, "SMTP_TO", "jak@jstcompete.co.uk")
    return FakeSMTP


# ── the bug itself ────────────────────────────────────────────────────────────

def test_the_original_failure_is_real():
    """Guard the premise: an uncleaned pair really does fail this way."""
    with pytest.raises(UnicodeEncodeError) as e:
        FakeSMTP("h", 1).login("coaching@jstcompete.co.uk", APP_PASSWORD)
    assert e.value.reason.startswith("ordinal not in range")
    assert "\xa0" == e.value.object[e.value.start]


def test_position_30_is_the_login_pair_not_the_recipients():
    """The offset in the production log can only come from user+password."""
    user = "u" * 24                    # what puts the first separator at 30
    with pytest.raises(UnicodeEncodeError) as e:
        ("\0%s\0%s" % (user, APP_PASSWORD)).encode("ascii")
    assert e.value.start == 30


def test_the_digest_now_sends(smtp):
    notifier.send_email("JST Compete Coaching Digest", "£1,234 of MRR — all fine")
    assert len(FakeSMTP.last.sent) == 1


def test_the_app_password_separators_are_dropped(smtp):
    notifier.send_email("subject", "body")
    assert FakeSMTP.last.password == "abcdefghijklmnop"


def test_a_plain_space_is_dropped_too(smtp, monkeypatch):
    """Google's own UI shows the password with ordinary spaces."""
    monkeypatch.setattr(config, "SMTP_PASSWORD", "abcd efgh ijkl mnop")
    notifier.send_email("subject", "body")
    assert FakeSMTP.last.password == "abcdefghijklmnop"


def test_an_invisible_character_in_the_login_user_is_cleaned(smtp, monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", f"coaching@jstcompete.co.uk{NBSP}")
    notifier.send_email("subject", "body")
    assert FakeSMTP.last.user == "coaching@jstcompete.co.uk"


# ── when it cannot be cleaned, say which field ────────────────────────────────

def test_an_unusable_password_names_the_field_not_the_value(monkeypatch):
    monkeypatch.setattr(config, "SMTP_PASSWORD", "abcd€fgh")
    with pytest.raises(ValueError) as e:
        notifier._smtp_credentials(config.SMTP_FROM, config.SMTP_PASSWORD)
    assert "SMTP_PASSWORD" in str(e.value)
    assert "abcd" not in str(e.value)      # never print a password
    assert "€" not in str(e.value)


def test_an_unusable_user_names_that_field_instead(monkeypatch):
    # SMTP_USER wins over SMTP_FROM when set (alias sending), so clear it or
    # this asserts nothing on a machine that configures one.
    monkeypatch.setattr(config, "SMTP_USER", "")
    with pytest.raises(ValueError) as e:
        notifier._smtp_credentials("coach€@jstcompete.co.uk", "abcdefghijklmnop")
    assert "SMTP_USER/SMTP_FROM" in str(e.value)


def test_the_error_gives_an_offset_to_find_it_by(monkeypatch):
    monkeypatch.setattr(config, "SMTP_USER", "")
    with pytest.raises(ValueError) as e:
        notifier._smtp_credentials("a@b.co", "abcdefgh€ijkl")
    assert "offset 8" in str(e.value)


# ── every send path, not just the digest ──────────────────────────────────────

def test_the_per_athlete_path_is_covered(smtp):
    notifier._send_email_to(config.SMTP_FROM, APP_PASSWORD,
                            "athlete@example.com", "subject", "body")
    assert FakeSMTP.last.password == "abcdefghijklmnop"


def test_the_html_path_is_covered(smtp):
    notifier._send_html_email_to(config.SMTP_FROM, APP_PASSWORD,
                                 "athlete@example.com", "subject", "text", "<p>html</p>")
    assert FakeSMTP.last.password == "abcdefghijklmnop"


def test_the_html_path_cleans_its_recipient(smtp):
    """It used to pass the raw address straight to smtplib."""
    notifier._send_html_email_to(config.SMTP_FROM, APP_PASSWORD,
                                 f"athlete@example.com{NBSP}", "s", "t", "<p>h</p>")
    assert FakeSMTP.last.sent[0][1] == ["athlete@example.com"]


# ── things that must keep working ─────────────────────────────────────────────

def test_a_utf8_body_still_goes_out_intact(smtp):
    notifier.send_email("subject", "Chloé lifted £100 — 90kg × 3")
    msg = FakeSMTP.last.sent[0][0]
    assert "Chloé" in msg.get_content()


def test_recipient_cleaning_still_applies(smtp, monkeypatch):
    monkeypatch.setattr(config, "SMTP_TO", f"jak@jstcompete.co.uk,{NBSP}ed@jstcompete.co.uk")
    notifier.send_email("subject", "body")
    assert FakeSMTP.last.sent[0][1] == ["jak@jstcompete.co.uk", "ed@jstcompete.co.uk"]
