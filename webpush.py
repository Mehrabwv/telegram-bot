"""Windows notifications for the dashboard app (Web Push), with no outside service.

How it works: when you turn on notifications in the app, Edge (or Chrome) gives
the app a private "push address" at Microsoft's (or Google's) push service. The
bot encrypts each notification for that browser only (RFC 8291) and signs it with
its own key (VAPID, RFC 8292), then posts it there. Windows shows it even when the
app's window is closed.

Kept off GitHub (see .gitignore):
- dashboard_keys.json: the bot's private signing key
- dashboard_subscriptions.json: each device's push address
"""

import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FOLDER = Path(__file__).resolve().parent
KEYS_FILE = FOLDER / "dashboard_keys.json"
SUBSCRIPTIONS_FILE = FOLDER / "dashboard_subscriptions.json"
# Push services want a contact for the sender; the project's page is enough
CONTACT = "https://github.com/Mehrabwv/telegram-bot"
TTL_SECONDS = 12 * 3600  # a device that's off gets the notification within 12 hours, else never
RECORD_SIZE = 4096


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_bytes(key):
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


# =====================================
# THE BOT'S SIGNING KEY (VAPID)
# =====================================

_key = None


def signing_key():
    """The bot's own key, made the first time and kept in KEYS_FILE."""
    global _key
    if _key is None:
        try:
            pem = json.loads(KEYS_FILE.read_text(encoding="utf-8"))["private_key"]
            _key = serialization.load_pem_private_key(pem.encode(), password=None)
        except (FileNotFoundError, ValueError, KeyError):
            _key = ec.generate_private_key(ec.SECP256R1())
            pem = _key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode()
            KEYS_FILE.write_text(json.dumps({"private_key": pem}), encoding="utf-8")
    return _key


def public_key():
    """What the app needs to subscribe (base64url)."""
    return b64url(public_bytes(signing_key()))


def vapid_header(endpoint):
    """'vapid t=<signed token>, k=<public key>' for one push service."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600,
              "sub": CONTACT}
    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signed = f"{header}.{body}".encode()
    r, s = decode_dss_signature(signing_key().sign(signed, ec.ECDSA(hashes.SHA256())))
    token = f"{header}.{body}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={public_key()}"


# =====================================
# ENCRYPTION (aes128gcm, RFC 8291)
# =====================================

def encrypt(payload, browser_key, auth_secret):
    """payload (bytes) encrypted for one browser."""
    browser_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), browser_key)
    ours = ec.generate_private_key(ec.SECP256R1())  # a new one for every message
    our_public = public_bytes(ours)
    shared = ours.exchange(ec.ECDH(), browser_public)

    key_info = b"WebPush: info\x00" + browser_key + our_public
    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=key_info).derive(shared)
    salt = os.urandom(16)
    content_key = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)

    ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)  # \x02: the last record
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(our_public)]) + our_public
    return header + ciphertext


# =====================================
# DEVICES
# =====================================

def load_subscriptions():
    try:
        subscriptions = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return subscriptions if isinstance(subscriptions, list) else []


def save_subscriptions(subscriptions):
    SUBSCRIPTIONS_FILE.write_text(json.dumps(subscriptions, indent=1), encoding="utf-8")


def valid_subscription(subscription):
    """The browser's {endpoint, keys: {p256dh, auth}}, checked; None if it isn't one."""
    try:
        endpoint = subscription["endpoint"]
        keys = subscription["keys"]
        if not endpoint.startswith("https://") or len(endpoint) > 2000:
            return None
        if len(unb64url(keys["p256dh"])) != 65 or len(unb64url(keys["auth"])) != 16:
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}


def add_subscription(subscription, device=""):
    subscription = valid_subscription(subscription)
    if subscription is None:
        return False
    subscriptions = [s for s in load_subscriptions() if s["endpoint"] != subscription["endpoint"]]
    subscription["device"] = str(device)[:60]
    subscription["added"] = time.strftime("%Y-%m-%dT%H:%M")
    save_subscriptions(subscriptions + [subscription])
    return True


def remove_subscription(endpoint):
    subscriptions = load_subscriptions()
    kept = [s for s in subscriptions if s["endpoint"] != endpoint]
    if len(kept) != len(subscriptions):
        save_subscriptions(kept)


def device_count():
    return len(load_subscriptions())


async def send(message, urgent=False, endpoint=None):
    """Send {title, body, url, tag} to every device (or only the one with this
    push address). Returns how many got it. Devices whose push address expired
    (the app was uninstalled, notifications turned off) are forgotten."""
    subscriptions = [s for s in load_subscriptions() if endpoint is None or s["endpoint"] == endpoint]
    if not subscriptions:
        return 0
    payload = json.dumps(message, ensure_ascii=False).encode()
    if len(payload) > 3000:  # push services allow about 4 KB
        message = {**message, "body": message.get("body", "")[:1000]}
        payload = json.dumps(message, ensure_ascii=False).encode()

    delivered, gone = 0, []
    async with httpx.AsyncClient(timeout=15) as client:
        for subscription in subscriptions:
            try:
                body = encrypt(payload, unb64url(subscription["keys"]["p256dh"]),
                               unb64url(subscription["keys"]["auth"]))
                response = await client.post(subscription["endpoint"], content=body, headers={
                    "Authorization": vapid_header(subscription["endpoint"]),
                    "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream",
                    "TTL": str(TTL_SECONDS),
                    "Urgency": "high" if urgent else "normal",
                })
            except (httpx.HTTPError, ValueError, KeyError) as error:
                print(f"Notification to {subscription.get('device') or 'a device'} failed: {type(error).__name__}")
                continue
            if response.status_code in (404, 410):
                gone.append(subscription["endpoint"])
            elif response.status_code >= 400:
                print(f"Notification to {subscription.get('device') or 'a device'} refused: HTTP {response.status_code}")
            else:
                delivered += 1
    for endpoint in gone:
        remove_subscription(endpoint)
    if gone:
        print(f"Forgot {len(gone)} device(s) whose notifications were turned off.")
    return delivered
