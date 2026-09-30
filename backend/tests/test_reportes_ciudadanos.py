"""Reportes ciudadanos antes del lanzamiento (§C, 2026-09-30).

Lo que se fija acá, en el orden en que lo vive un reporte:

1. **Entrada.** Geocerca, precisión GPS, Turnstile, cupo por dispositivo y por
   red, hora del servidor, huellas en vez de identificadores.
2. **Quórum.** Un incidente sólo ciudadano no se publica hasta juntar tres
   vecinos independientes (dispositivos Y redes distintos). Repetir el reporte
   no suma confianza ni vecinos.
3. **Texto.** Oculto hasta que Gemini o un operador lo aprueben; el filtro
   rechaza de entrada teléfonos, correos, RUT y enlaces.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_ingest_service
from app.core.config import settings
from app.main import app
from app.models.enums import EventSource, EventType, IncidentStatus, IncidentType
from app.schemas.event import CitizenReportCreate, ReportCategory
from app.services import ciudadanos, moderacion, turnstile
from app.services.ciudadanos import (
    APROBADO,
    PENDIENTE,
    RECHAZADO,
    es_publico,
    huella,
    huellas_de,
    prefiltro,
    red_de,
    seleccionar_independientes,
    texto_publico,
)
from app.services.correlation.engine import CorrelationEngine
from app.services.ingest_service import IngestService

AHORA = datetime(2026, 9, 30, 21, 0, tzinfo=UTC)
VINA = (-33.0245, -71.5518)


# --- Huellas ------------------------------------------------------------------


def test_la_red_de_una_ipv4_es_su_24():
    assert red_de("190.100.20.33") == "190.100.20.0/24"
    assert red_de("190.100.20.250") == red_de("190.100.20.1")
    assert red_de("190.100.21.1") != red_de("190.100.20.1")


def test_la_red_de_una_ipv6_es_su_48():
    assert red_de("2800:150:1:2:3::9") == "2800:150:1::/48"


def test_una_ip_ilegible_es_su_propia_red():
    assert red_de("desconocida") == "desconocida"


def test_la_huella_no_guarda_el_valor_y_es_estable():
    h = huella("abc-123-dispositivo", tipo="dispositivo")
    assert h == huella("abc-123-dispositivo", tipo="dispositivo")
    assert "abc" not in h and len(h) == 20


def test_la_huella_depende_de_la_sal(monkeypatch):
    antes = huella("x", tipo="red")
    monkeypatch.setattr(settings, "CITIZEN_HASH_SALT", "otra-sal-de-prueba")
    assert huella("x", tipo="red") != antes


def test_sin_dispositivo_se_usa_la_ip_sin_confundirla_con_la_red():
    sin = huellas_de(ip="10.0.0.5", dispositivo=None)
    con = huellas_de(ip="10.0.0.5", dispositivo="abcdefgh1234")
    assert sin["red"] == con["red"]
    assert sin["dispositivo"] != con["dispositivo"]


# --- Independencia y publicación ----------------------------------------------


def senal(i: int, *, fuente=EventSource.CITIZEN, dispositivo=None, red=None, minuto=0):
    raw: dict[str, Any] = {}
    if dispositivo or red:
        raw["_ciudadano"] = {"dispositivo": dispositivo, "red": red}
    return SimpleNamespace(
        id=i,
        source=fuente,
        type=EventType.SMOKE,
        confidence=0.40,
        timestamp=AHORA + timedelta(minutes=minuto),
        raw_data=raw,
        commune=None,
        text="humo",
        lat=VINA[0],
        lon=VINA[1],
        province=None,
    )


def test_el_mismo_dispositivo_cuenta_una_vez():
    senales = [senal(i, dispositivo="d1", red=f"r{i}", minuto=i) for i in range(3)]
    assert [s.id for s in seleccionar_independientes(senales)] == [0]


def test_la_misma_red_cuenta_una_vez_aunque_cambie_el_dispositivo():
    """Borrar el almacenamiento del navegador no fabrica vecinos nuevos."""
    senales = [senal(i, dispositivo=f"d{i}", red="casa", minuto=i) for i in range(3)]
    assert len(seleccionar_independientes(senales)) == 1


def test_tres_vecinos_de_verdad_son_tres():
    senales = [senal(i, dispositivo=f"d{i}", red=f"r{i}", minuto=i) for i in range(3)]
    assert len(seleccionar_independientes(senales)) == 3


def test_manda_el_primero_que_llego():
    tarde = senal(1, dispositivo="d", red="r", minuto=5)
    temprano = senal(2, dispositivo="d", red="r", minuto=1)
    assert seleccionar_independientes([tarde, temprano]) == [temprano]


def test_un_reporte_viejo_sin_huellas_es_su_propio_vecino():
    senales = [senal(1), senal(2)]
    assert len(seleccionar_independientes(senales)) == 2


def test_otras_fuentes_no_pasan_por_la_seleccion():
    senales = [senal(1, fuente=EventSource.NASA_FIRMS), senal(2, dispositivo="d", red="r")]
    assert [s.id for s in seleccionar_independientes(senales)] == [2]


@pytest.mark.parametrize(
    ("fuentes", "vecinos", "freno", "esperado"),
    [
        ([EventSource.CITIZEN], 1, False, False),
        ([EventSource.CITIZEN], 2, False, False),
        ([EventSource.CITIZEN], 3, False, True),
        ([EventSource.CITIZEN], 3, True, False),
        # Con cualquier otra fuente se publica como siempre, con freno o sin él.
        ([EventSource.CITIZEN, EventSource.NASA_FIRMS], 1, False, True),
        ([EventSource.BOMBEROS], 0, True, True),
        (["citizen", "media"], 1, False, True),
    ],
)
def test_que_se_publica(fuentes, vecinos, freno, esperado):
    assert es_publico(fuentes=fuentes, independientes=vecinos, freno=freno) is esperado


# --- El motor: quórum y confianza sin duplicados --------------------------------


def motor_con(senales: list[Any]) -> tuple[CorrelationEngine, dict[str, Any]]:
    engine = CorrelationEngine(MagicMock(), perfiles=False, solo_region=False)
    escrito: dict[str, Any] = {}

    async def update_incident(_id, **valores):
        escrito.update(valores)

    engine.repo = MagicMock()
    engine.repo.signals_of = AsyncMock(return_value=senales)
    engine.repo.recompute_geometry = AsyncMock(return_value=None)
    engine.repo.comunas_disponibles = AsyncMock(return_value=False)
    engine.repo.update_incident = update_incident
    return engine, escrito


def refrescar(senales: list[Any], *, freno: bool = False) -> dict[str, Any]:
    engine, escrito = motor_con(senales)
    engine._freno_ciudadano = freno
    incidente = SimpleNamespace(id=1, status=IncidentStatus.ACTIVE, lat=VINA[0], lon=VINA[1])
    asyncio.run(engine._refresh(incidente, now=AHORA))  # type: ignore[arg-type]
    return escrito


def test_el_hueco_de_la_confianza_esta_cerrado():
    """Dos reportes de la misma persona ya no suman 0,58 ni publican nada."""
    escrito = refrescar(
        [
            senal(1, dispositivo="d1", red="4g"),
            senal(2, dispositivo="d1", red="wifi", minuto=1),
        ]
    )
    assert escrito["ciudadanos_independientes"] == 1
    assert escrito["publico"] is False
    uno = refrescar([senal(1, dispositivo="d1", red="4g")])
    assert escrito["confidence"] == pytest.approx(uno["confidence"])


def test_tres_vecinos_publican():
    escrito = refrescar(
        [senal(i, dispositivo=f"d{i}", red=f"r{i}", minuto=i) for i in range(3)]
    )
    assert escrito["ciudadanos_independientes"] == 3
    assert escrito["publico"] is True


def test_con_el_freno_puesto_tres_vecinos_no_publican():
    escrito = refrescar(
        [senal(i, dispositivo=f"d{i}", red=f"r{i}", minuto=i) for i in range(3)],
        freno=True,
    )
    assert escrito["publico"] is False


def test_un_vecino_mas_otra_fuente_publica():
    escrito = refrescar(
        [senal(1, dispositivo="d", red="r"), senal(2, fuente=EventSource.NASA_FIRMS)]
    )
    assert escrito["publico"] is True
    assert set(escrito["sources"]) == {"citizen", "nasa_firms"}


def test_el_freno_se_mide_una_vez_por_pasada(monkeypatch):
    monkeypatch.setattr(settings, "CITIZEN_GLOBAL_BRAKE_PER_10MIN", 5)
    engine, _ = motor_con([])
    engine.repo.count_recent_citizen_reports = AsyncMock(return_value=6)
    resultado = SimpleNamespace(warnings=[])
    assert asyncio.run(engine._freno_global(resultado, now=AHORA)) is True
    assert "freno ciudadano" in resultado.warnings[0]

    engine.repo.count_recent_citizen_reports = AsyncMock(return_value=5)
    assert asyncio.run(engine._freno_global(resultado, now=AHORA)) is False


def test_el_freno_en_cero_no_consulta_nada(monkeypatch):
    monkeypatch.setattr(settings, "CITIZEN_GLOBAL_BRAKE_PER_10MIN", 0)
    engine, _ = motor_con([])
    engine.repo.count_recent_citizen_reports = AsyncMock()
    assert asyncio.run(engine._freno_global(SimpleNamespace(warnings=[]), now=AHORA)) is False
    engine.repo.count_recent_citizen_reports.assert_not_called()


# --- Texto --------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "llamen al +56 9 8765 4321 urgente",
        "mi número 987654321",
        "escríbanme a vecino@gmail.com",
        "el dueño es 12.345.678-9",
        "miren https://ejemplo.cl/foto",
        "sale en www.algo.com",
        "según @vecino_chismoso",
    ],
)
def test_el_filtro_rechaza_datos_personales_y_enlaces(texto):
    assert prefiltro(texto) is not None


@pytest.mark.parametrize(
    "texto",
    [
        "Humo negro en el cerro Esperanza, subida Ecuador 1450",
        "Choque en 1 Norte con Libertad, hay 2 autos",
        "Incendio de pastizales desde las 14:30 del 30-09-2026",
        "Se ve fuego sobre la ruta 68 km 12",
        "Bomberos ya llegó, el viento va hacia Forestal",
    ],
)
def test_el_filtro_deja_pasar_lo_que_describe_una_emergencia(texto):
    assert prefiltro(texto) is None


def test_el_texto_ciudadano_solo_sale_aprobado():
    for estado, esperado in [(PENDIENTE, (None, True)), (RECHAZADO, (None, True))]:
        raw = {"_moderacion": {"estado": estado}}
        assert texto_publico(EventSource.CITIZEN, raw, "hola") == esperado
    assert texto_publico(EventSource.CITIZEN, {}, "hola") == (None, True)
    raw = {"_moderacion": {"estado": APROBADO}}
    assert texto_publico(EventSource.CITIZEN, raw, "hola") == ("hola", False)


def test_las_demas_fuentes_no_pasan_por_la_moderacion():
    assert texto_publico(EventSource.MEDIA, {}, "titular") == ("titular", False)


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ('{"apto": true, "motivo": "describe humo"}', (True, "describe humo")),
        ('```json\n{"apto": false, "motivo": "nombre propio"}\n```', (False, "nombre propio")),
        ('{"apto": "sí"}', None),
        ("no es json", None),
    ],
)
def test_parse_del_veredicto(crudo, esperado):
    assert moderacion.parse_veredicto(crudo) == esperado


def evento_pendiente(texto: str) -> Any:
    return SimpleNamespace(
        text=texto, raw_data={"channel": "pwa", "_moderacion": {"estado": PENDIENTE}}
    )


def correr_moderacion(monkeypatch, eventos, veredictos) -> tuple[Any, Any]:
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(settings, "CITIZEN_MODERATION_ENABLED", True)
    respuestas = iter(veredictos)
    monkeypatch.setattr(moderacion, "veredicto_gemini", AsyncMock(side_effect=lambda _t: next(respuestas)))
    repo = MagicMock()
    repo.pending_moderation = AsyncMock(return_value=eventos)
    monkeypatch.setattr(moderacion, "EventRepository", lambda _s: repo)
    session = MagicMock()
    session.commit = AsyncMock()
    pasada = asyncio.run(moderacion.moderar_pendientes(session, ahora=AHORA))
    return pasada, session


def test_gemini_aprueba_y_rechaza(monkeypatch):
    bueno, malo = evento_pendiente("humo en el cerro"), evento_pendiente("el vecino Juan quemó")
    pasada, session = correr_moderacion(
        monkeypatch, [bueno, malo], [(True, "describe humo"), (False, "nombre propio")]
    )
    assert bueno.raw_data["_moderacion"]["estado"] == APROBADO
    assert bueno.raw_data["_moderacion"]["por"] == "gemini"
    assert malo.raw_data["_moderacion"]["estado"] == RECHAZADO
    assert (pasada.aprobados, pasada.rechazados) == (1, 1)
    session.commit.assert_awaited_once()


def test_si_gemini_no_responde_el_texto_queda_oculto_y_la_pasada_se_corta(monkeypatch):
    uno, dos = evento_pendiente("humo"), evento_pendiente("fuego")
    pasada, session = correr_moderacion(monkeypatch, [uno, dos], [None, (True, "x")])
    assert uno.raw_data["_moderacion"]["estado"] == PENDIENTE
    assert dos.raw_data["_moderacion"]["estado"] == PENDIENTE
    assert pasada.sin_respuesta == 1
    session.commit.assert_not_awaited()


def test_sin_clave_de_gemini_no_se_modera_nada(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    pasada = asyncio.run(moderacion.moderar_pendientes(MagicMock(), ahora=AHORA))
    assert pasada.omitida == "sin GEMINI_API_KEY"


# --- Cupo por dispositivo y por red ---------------------------------------------


def servicio_con_reportes(por_dispositivo: list[datetime], por_red: list[datetime]):
    servicio = IngestService(MagicMock())

    async def reportes(*, since, dispositivo=None, red=None):
        return por_dispositivo if dispositivo else por_red

    servicio.repo = MagicMock()
    servicio.repo.citizen_reports_since = reportes
    return servicio


def espera(servicio) -> int | None:
    return asyncio.run(
        servicio.citizen_retry_after(huellas={"dispositivo": "d", "red": "r"}, ahora=AHORA)
    )


def test_el_primer_reporte_del_dispositivo_pasa(monkeypatch):
    monkeypatch.setattr(settings, "CITIZEN_REPORT_MIN_INTERVAL_SECONDS", 600)
    assert espera(servicio_con_reportes([], [])) is None


def test_el_segundo_reporte_del_dispositivo_espera_lo_que_falta(monkeypatch):
    monkeypatch.setattr(settings, "CITIZEN_REPORT_MIN_INTERVAL_SECONDS", 600)
    hace_4_min = AHORA - timedelta(minutes=4)
    segundos = espera(servicio_con_reportes([hace_4_min], [hace_4_min]))
    assert segundos is not None and 355 <= segundos <= 361


def test_una_red_admite_varios_vecinos_hasta_su_cupo(monkeypatch):
    """Una familia o una oficina no son spam; el cuarto de la misma red, sí."""
    monkeypatch.setattr(settings, "CITIZEN_REPORT_MIN_INTERVAL_SECONDS", 600)
    monkeypatch.setattr(settings, "CITIZEN_REPORTS_PER_NETWORK", 3)
    red = [AHORA - timedelta(minutes=m) for m in (9, 5, 1)]
    assert espera(servicio_con_reportes([], red[1:])) is None
    segundos = espera(servicio_con_reportes([], red))
    assert segundos is not None and segundos <= 61


def test_con_la_ventana_en_cero_no_hay_cupo(monkeypatch):
    monkeypatch.setattr(settings, "CITIZEN_REPORT_MIN_INTERVAL_SECONDS", 0)
    assert espera(servicio_con_reportes([AHORA], [AHORA] * 9)) is None


# --- El endpoint ----------------------------------------------------------------


class ServicioFalso:
    def __init__(self, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        self.recibido: dict[str, Any] | None = None

    async def citizen_retry_after(self, *, huellas):
        return self.retry_after

    async def ingest_citizen_report(self, report, *, huellas, moderacion):
        evento = report.to_event_create(huellas=huellas, moderacion=moderacion, ahora=AHORA)
        self.recibido = {"evento": evento, "huellas": huellas, "moderacion": moderacion}
        return SimpleNamespace(
            id=1,
            public_id=uuid4(),
            ingested_at=AHORA,
            processed_at=None,
            incident_id=None,
            commune=None,
            province=None,
            **evento.to_orm_kwargs(),
        )


@pytest.fixture
def cliente_con(monkeypatch):
    from app.api.v1.endpoints import events

    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")
    monkeypatch.setattr(events.citizen_report_limiter, "interval_seconds", 0)

    def armar(servicio: ServicioFalso) -> TestClient:
        app.dependency_overrides[get_ingest_service] = lambda: servicio
        return TestClient(app)

    yield armar
    app.dependency_overrides.pop(get_ingest_service, None)


def cuerpo(**cambios) -> dict[str, Any]:
    return {
        "lat": VINA[0],
        "lon": VINA[1],
        "accuracy_m": 30,
        "category": "fire",
        "text": "Humo en el cerro",
        "device_id": "dispositivo-de-prueba-1",
        **cambios,
    }


URL = "/api/v1/events/citizen-report"


def test_un_reporte_valido_entra_con_huellas_y_pendiente(cliente_con):
    servicio = ServicioFalso()
    respuesta = cliente_con(servicio).post(URL, json=cuerpo())
    assert respuesta.status_code == 201, respuesta.text
    assert servicio.recibido is not None
    raw = servicio.recibido["evento"].raw_data
    assert raw["_moderacion"]["estado"] == PENDIENTE
    assert set(raw["_ciudadano"]) == {"dispositivo", "red", "turnstile"}
    assert "dispositivo-de-prueba-1" not in str(raw), "se guarda la huella, no el valor"
    # Quien reporta no recibe las huellas de vuelta.
    assert "_ciudadano" not in respuesta.json()["raw_data"]


def test_la_hora_la_fija_el_servidor(cliente_con):
    servicio = ServicioFalso()
    cliente_con(servicio).post(URL, json=cuerpo(reported_at="2020-01-01T00:00:00Z"))
    assert servicio.recibido["evento"].timestamp == AHORA


def test_un_texto_con_telefono_entra_rechazado(cliente_con):
    servicio = ServicioFalso()
    cliente_con(servicio).post(URL, json=cuerpo(text="llamen al 9 8765 4321"))
    assert servicio.recibido["moderacion"]["estado"] == RECHAZADO
    assert servicio.recibido["moderacion"]["por"] == "filtro"


@pytest.mark.parametrize(("lat", "lon"), [(-33.45, -70.65 + 1.5), (-36.8, -73.05), (0, 0)])
def test_fuera_de_la_region_es_422(cliente_con, lat, lon):
    respuesta = cliente_con(ServicioFalso()).post(URL, json=cuerpo(lat=lat, lon=lon))
    assert respuesta.status_code == 422
    assert "fuera de la Región de Valparaíso" in respuesta.json()["detail"]


def test_un_gps_impreciso_es_422(cliente_con):
    respuesta = cliente_con(ServicioFalso()).post(URL, json=cuerpo(accuracy_m=5000))
    assert respuesta.status_code == 422
    assert "±5.000 m" in respuesta.json()["detail"]


def test_sin_precision_es_422(cliente_con):
    datos = cuerpo()
    del datos["accuracy_m"]
    assert cliente_con(ServicioFalso()).post(URL, json=datos).status_code == 422


def test_sin_cupo_es_429_con_retry_after(cliente_con):
    respuesta = cliente_con(ServicioFalso(retry_after=321)).post(URL, json=cuerpo())
    assert respuesta.status_code == 429
    assert respuesta.headers["Retry-After"] == "321"


def test_turnstile_rechazado_es_403(cliente_con, monkeypatch):
    monkeypatch.setattr(
        turnstile,
        "verificar",
        AsyncMock(return_value=turnstile.TurnstileResult(permitido=False, verificado=False)),
    )
    servicio = ServicioFalso()
    respuesta = cliente_con(servicio).post(URL, json=cuerpo(turnstile_token="malo"))
    assert respuesta.status_code == 403
    assert servicio.recibido is None


def test_turnstile_verificado_queda_anotado(cliente_con, monkeypatch):
    monkeypatch.setattr(
        turnstile,
        "verificar",
        AsyncMock(return_value=turnstile.TurnstileResult(permitido=True, verificado=True)),
    )
    servicio = ServicioFalso()
    cliente_con(servicio).post(URL, json=cuerpo(turnstile_token="bueno"))
    assert servicio.recibido["huellas"]["turnstile"] == "si"


def test_un_device_id_raro_es_422(cliente_con):
    respuesta = cliente_con(ServicioFalso()).post(URL, json=cuerpo(device_id="<script>"))
    assert respuesta.status_code == 422


# --- Turnstile ------------------------------------------------------------------


def test_turnstile_sin_secreto_deja_pasar(monkeypatch):
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "")
    resultado = asyncio.run(turnstile.verificar(None))
    assert resultado.permitido and not resultado.verificado


def test_turnstile_con_secreto_y_sin_token_rechaza(monkeypatch):
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "secreto")
    assert asyncio.run(turnstile.verificar("")).permitido is False


def test_turnstile_consulta_a_cloudflare(monkeypatch):
    import httpx
    import respx

    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "secreto")
    with respx.mock:
        ruta = respx.post(settings.TURNSTILE_VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        assert asyncio.run(turnstile.verificar("tok", ip="1.2.3.4")).verificado is True
        assert b"remoteip=1.2.3.4" in ruta.calls[0].request.content

        respx.post(settings.TURNSTILE_VERIFY_URL).mock(
            return_value=httpx.Response(200, json={"success": False, "error-codes": ["x"]})
        )
        assert asyncio.run(turnstile.verificar("tok")).permitido is False


def test_si_cloudflare_se_cae_el_reporte_pasa(monkeypatch):
    """Una caída de un tercero no puede impedir reportar un incendio."""
    import httpx
    import respx

    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "secreto")
    with respx.mock:
        respx.post(settings.TURNSTILE_VERIFY_URL).mock(side_effect=httpx.ConnectError("caído"))
        resultado = asyncio.run(turnstile.verificar("tok"))
    assert resultado.permitido is True and resultado.verificado is False


# --- Ficha pública ----------------------------------------------------------------


def detalle_de(incidente: Any, pares: list[tuple[Any, Any]]):
    from app.services.incident_service import IncidentService

    servicio = IncidentService(MagicMock())
    servicio.repo = MagicMock()
    servicio.repo.get_by_code = AsyncMock(return_value=incidente)
    servicio.repo.links_with_events = AsyncMock(return_value=pares)
    return asyncio.run(servicio.get_detail(code="INC-2026-00001"))


def incidente(*, publico: bool) -> Any:
    return SimpleNamespace(
        id=1,
        public_id=uuid4(),
        code="INC-2026-00001",
        type=IncidentType.POSSIBLE_FIRE,
        status=IncidentStatus.ACTIVE,
        lat=VINA[0],
        lon=VINA[1],
        confidence=0.6,
        alert_confidence=0.0,
        alert_level=None,
        is_official_confirmed=False,
        confidence_breakdown={},
        event_count=3,
        source_count=1,
        sources=["citizen"],
        title="Posible incendio",
        commune="Viña del Mar",
        province=None,
        first_seen_at=AHORA,
        last_seen_at=AHORA,
        resolved_at=None,
        correlated_at=AHORA,
        publico=publico,
        ciudadanos_independientes=3,
    )


def test_un_incidente_sin_quorum_no_tiene_ficha():
    assert detalle_de(incidente(publico=False), []) is None


def test_la_ficha_oculta_el_texto_pendiente_y_redondea_el_gps():
    from app.models.enums import LinkMethod

    evento = SimpleNamespace(
        id=7,
        public_id=uuid4(),
        source=EventSource.CITIZEN,
        type=EventType.SMOKE,
        timestamp=AHORA,
        confidence=0.4,
        text="el vecino Juan quemó basura",
        lat=-33.024567,
        lon=-71.551834,
        raw_data={"_moderacion": {"estado": PENDIENTE}},
    )
    enlace = SimpleNamespace(
        link_method=LinkMethod.SPATIAL,
        link_confidence=1.0,
        distance_m=0.0,
        matched_commune=None,
        note=None,
    )
    detalle = detalle_de(incidente(publico=True), [(enlace, evento)])
    assert detalle is not None
    senal_publica = detalle.events[0]
    assert senal_publica.text is None
    assert senal_publica.texto_en_revision is True
    assert (senal_publica.lat, senal_publica.lon) == (-33.025, -71.552)


def test_la_api_publica_filtra_lo_no_publicado():
    """`_apply_filters` es la puerta de `/incidents/active`, `/geojson` y el historial."""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from app.models.incident import Incident
    from app.repositories.incident_repository import IncidentRepository

    stmt = IncidentRepository(MagicMock())._apply_filters(
        select(Incident),
        since=None,
        statuses=None,
        types=None,
        min_confidence=None,
        commune=None,
        bbox=None,
        confirmed_only=False,
        with_alert_only=False,
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "incidents.publico IS true" in sql


def test_la_migracion_0018_encadena_con_la_0017():
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).parents[1] / "migrations" / "versions" / "0018_quorum_ciudadano.py"
    spec = importlib.util.spec_from_file_location("m0018", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    assert modulo.revision == "0018_quorum_ciudadano"
    assert modulo.down_revision == "0017_rejilla_lluvia"


def test_ciudadanos_no_toca_la_red_ni_la_base():
    """El módulo de reglas se puede importar y usar sin sesión."""
    assert not hasattr(ciudadanos, "AsyncSession")
    schema = CitizenReportCreate(
        lat=VINA[0], lon=VINA[1], accuracy_m=10, text="humo", category=ReportCategory.FIRE
    )
    assert schema.to_event_create().raw_data["_moderacion"]["estado"] == PENDIENTE
