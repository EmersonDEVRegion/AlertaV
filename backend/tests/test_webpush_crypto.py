"""Cifrado y firma de Web Push.

El test que importa es el primero: reproduce byte a byte el ejemplo de la
sección 5 del RFC 8291. Si pasa, un navegador descifra lo que sale de
`encrypt_payload`; si falla, ninguna notificación llegaría, y el servicio de
push no diría por qué (acepta el POST con 201 y el teléfono lo descarta).
"""

from __future__ import annotations

import json
import struct

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.services.push.webpush import (
    MAX_PLAINTEXT_BYTES,
    PushSubscriptionKeys,
    SubscriptionKeyError,
    VapidKeyError,
    VapidKeys,
    VapidSigner,
    WebPushSender,
    b64url_decode,
    b64url_encode,
    encrypt_payload,
    validate_subscription_keys,
)

# --- RFC 8291, sección 5 ------------------------------------------------------

RFC_PLAINTEXT = b64url_decode("V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24")
RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_AS_PUBLIC = (
    "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
)
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_BODY = "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"


def _private_key(b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64url_decode(b64), "big"), ec.SECP256R1())


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _decrypt(body: bytes, *, ua_private: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """Lo que hace el navegador. Independiente de `encrypt_payload` a propósito."""
    salt = body[:16]
    record_size, id_len = struct.unpack("!IB", body[16:21])
    as_public_bytes = body[21 : 21 + id_len]
    ciphertext = body[21 + id_len :]
    assert record_size == 4096

    ua_public_bytes = _point(ua_private.public_key())
    as_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public_bytes)
    shared = ua_private.exchange(ec.ECDH(), as_public)

    def hkdf(salt_: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt_, info=info).derive(ikm)

    ikm = hkdf(
        b64url_decode(auth),
        shared,
        b"WebPush: info\x00" + ua_public_bytes + as_public_bytes,
        32,
    )
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert padded.endswith(b"\x02"), "el único registro debe llevar el delimitador final"
    return padded[:-1]


