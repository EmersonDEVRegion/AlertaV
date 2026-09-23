"""Web Push sin intermediarios: cifrado RFC 8291, firma VAPID RFC 8292, envío.

Por qué no `pywebpush`
----------------------
Es la librería de referencia y funciona, pero arrastra `requests` y `aiohttp`
para hacer una sola cosa: un POST. Este backend ya habla HTTP con `httpx` en
modo asíncrono y corre en 512 MB; sumar dos clientes HTTP más para un envío
que cabe en una función era el intercambio equivocado.

Lo que sí se usa es `cryptography`, que ya venía instalado (lo trae
`google-auth`) y que ahora se declara explícitamente en `requirements-prod.txt`
porque este módulo depende de él de forma directa. Todo lo criptográfico —ECDH
sobre P-256, HKDF, AES-128-GCM, ECDSA— sale de ahí; acá sólo se arma el sobre
que el RFC describe.

La implementación se verifica contra el ejemplo del propio RFC 8291 (sección 5)
en `tests/test_webpush_crypto.py`: mismas claves, misma sal, mismos bytes de
salida. Si ese test pasa, un navegador puede descifrar lo que sale de acá.

Qué es cada cosa
----------------
* **La suscripción** la crea el navegador: un `endpoint` (la URL del servicio de
  push de Google, Mozilla o Apple) y dos claves del teléfono, `p256dh` y `auth`.
* **El cifrado** (RFC 8291) hace que el servicio de push transporte el mensaje
  sin poder leerlo. Sólo el teléfono tiene la clave privada.
* **VAPID** (RFC 8292) prueba ante el servicio de push que el mensaje lo manda
  el mismo servidor que pidió la suscripción. Sin eso, cualquiera que obtuviera
  el `endpoint` podría mandar notificaciones a nombre de AlertaV.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

#: Tamaño de registro declarado en la cabecera del mensaje. 4096 es el valor que
#: usan todos los clientes y el del ejemplo del RFC. Un mensaje de push cabe
#: siempre en un solo registro: los servicios aceptan hasta 4096 bytes de cuerpo.
RECORD_SIZE = 4096

#: Máximo de texto útil. 4096 de registro menos 16 de la etiqueta de GCM, menos
#: el delimitador, menos la cabecera (16 de sal + 4 + 1 + 65 de clave).
MAX_PLAINTEXT_BYTES = RECORD_SIZE - 16 - 1 - 86

#: Vida del JWT de VAPID. El RFC permite hasta 24 horas; 12 deja margen para el
#: desfase de reloj entre este servidor y el servicio de push.
VAPID_TOKEN_TTL_SECONDS = 12 * 3600

#: Un JWT firmado sirve para todos los envíos a un mismo servicio de push. Se
#: reusa mientras le quede al menos esta holgura de vida.
_VAPID_REUSE_MARGIN_SECONDS = 3600


def b64url_encode(data: bytes) -> str:
    """Base64 URL-safe sin relleno, que es como viajan claves y JWT en Web Push."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Inverso de `b64url_encode`. Tolera relleno presente o ausente."""
    cleaned = value.strip()
    return base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """Punto sin comprimir: 0x04 ‖ X ‖ Y, 65 bytes."""
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


# ---------------------------------------------------------------------------
#  Claves VAPID
# ---------------------------------------------------------------------------


class VapidKeyError(ValueError):
    """La clave VAPID configurada no es una clave P-256 válida."""


@dataclass(frozen=True, slots=True)
class VapidKeys:
    """Par de claves del servidor.

    Se configura sólo la privada (`VAPID_PRIVATE_KEY`); la pública se deriva de
    ella. Configurar las dos por separado abre la puerta al error más difícil de
    diagnosticar de Web Push: una pública que no corresponde a la privada. El
    navegador se suscribe con una, el servidor firma con la otra, y el servicio
    de push responde 403 a cada envío sin decir por qué.
    """

    private_key: ec.EllipticCurvePrivateKey

    @classmethod
    def from_private_b64url(cls, value: str) -> VapidKeys:
        """Carga la clave desde su escalar de 32 bytes en base64url.

        Es el formato que produce `scripts/generate_vapid_keys.py` y el mismo
        que usan `web-push` (Node) y `pywebpush`, así que una clave generada con
        cualquiera de ellos sirve acá.
        """
        try:
            raw = b64url_decode(value)
        except (ValueError, TypeError) as exc:
            raise VapidKeyError("VAPID_PRIVATE_KEY no es base64url válido") from exc
        if len(raw) != 32:
            raise VapidKeyError(f"VAPID_PRIVATE_KEY debe decodificar a 32 bytes, no a {len(raw)}")
        try:
            key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
        except ValueError as exc:
            raise VapidKeyError("VAPID_PRIVATE_KEY no es un escalar P-256 válido") from exc
        return cls(private_key=key)

    @classmethod
    def generate(cls) -> VapidKeys:
        return cls(private_key=ec.generate_private_key(ec.SECP256R1()))

    @property
    def private_b64url(self) -> str:
        scalar = self.private_key.private_numbers().private_value
        return b64url_encode(scalar.to_bytes(32, "big"))

    @property
    def public_b64url(self) -> str:
        """La `applicationServerKey` que el navegador necesita para suscribirse."""
        return b64url_encode(_public_bytes(self.private_key.public_key()))


