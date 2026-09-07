"""Gate-Freigaben an die aktuelle Passphrase und den privaten Signierschlüssel binden."""

from django.conf import settings
from django.utils.crypto import constant_time_compare, salted_hmac


def passphrase_session_token():
    # Kein Klartext-Secret in signierten (aber nicht verschlüsselten) Cookies.
    return salted_hmac(
        "trading.passphrase_gate", settings.PASSPHRASE, algorithm="sha256"
    ).hexdigest()


def is_passphrase_verified(session):
    token = session.get("passphrase_verified")
    # Alte boolesche Freigaben werden beim Upgrade ebenfalls ungültig.
    return isinstance(token, str) and constant_time_compare(token, passphrase_session_token())