class TestRfc8291:
    def test_reproduce_el_ejemplo_del_rfc_byte_a_byte(self) -> None:
        body = encrypt_payload(
            RFC_PLAINTEXT,
            p256dh=RFC_UA_PUBLIC,
            auth=RFC_AUTH,
            salt=b64url_decode(RFC_SALT),
            server_private_key=_private_key(RFC_AS_PRIVATE),
        )
        assert b64url_encode(body) == RFC_BODY

    def test_la_clave_publica_del_servidor_viaja_en_la_cabecera(self) -> None:
        body = encrypt_payload(
            RFC_PLAINTEXT,
            p256dh=RFC_UA_PUBLIC,
            auth=RFC_AUTH,
            salt=b64url_decode(RFC_SALT),
            server_private_key=_private_key(RFC_AS_PRIVATE),
        )
        assert body[21:86] == b64url_decode(RFC_AS_PUBLIC)

    def test_el_navegador_puede_descifrar_un_mensaje_real(self) -> None:
        ua_private = ec.generate_private_key(ec.SECP256R1())
        ua_public = b64url_encode(_point(ua_private.public_key()))
        auth = b64url_encode(b"0123456789abcdef")
        payload = json.dumps({"title": "Incendio forestal a 1,2 km"}).encode()

        body = encrypt_payload(payload, p256dh=ua_public, auth=auth)

        assert _decrypt(body, ua_private=ua_private, auth=auth) == payload

    def test_cada_envio_usa_sal_y_clave_efimera_nuevas(self) -> None:
        a = encrypt_payload(b"x", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
        b = encrypt_payload(b"x", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
        assert a[:16] != b[:16]
        assert a[21:86] != b[21:86]

    def test_rechaza_un_mensaje_que_no_cabe(self) -> None:
        with pytest.raises(ValueError, match="máximo"):
            encrypt_payload(b"x" * (MAX_PLAINTEXT_BYTES + 1), p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)

    def test_el_maximo_cabe_en_los_4096_bytes_del_servicio(self) -> None:
        body = encrypt_payload(b"x" * MAX_PLAINTEXT_BYTES, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
        assert len(body) == 4096


class TestValidacionDeSuscripcion:
    def test_acepta_las_claves_del_rfc(self) -> None:
        validate_subscription_keys(RFC_UA_PUBLIC, RFC_AUTH)

    @pytest.mark.parametrize(
        ("p256dh", "auth"),
        [
            (RFC_UA_PUBLIC, "corto"),
            (RFC_UA_PUBLIC[:-4], RFC_AUTH),
            ("B" + "A" * 86, RFC_AUTH),  # 65 bytes con 0x04 pero fuera de la curva
            ("%%%", RFC_AUTH),
        ],
    )
    def test_rechaza_claves_que_el_cifrado_no_podria_usar(self, p256dh: str, auth: str) -> None:
        with pytest.raises(SubscriptionKeyError):
            validate_subscription_keys(p256dh, auth)


class TestVapid:
    def test_la_publica_se_deriva_de_la_privada(self) -> None:
        keys = VapidKeys.from_private_b64url(RFC_AS_PRIVATE)
        assert keys.public_b64url == RFC_AS_PUBLIC
        assert keys.private_b64url == RFC_AS_PRIVATE

    def test_generar_y_recargar_da_el_mismo_par(self) -> None:
        keys = VapidKeys.generate()
        again = VapidKeys.from_private_b64url(keys.private_b64url)
        assert again.public_b64url == keys.public_b64url
        assert len(b64url_decode(keys.public_b64url)) == 65

    @pytest.mark.parametrize("value", ["", "no-es-base64-!!", b64url_encode(b"\x01" * 31)])
    def test_rechaza_claves_privadas_invalidas(self, value: str) -> None:
        with pytest.raises(VapidKeyError):
            VapidKeys.from_private_b64url(value)

    def test_exige_un_contacto_valido(self) -> None:
        with pytest.raises(VapidKeyError):
            VapidSigner(VapidKeys.generate(), subject="alertav@example.com")

    def test_el_jwt_es_verificable_con_la_clave_publica(self) -> None:
        keys = VapidKeys.generate()
        signer = VapidSigner(keys, subject="mailto:equipo@alertav.cl")
        header = signer.authorization(
            "https://fcm.googleapis.com/fcm/send/abc:def", now=1_790_000_000
        )

        assert header.startswith("vapid t=")
        token = header.split("t=", 1)[1].split(",", 1)[0]
        k = header.split("k=", 1)[1]
        assert k == keys.public_b64url

        head, claims, signature = token.split(".")
        assert json.loads(b64url_decode(head)) == {"typ": "JWT", "alg": "ES256"}
        payload = json.loads(b64url_decode(claims))
        assert payload["aud"] == "https://fcm.googleapis.com"
        assert payload["sub"] == "mailto:equipo@alertav.cl"
        assert payload["exp"] - 1_790_000_000 <= 24 * 3600

        raw = b64url_decode(signature)
        assert len(raw) == 64
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        keys.private_key.public_key().verify(
            der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())
        )

    def test_reusa_el_token_por_servicio_y_lo_renueva_antes_de_vencer(self) -> None:
        signer = VapidSigner(VapidKeys.generate(), subject="mailto:a@b.cl")
        endpoint = "https://updates.push.services.mozilla.com/wpush/v2/xyz"
        first = signer.authorization(endpoint, now=1_000_000)
        assert signer.authorization(endpoint, now=1_000_000 + 60) == first
        assert signer.authorization(endpoint, now=1_000_000 + 11.5 * 3600) != first

    def test_un_token_por_audiencia(self) -> None:
        signer = VapidSigner(VapidKeys.generate(), subject="mailto:a@b.cl")
        a = signer.authorization("https://fcm.googleapis.com/fcm/send/1", now=1)
        b = signer.authorization("https://web.push.apple.com/abc", now=1)
        assert a != b


class TestEnvio:
    SUB = PushSubscriptionKeys(
        endpoint="https://fcm.googleapis.com/fcm/send/abc", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH
    )

    def _sender(self) -> WebPushSender:
        return WebPushSender(
            VapidSigner(VapidKeys.generate(), subject="mailto:a@b.cl"),
            client=httpx.AsyncClient(),
        )

    @respx.mock
    async def test_manda_las_cabeceras_del_protocolo(self) -> None:
        route = respx.post(self.SUB.endpoint).mock(return_value=httpx.Response(201))

        result = await self._sender().send(
            self.SUB, {"title": "t"}, ttl_seconds=1800, topic="INC-2026-00142"
        )

        assert result.ok and not result.gone
        request = route.calls.last.request
        assert request.headers["Content-Encoding"] == "aes128gcm"
        assert request.headers["TTL"] == "1800"
        assert request.headers["Urgency"] == "high"
        assert request.headers["Topic"] == "INC-2026-00142"
        assert request.headers["Authorization"].startswith("vapid t=")

    @respx.mock
    @pytest.mark.parametrize("code", [404, 410])
    async def test_404_y_410_marcan_la_suscripcion_como_muerta(self, code: int) -> None:
        respx.post(self.SUB.endpoint).mock(return_value=httpx.Response(code))
        result = await self._sender().send(self.SUB, {}, ttl_seconds=60)
        assert not result.ok and result.gone

    @respx.mock
    @pytest.mark.parametrize("code", [400, 403, 413, 429, 500, 503])
    async def test_otros_errores_no_la_matan(self, code: int) -> None:
        respx.post(self.SUB.endpoint).mock(return_value=httpx.Response(code, text="x"))
        result = await self._sender().send(self.SUB, {}, ttl_seconds=60)
        assert not result.ok and not result.gone
        assert result.status_code == code

    @respx.mock
    async def test_un_fallo_de_red_no_lanza(self) -> None:
        respx.post(self.SUB.endpoint).mock(side_effect=httpx.ConnectError("sin red"))
        result = await self._sender().send(self.SUB, {}, ttl_seconds=60)
        assert not result.ok and result.status_code is None and not result.gone