class VapidSigner:
    """Firma los JWT de VAPID y los reusa por servicio de push."""

    def __init__(self, keys: VapidKeys, *, subject: str) -> None:
        if not subject.startswith(("mailto:", "https://")):
            # El RFC exige un contacto. Los servicios de push lo usan para
            # avisar antes de bloquear a un emisor que se porta mal, y algunos
            # (Apple) rechazan el envío si falta o está mal formado.
            raise VapidKeyError("VAPID_SUBJECT debe empezar por 'mailto:' o 'https://'")
        self.keys = keys
        self.subject = subject
        self._cache: dict[str, tuple[str, int]] = {}

    def authorization(self, endpoint: str, *, now: float | None = None) -> str:
        """Cabecera `Authorization` para un envío a `endpoint`."""
        parts = urlsplit(endpoint)
        audience = f"{parts.scheme}://{parts.netloc}"
        current = int(time.time() if now is None else now)

        cached = self._cache.get(audience)
        if cached is not None and cached[1] - current > _VAPID_REUSE_MARGIN_SECONDS:
            token = cached[0]
        else:
            expires = current + VAPID_TOKEN_TTL_SECONDS
            token = self._sign(audience, expires)
            self._cache[audience] = (token, expires)

        return f"vapid t={token}, k={self.keys.public_b64url}"

    def _sign(self, audience: str, expires: int) -> str:
        header = b64url_encode(b'{"typ":"JWT","alg":"ES256"}')
        claims = b64url_encode(
            json.dumps(
                {"aud": audience, "exp": expires, "sub": self.subject},
                separators=(",", ":"),
            ).encode()
        )
        signing_input = f"{header}.{claims}".encode("ascii")
        der = self.keys.private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        # `cryptography` devuelve la firma en DER; JWS (ES256) la quiere cruda:
        # r y s de 32 bytes cada uno, concatenados.
        r, s = decode_dss_signature(der)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{header}.{claims}.{b64url_encode(signature)}"


# ---------------------------------------------------------------------------
#  Cifrado RFC 8291 (aes128gcm)
# ---------------------------------------------------------------------------


class SubscriptionKeyError(ValueError):
    """Las claves `p256dh`/`auth` que mandó el navegador no son válidas."""


def validate_subscription_keys(p256dh: str, auth: str) -> None:
    """Rechaza claves que el cifrado no podría usar.

    Se llama al registrar la suscripción, no al enviar: una clave rota detectada
    en el envío es un aviso de emergencia que no llega, y detectada en el
    registro es un error que el navegador ve y puede reintentar.
    """
    try:
        ua_public = b64url_decode(p256dh)
        auth_secret = b64url_decode(auth)
    except (ValueError, TypeError) as exc:
        raise SubscriptionKeyError("las claves no son base64url válido") from exc
    if len(auth_secret) != 16:
        raise SubscriptionKeyError("`auth` debe tener 16 bytes")
    if len(ua_public) != 65 or ua_public[0] != 0x04:
        raise SubscriptionKeyError("`p256dh` debe ser un punto P-256 sin comprimir")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    except ValueError as exc:
        raise SubscriptionKeyError("`p256dh` no es un punto de la curva P-256") from exc


