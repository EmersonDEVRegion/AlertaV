"""Una sola identidad ante los servicios que se consultan (auditoría 2026-09-23).

Antes había siete `User-Agent`: el de Nominatim prestado a la CGE y al
sismológico, uno con una organización de GitHub que no existe, y varios
servicios del Estado consultados con el agente anónimo de httpx.
"""

from __future__ import annotations

import asyncio
import pathlib

import httpx
import pytest
import respx

from app.core.config import Settings, settings
from app.core.identidad import nominatim_user_agent, solo_ascii, user_agent, user_agent_o

CORREO = "operador@alertav.test"


@pytest.fixture
def con_correo(monkeypatch):
    monkeypatch.setattr(settings, "CONTACT_EMAIL", CORREO)


def test_la_identidad_lleva_repo_y_correo(con_correo):
    agente = user_agent("cortes de agua")
    assert agente == (
        f"AlertaV/1.0 (+{settings.CONTACT_URL}; {CORREO}; cortes de agua)"
    )


def test_con_navegador_el_navegador_va_primero(con_correo):
    agente = user_agent(navegador=True)
    assert agente.startswith("Mozilla/5.0")
    assert agente.endswith(f"AlertaV/1.0 (+{settings.CONTACT_URL}; {CORREO})")


def test_nunca_sale_una_tilde_que_rompa_httpx(con_correo):
    agente = user_agent("cortes de agua Región de Valparaíso", navegador=True)
    assert agente.isascii()
    assert "Region de Valparaiso" in agente
    httpx.Headers({"User-Agent": agente})  # no lanza
    assert solo_ascii("ñandú") == "nandu"


def test_la_anulacion_manda_cuando_tiene_valor(con_correo):
    assert user_agent_o("  MiAgente/2.0  ") == "MiAgente/2.0"
    assert user_agent_o("", "x").startswith("AlertaV/1.0")


def test_nominatim_usa_su_anulacion_o_la_identidad(monkeypatch, con_correo):
    monkeypatch.setattr(settings, "NOMINATIM_USER_AGENT", "AlertaV/1.0 (a@b.cl)")
    assert nominatim_user_agent() == "AlertaV/1.0 (a@b.cl)"
    monkeypatch.setattr(settings, "NOMINATIM_USER_AGENT", "")
    assert CORREO in nominatim_user_agent()
    assert "Mozilla" not in nominatim_user_agent(), "Nominatim pide identificarse, no disfrazarse"


@pytest.mark.parametrize(
    ("campos", "esperado"),
    [
        ({"CONTACT_EMAIL": "uno@a.cl", "VAPID_SUBJECT": "mailto:dos@a.cl"}, "uno@a.cl"),
        ({"VAPID_SUBJECT": "mailto:dos@a.cl", "NOMINATIM_USER_AGENT": "X (tres@a.cl)"}, "dos@a.cl"),
        ({"NOMINATIM_USER_AGENT": "AlertaV/1.0 (tres@a.cl)"}, "tres@a.cl"),
        ({"NOMINATIM_USER_AGENT": "AlertaV/0.1 (contacto: alertav@example.cl)"}, None),
        ({"VAPID_SUBJECT": "mailto:TU_CORREO"}, None),
        ({}, None),
    ],
)
def test_el_correo_de_contacto_sale_de_lo_que_ya_esta_configurado(campos, esperado):
    """Render ya tiene `VAPID_SUBJECT` y `NOMINATIM_USER_AGENT`: no hace falta otra variable."""
    assert Settings(_env_file=None, **campos).contacto_email == esperado


def _produccion(**cambios) -> Settings:
    base = {
        "ENVIRONMENT": "production",
        "APIFY_WEBHOOK_SECRET": "s" * 32,
        "APIFY_BOMBEROS_ACTOR_IDS": "nfp1fpt5gUlBwPcor",
        "VAPID_SUBJECT": "mailto:operador@alertav.cl",
        "FIRMS_MAP_KEY": "0123456789abcdef",
    }
    base.update(cambios)
    return Settings(_env_file=None, **base)


def test_produccion_arranca_sin_nominatim_user_agent_si_hay_correo():
    assert _produccion().contacto_email == "operador@alertav.cl"


def test_produccion_no_arranca_sin_ningun_correo():
    with pytest.raises(ValueError, match="correo de contacto"):
        _produccion(VAPID_SUBJECT="", PUSH_ENABLED=False)


def test_produccion_no_arranca_con_un_agente_con_tildes():
    with pytest.raises(ValueError, match="TRANSPORTE_INFORMA_USER_AGENT"):
        _produccion(TRANSPORTE_INFORMA_USER_AGENT="AlertaV (Región)")


def test_ya_no_queda_la_organizacion_de_github_que_no_existe():
    raiz = pathlib.Path(__file__).resolve().parents[1] / "app"
    culpables = [
        str(ruta.relative_to(raiz))
        for ruta in raiz.rglob("*.py")
        if "github.com/alertav" in ruta.read_text(encoding="utf-8")
        and ruta.name not in {"identidad.py", "config.py"}
    ]
    assert culpables == []


@respx.mock
def test_firms_ya_no_consulta_como_httpx_anonimo(con_correo):
    from app.collectors.firms.client import FirmsClient

    ruta = respx.get(url__startswith="https://firms.test/").mock(
        return_value=httpx.Response(200, text="latitude,longitude\n")
    )
    cliente = FirmsClient(map_key="CLAVE0123456789", base_url="https://firms.test")
    asyncio.run(cliente.fetch_area(sensor="VIIRS_SNPP_NRT", bbox="-72,-34,-70,-32", day_range=1))

    agente = ruta.calls[0].request.headers["User-Agent"]
    assert agente.startswith("AlertaV/1.0")
    assert CORREO in agente


@respx.mock
def test_los_servicios_arcgis_del_estado_reciben_la_identidad(con_correo):
    from app.collectors.geoservices import GeoJsonClient, SourceSpec

    ruta = respx.get("https://sig.test/capa.geojson").mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection", "features": []})
    )
    spec = SourceSpec(kind="geojson", url="https://sig.test/capa.geojson")
    asyncio.run(GeoJsonClient().fetch(spec))

    assert ruta.calls[0].request.headers["User-Agent"].startswith("AlertaV/1.0")
