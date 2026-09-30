"""Blindaje de la auditoría del 2026-09-23.

Cinco agujeros, cada uno con su test:

1. Rutas de operación sin autenticación (`require_operator`).
2. Producción arrancando con secretos flojos o placeholders (validador).
3. El límite de reportes ciudadanos saltable con un `X-Forwarded-For` falso.
4. Secretos en los logs (la `FIRMS_MAP_KEY` viajaba en tracebacks).
5. La `FIRMS_MAP_KEY` en `collector_runs.error`, servido por la API.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.collectors.firms.client import FirmsClient
from app.core.config import Settings, settings
from app.core.exceptions import CollectorError
from app.core.logging import JsonFormatter
from app.core.ratelimit import client_ip
from app.main import app

TOKEN = "t" * 40


# --- 1. Rutas de operación ----------------------------------------------------


@pytest.fixture
def cliente():
    return TestClient(app)


def test_en_local_sin_token_la_ruta_de_operador_sigue_abierta(cliente, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(settings, "OPERATOR_TOKEN", "")
    assert cliente.get("/api/v1/collectors").status_code == 200


def test_en_produccion_sin_token_la_ruta_falla_cerrada(cliente, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "OPERATOR_TOKEN", "")
    assert cliente.get("/api/v1/collectors").status_code == 503


@pytest.mark.parametrize(
    "cabecera",
    [None, "Bearer equivocado", f"Basic {TOKEN}", "Bearer "],
)
def test_con_token_sin_credencial_valida_es_401(cliente, monkeypatch, cabecera):
    monkeypatch.setattr(settings, "OPERATOR_TOKEN", TOKEN)
    headers = {"Authorization": cabecera} if cabecera else {}
    respuesta = cliente.get("/api/v1/collectors", headers=headers)
    assert respuesta.status_code == 401
    assert respuesta.headers.get("www-authenticate") == "Bearer"


def test_un_token_no_ascii_se_rechaza_sin_500(monkeypatch):
    """`compare_digest` con dos `str` no ASCII lanza TypeError: se compara en bytes."""
    from fastapi import HTTPException

    from app.api.deps import require_operator

    monkeypatch.setattr(settings, "OPERATOR_TOKEN", TOKEN)
    with pytest.raises(HTTPException) as info:
        asyncio.run(require_operator("Bearer ñandú"))
    assert info.value.status_code == 401


def test_con_el_token_correcto_pasa(cliente, monkeypatch):
    monkeypatch.setattr(settings, "OPERATOR_TOKEN", TOKEN)
    respuesta = cliente.get("/api/v1/collectors", headers={"Authorization": f"Bearer {TOKEN}"})
    assert respuesta.status_code == 200
    assert "collectors" in respuesta.json()


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("get", "/api/v1/collectors"),
        ("get", "/api/v1/collectors/runs"),
        ("post", "/api/v1/collectors/transporte_informa/run"),
        ("post", "/api/v1/collectors/backfill-geocoding"),
        ("post", "/api/v1/incidents/correlate"),
        # Moderación de comentarios ciudadanos (§C).
        ("get", "/api/v1/moderacion"),
        ("post", "/api/v1/moderacion/00000000-0000-0000-0000-000000000000"),
        # Lecturas crudas: traen el texto ciudadano sin revisar y su GPS exacto.
        ("get", "/api/v1/events"),
        ("get", "/api/v1/events/geojson"),
        ("get", "/api/v1/events/stats"),
        ("get", "/api/v1/events/00000000-0000-0000-0000-000000000000"),
        ("get", "/api/v1/events/00000000-0000-0000-0000-000000000000/neighbours"),
    ],
)
def test_todas_las_rutas_de_operacion_piden_token(cliente, monkeypatch, metodo, ruta):
    """La lista de rutas que gastan cuota o exponen errores completos.

    El 401 tiene que llegar ANTES de tocar la base: sin base en el test, una
    ruta que no pidiera token reventaría con otro código.
    """
    monkeypatch.setattr(settings, "OPERATOR_TOKEN", TOKEN)
    assert getattr(cliente, metodo)(ruta).status_code == 401


def test_la_salud_de_los_collectors_sigue_publica():
    """La lee el mapa: ponerle token dejaría el aviso de capa ciega sin datos."""
    from app.api.v1.endpoints import collectors

    ruta = next(r for r in collectors.router.routes if r.path.endswith("/health"))
    assert not ruta.dependant.dependencies or all(
        d.call.__name__ != "require_operator" for d in ruta.dependant.dependencies
    )


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [("post", "/api/v1/events"), ("post", "/api/v1/events/batch")],
)
def test_la_ingesta_abierta_ya_no_existe(cliente, metodo, ruta):
    """Dejaban a cualquiera crear señales con `source=bomberos` y confianza 1.0."""
    respuesta = getattr(cliente, metodo)(ruta, json={})
    assert respuesta.status_code in (404, 405)


# --- 2. Validador de producción ----------------------------------------------


def _produccion(**cambios) -> Settings:
    base = {
        "ENVIRONMENT": "production",
        "APIFY_WEBHOOK_SECRET": "s" * 32,
        "APIFY_BOMBEROS_ACTOR_IDS": "nfp1fpt5gUlBwPcor",
        "NOMINATIM_USER_AGENT": "AlertaV/1.0 (operador@alertav.cl)",
        "VAPID_SUBJECT": "mailto:operador@alertav.cl",
        "FIRMS_MAP_KEY": "0123456789abcdef",
    }
    base.update(cambios)
    return Settings(_env_file=None, **base)


def test_la_configuracion_de_produccion_valida_arranca():
    assert _produccion().ENVIRONMENT == "production"


def test_sin_operator_token_arranca_igual():
    """Las rutas de operación responden 503: no hace falta tumbar el mapa por eso."""
    assert _produccion(OPERATOR_TOKEN="").OPERATOR_TOKEN == ""


@pytest.mark.parametrize(
    ("cambio", "motivo"),
    [
        ({"APIFY_WEBHOOK_SECRET": ""}, "APIFY_WEBHOOK_SECRET"),
        ({"APIFY_WEBHOOK_SECRET": "corto"}, "APIFY_WEBHOOK_SECRET"),
        ({"APIFY_WEBHOOK_SECRET": "ñ" * 32}, "APIFY_WEBHOOK_SECRET"),
        ({"APIFY_BOMBEROS_ACTOR_IDS": ""}, "APIFY_BOMBEROS_ACTOR_IDS"),
        ({"OPERATOR_TOKEN": "corto"}, "OPERATOR_TOKEN"),
        ({"NOMINATIM_USER_AGENT": "AlertaV/0.1 (contacto: TU_CORREO)"}, "NOMINATIM_USER_AGENT"),
        ({"NOMINATIM_USER_AGENT": "AlertaV/0.1 (contacto: alertav@example.cl)"}, "NOMINATIM_USER_AGENT"),
        ({"VAPID_SUBJECT": "mailto:TU_CORREO"}, "VAPID_SUBJECT"),
        ({"FIRMS_MAP_KEY": "<tu-map-key-de-nasa-firms>"}, "FIRMS_MAP_KEY"),
    ],
)
def test_produccion_no_arranca_con_huecos(cambio, motivo):
    with pytest.raises(ValueError, match=motivo):
        _produccion(**cambio)


#: Un proxy de salida bien formado: usuario y clave larga, host y puerto.
PROXY_VALIDO = "http://alertav:" + "k" * 32 + "@203.0.113.7:8888"


def test_el_proxy_de_esval_es_opcional_en_produccion():
    """Sin proxy arranca igual: el collector de Esval falla solo, con el motivo."""
    assert _produccion(ESVAL_PROXY_URL="").ESVAL_PROXY_URL == ""
    assert _produccion(ESVAL_PROXY_URL=PROXY_VALIDO).ESVAL_PROXY_URL == PROXY_VALIDO


@pytest.mark.parametrize(
    "url",
    [
        "http://203.0.113.7:8888",  # sin credenciales: un proxy abierto
        "http://alertav:corta@203.0.113.7:8888",  # clave adivinable
        "socks5://alertav:" + "k" * 32 + "@203.0.113.7:1080",  # httpx sin extra
        "http://alertav:" + "k" * 32 + "@203.0.113.7",  # sin puerto
        "http://alertav:" + "k" * 32 + "@203.0.113.7:99999",  # puerto imposible
        "http://alertav:" + "ñ" * 32 + "@203.0.113.7:8888",  # no ASCII
    ],
)
def test_un_proxy_de_esval_flojo_no_arranca_y_el_error_no_trae_la_clave(url):
    with pytest.raises(ValueError, match="ESVAL_PROXY_URL") as error:
        _produccion(ESVAL_PROXY_URL=url)
    clave = url.split(":")[2].split("@")[0] if url.count(":") > 2 else ""
    if len(clave) > 5:
        assert clave not in str(error.value)


def test_fuera_de_produccion_no_se_exige_nada():
    assert Settings(_env_file=None, ENVIRONMENT="local", APIFY_WEBHOOK_SECRET="").ENVIRONMENT == "local"


# --- 3. IP del cliente --------------------------------------------------------


def test_la_cabecera_de_cloudflare_gana_a_un_x_forwarded_for_falso():
    """En Render el cliente puede escribir X-Forwarded-For; True-Client-IP no."""
    ip = client_ip(
        forwarded_for="1.2.3.4, 181.43.10.20, 172.71.195.123",
        real_ip=None,
        peer="10.0.0.1",
        true_client_ip="181.43.10.20",
    )
    assert ip == "181.43.10.20"


def test_cf_connecting_ip_como_respaldo():
    assert (
        client_ip(forwarded_for="1.2.3.4", real_ip=None, peer=None, cf_connecting_ip="200.1.1.1")
        == "200.1.1.1"
    )


def test_sin_cabeceras_de_cloudflare_se_mantiene_el_comportamiento_anterior():
    assert client_ip(forwarded_for="5.6.7.8, 9.9.9.9", real_ip=None, peer=None) == "5.6.7.8"


# --- 4. Secretos en los logs -------------------------------------------------


def _registro_con_traceback(mensaje: str) -> logging.LogRecord:
    try:
        raise RuntimeError(mensaje)
    except RuntimeError:
        import sys

        return logging.LogRecord(
            "prueba", logging.ERROR, __file__, 1, "falló: %s", (mensaje,), sys.exc_info()
        )


def test_el_formatter_tacha_el_secreto_en_mensaje_y_traceback():
    clave = "ABCDEF0123456789SECRETA"
    formatter = JsonFormatter(secretos=(clave,))
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{clave}/VIIRS/x/1"

    linea = formatter.format(_registro_con_traceback(url))

    assert clave not in linea
    assert "***" in linea
    json.loads(linea)  # sigue siendo JSON válido


def test_los_secretos_configurados_incluyen_la_clave_de_firms(monkeypatch):
    from app.core.logging import secretos_configurados

    monkeypatch.setattr(settings, "FIRMS_MAP_KEY", "clave-firms-de-prueba-1234")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "alertav")
    secretos = secretos_configurados()
    assert "clave-firms-de-prueba-1234" in secretos
    assert "alertav" not in secretos, "un valor corto taparía media línea sin proteger nada"


def test_los_secretos_configurados_incluyen_la_clave_del_proxy_de_esval(monkeypatch):
    from app.core.logging import secretos_configurados

    monkeypatch.setattr(
        settings, "ESVAL_PROXY_URL", "http://alertav:clave%2Fdel-proxy-cl-0123456789@203.0.113.7:8888"
    )
    secretos = secretos_configurados()
    assert "clave%2Fdel-proxy-cl-0123456789" in secretos
    assert "clave/del-proxy-cl-0123456789" in secretos


def test_una_url_de_proxy_ilegible_no_rompe_el_logging(monkeypatch):
    from app.core.logging import secretos_configurados

    monkeypatch.setattr(settings, "ESVAL_PROXY_URL", "http://[no-es-ipv6")
    secretos_configurados()  # no lanza: el logging no puede caerse por esto


# --- 5. La clave de FIRMS en el error de la corrida --------------------------


@respx.mock
@pytest.mark.parametrize(
    "respuesta",
    [httpx.Response(403), httpx.ConnectError("sin red")],
)
def test_el_error_de_firms_no_lleva_la_clave(respuesta):
    clave = "CLAVEFIRMS0123456789"
    cliente_firms = FirmsClient(map_key=clave, base_url="https://firms.test")
    ruta = respx.get(url__startswith="https://firms.test/")
    if isinstance(respuesta, Exception):
        ruta.mock(side_effect=respuesta)
    else:
        ruta.mock(return_value=respuesta)

    with pytest.raises(CollectorError) as info:
        asyncio.run(
            cliente_firms.fetch_area(sensor="VIIRS_SNPP_NRT", bbox="-72,-34,-70,-32", day_range=1)
        )

    error = info.value
    assert clave not in error.message
    assert clave not in json.dumps(error.detail)
    # Sin excepción encadenada: el traceback de logger.exception no la imprime.
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