def _hkdf(*, salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_payload(
    plaintext: bytes,
    *,
    p256dh: str,
    auth: str,
    salt: bytes | None = None,
    server_private_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Cifra `plaintext` para una suscripción. Devuelve el cuerpo del POST.

    `salt` y `server_private_key` existen sólo para reproducir el ejemplo del
    RFC en los tests. En producción se dejan en `None` y se generan al azar en
    cada envío: reusar la sal o la clave efímera con la misma suscripción
    debilitaría el cifrado.
    """
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError(
            f"el mensaje ocupa {len(plaintext)} bytes; el máximo es {MAX_PLAINTEXT_BYTES}"
        )

    ua_public_bytes = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)

    as_private = server_private_key or ec.generate_private_key(ec.SECP256R1())
    as_public_bytes = _public_bytes(as_private.public_key())
    salt = salt if salt is not None else os.urandom(16)

    # RFC 8291 §3.3-3.4: el secreto compartido se mezcla con el secreto de
    # autenticación del navegador, y de ahí salen la clave y el nonce.
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_public)
    key_info = b"WebPush: info\x00" + ua_public_bytes + as_public_bytes
    ikm = _hkdf(salt=auth_secret, ikm=ecdh_secret, info=key_info, length=32)
    cek = _hkdf(salt=salt, ikm=ikm, info=b"Content-Encoding: aes128gcm\x00", length=16)
    nonce = _hkdf(salt=salt, ikm=ikm, info=b"Content-Encoding: nonce\x00", length=12)

    # Un solo registro, que además es el último: delimitador 0x02 y sin relleno.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)

    # RFC 8188 §2.1: sal ‖ tamaño de registro ‖ largo del id ‖ id (la clave
    # pública efímera del servidor, que el navegador necesita para el ECDH).
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public_bytes)) + as_public_bytes
    return header + ciphertext


# ---------------------------------------------------------------------------
#  Envío
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PushSubscriptionKeys:
    """Lo mínimo que hace falta para mandarle algo a un navegador."""

    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True, slots=True)
class PushResult:
    """Qué pasó con un envío.

    `gone` separa el caso que importa del resto: 404 y 410 significan que la
    suscripción ya no existe —desinstalaron la app, revocaron el permiso— y el
    servicio de push no la va a aceptar nunca más. Esa fila se borra. Cualquier
    otro error puede ser pasajero y sólo suma un fallo.
    """

    status_code: int | None
    ok: bool
    gone: bool = False
    error: str | None = None


#: Códigos con los que el servicio de push dice «esta suscripción murió».
_GONE_STATUSES = frozenset({404, 410})


class WebPushSender:
    """Cliente HTTP de Web Push. Uno por proceso, reusado entre envíos."""

    def __init__(
        self,
        signer: VapidSigner,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.signer = signer
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(
        self,
        subscription: PushSubscriptionKeys,
        payload: dict[str, object],
        *,
        ttl_seconds: int,
        topic: str | None = None,
        urgency: str = "high",
    ) -> PushResult:
        """Cifra y entrega un mensaje. Nunca lanza por un fallo del servicio.

        `ttl_seconds` es cuánto guarda el servicio de push el mensaje si el
        teléfono está apagado o sin señal. `topic` hace que un mensaje nuevo con
        el mismo tema reemplace al pendiente en vez de acumularse.
        """
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        try:
            encrypted = encrypt_payload(body, p256dh=subscription.p256dh, auth=subscription.auth)
        except (ValueError, TypeError) as exc:
            # Claves que pasaron la validación del registro pero no sirven: no
            # debería ocurrir, y si ocurre no tiene arreglo reintentando.
            return PushResult(status_code=None, ok=False, gone=True, error=str(exc))

        headers = {
            "Authorization": self.signer.authorization(subscription.endpoint),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(max(0, int(ttl_seconds))),
            "Urgency": urgency,
        }
        if topic:
            headers["Topic"] = topic

        try:
            response = await self._client.post(
                subscription.endpoint, content=encrypted, headers=headers
            )
        except httpx.HTTPError as exc:
            return PushResult(status_code=None, ok=False, error=repr(exc))

        code = response.status_code
        if 200 <= code < 300:
            return PushResult(status_code=code, ok=True)
        return PushResult(
            status_code=code,
            ok=False,
            gone=code in _GONE_STATUSES,
            error=response.text[:300] or None,
        )


__all__ = [
    "MAX_PLAINTEXT_BYTES",
    "PushResult",
    "PushSubscriptionKeys",
    "SubscriptionKeyError",
    "VapidKeyError",
    "VapidKeys",
    "VapidSigner",
    "WebPushSender",
    "b64url_decode",
    "b64url_encode",
    "encrypt_payload",
    "validate_subscription_keys",
]
