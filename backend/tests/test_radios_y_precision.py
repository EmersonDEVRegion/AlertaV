"""Radios de aviso por categoría y punto del incidente (§K, 2026-10-05)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.models.enums import IncidentType
from app.repositories.incident_repository import ubicacion_de
from app.repositories.push_repository import Recipient, WaterCutView, radio_de_categoria_sql
from app.services.push import radios
from app.services.push.messages import water_cut_message

NOW = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)


# --- Punto del incidente ----------------------------------------------------------


def test_el_cruce_manda_sobre_la_calle_aunque_pese_menos():
    """El caso de INC-2026-00872: despacho en la esquina, nota a mitad de cuadra."""
    ubicacion = ubicacion_de(
        [
            (-33.0500, -71.6150, 1.00, "intersection"),  # Bomberos, en el cruce
            (-33.0480, -71.6160, 0.70, "street"),  # prensa, sobre Las Monjas
        ]
    )
    assert ubicacion is not None
    assert (ubicacion.lat, ubicacion.lon) == (-33.05, -71.615)
    assert ubicacion.precision == "intersection"


def test_una_coordenada_propia_manda_sobre_la_calle():
    ubicacion = ubicacion_de([(-33.0, -71.6, 0.4, None), (-33.1, -71.7, 1.0, "street")])
    assert ubicacion is not None and ubicacion.precision == "exacta"
    assert (ubicacion.lat, ubicacion.lon) == (-33.0, -71.6)


def test_dentro_del_mismo_rango_se_pondera_por_confianza():
    """Los píxeles de FIRMS siguen promediándose entre ellos, con su peso."""
    ubicacion = ubicacion_de([(-33.0, -71.6, 0.75, None), (-33.4, -71.6, 0.25, None)])
    assert ubicacion is not None
    assert ubicacion.lat == pytest.approx(-33.1)


def test_sin_senales_no_hay_punto():
    assert ubicacion_de([]) is None


def test_una_precision_desconocida_cuenta_como_calle():
    ubicacion = ubicacion_de([(-33.0, -71.6, 1.0, "rara"), (-33.2, -71.6, 1.0, "sector")])
    assert ubicacion is not None and ubicacion.precision == "street"


# --- Radios ---------------------------------------------------------------------------


def test_los_radios_por_defecto_son_los_acordados():
    assert radios.por_defecto() == {
        "fire": 5000.0,
        "traffic": 2000.0,
        "power": 1000.0,
        "hydro": 3000.0,
        "other": 2000.0,
        "water": 1000.0,
    }


@pytest.mark.parametrize(
    ("tipo", "categoria"),
    [
        (IncidentType.WILDFIRE, "fire"),
        (IncidentType.STRUCTURAL_FIRE, "fire"),
        (IncidentType.ACCIDENT, "traffic"),
        (IncidentType.POWER_OUTAGE, "power"),
        (IncidentType.FLOOD, "hydro"),
        (IncidentType.LANDSLIDE, "hydro"),
        (IncidentType.RESCUE, "other"),
        (IncidentType.OTHER, "other"),
    ],
)
def test_cada_tipo_cae_en_su_categoria(tipo, categoria):
    assert radios.categoria_de(tipo) == categoria


def test_normalizar_acepta_cero_y_redondea():
    assert radios.normalizar({"power": 0, "fire": 4999.6}) == {"power": 0.0, "fire": 5000.0}


@pytest.mark.parametrize(
    "malos",
    [{"fire": 100}, {"fire": 25_000}, {"fire": -1}, {"incendios": 5000}, {"fire": "mucho"}],
)
def test_normalizar_rechaza_lo_que_no_sirve(malos):
    with pytest.raises(ValueError):
        radios.normalizar(malos)


def test_los_efectivos_mezclan_lo_elegido_con_lo_del_servidor():
    efectivos = radios.efectivos({"power": 0, "fire": 8000, "basura": 3})
    assert efectivos["power"] == 0
    assert efectivos["fire"] == 8000
    assert efectivos["traffic"] == settings.PUSH_RADIO_TRAFFIC_M
    assert "basura" not in efectivos


def test_el_sql_del_radio_usa_lo_guardado_o_el_valor_por_defecto():
    from app.models.push import PushSubscription

    sql = str(
        radio_de_categoria_sql(PushSubscription.radios, "power").compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "coalesce" in sql.lower()
    assert "->>" in sql and "'power'" in sql
    assert "1000.0" in sql


# --- Endpoints -------------------------------------------------------------------------


def test_el_estado_publica_las_categorias_y_sus_radios():
    from app.main import app

    datos = TestClient(app).get("/api/v1/push/status").json()
    assert datos["radios_por_defecto"]["power"] == 1000
    assert [c["clave"] for c in datos["categorias"]] == list(radios.CATEGORIAS)
    assert datos["categorias"][-1]["etiqueta"] == "Cortes de agua"


def test_el_servicio_rechaza_radios_invalidos():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.core.exceptions import ValidationError
    from app.schemas.push import PushSubscribeRequest
    from app.services.push.subscriptions import PushSubscriptionService

    pedido = PushSubscribeRequest.model_validate(
        {
            "subscription": {
                "endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                "keys": {"p256dh": "x" * 87, "auth": "y" * 22},
            },
            "lat": -33.0,
            "lon": -71.6,
            "radios": {"fire": 50},
        }
    )
    servicio = PushSubscriptionService(MagicMock())
    servicio.repo = MagicMock()
    servicio.repo.upsert_subscription = AsyncMock()
    import app.services.push.subscriptions as modulo

    original = modulo.validate_subscription_keys
    modulo.validate_subscription_keys = lambda *_: None  # type: ignore[assignment]
    try:
        with pytest.raises(ValidationError):
            asyncio.run(servicio.subscribe(pedido))
    finally:
        modulo.validate_subscription_keys = original  # type: ignore[assignment]
    servicio.repo.upsert_subscription.assert_not_awaited()


# --- Cortes de agua -------------------------------------------------------------------


def corte(**cambios: Any) -> WaterCutView:
    base: dict[str, Any] = {
        "key": "esval:12345",
        "public_id": "8b0c6e8e-0000-0000-0000-000000000001",
        "lat": -33.0245,
        "lon": -71.5518,
        "comuna": "Viña del Mar",
        "calles": "1 Norte entre 3 y 5 Poniente",
        "sector": None,
        "inicio": "2026-10-05T23:00:00-03:00",
        "fin": "2026-10-06T06:00:00-03:00",
        "programado": True,
        "motivo": "Mantención de red",
    }
    base.update(cambios)
    return WaterCutView(**base)


def test_el_aviso_de_agua_dice_que_donde_y_hasta_cuando():
    c = corte()
    mensaje = water_cut_message(
        key=c.key,
        public_id=c.public_id,
        lat=c.lat,
        lon=c.lon,
        distance_m=650,
        comuna=c.comuna,
        calles=c.calles,
        sector=c.sector,
        inicio=c.inicio,
        fin=c.fin,
        programado=c.programado,
        motivo=c.motivo,
        place="Casa",
        now=NOW,
    )
    assert mensaje.title == "Corte de agua programado a 650 m de Casa"
    assert "1 Norte entre 3 y 5 Poniente · Viña del Mar" in mensaje.body
    # 22:00 UTC son las 19:00 en Chile: el inicio es hoy y el fin, mañana.
    assert "Desde 23:00 hasta 06/10 06:00 (estimado)" in mensaje.body
    assert mensaje.body.endswith("Fuente: Esval")
    assert mensaje.kind == "water_cut"
    assert "corte_agua=" in mensaje.url and "lat=-33.0245" in mensaje.url


def test_un_corte_sin_bloque_esval_no_se_avisa():
    fila = SimpleNamespace(
        raw_data={}, lat=-33.0, lon=-71.6, external_id="x", public_id="y", commune=None
    )
    assert WaterCutView.de(fila) is None  # type: ignore[arg-type]


async def test_el_notificador_avisa_cortes_y_pasa_la_categoria_del_incidente():
    from tests.test_push_notifier import NOW as AHORA_NOTIFICADOR
    from tests.test_push_notifier import FakeRepo, FakeSender, _incident, _notifier, _recipient

    repo = FakeRepo(
        incidents=[_incident(type=IncidentType.POWER_OUTAGE)],
        incident_recipients={"INC-2026-00142": [_recipient(1, 800)]},
        water_cuts=[corte()],
        water_recipients={"esval:12345": [_recipient(2, 400)]},
    )
    sender = FakeSender()
    notifier, _ = _notifier(repo, sender)
    resultado = await notifier.run(now=AHORA_NOTIFICADOR)

    assert repo.incident_queries[0]["categoria"] == "power"
    assert resultado.water_cuts_considered == 1
    tipos = sorted(kind for kind, _, _ in repo.reserved)
    assert tipos == ["incident", "water_cut"]
    titulos = [payload["title"] for _, payload, _ in sender.sent]
    assert "Corte de agua programado a 400 m" in titulos


def test_recipient_sigue_igual():
    r = Recipient(subscription_id=1, endpoint="e", p256dh="p", auth="a", distance_m=1.0)
    assert r.place is None
