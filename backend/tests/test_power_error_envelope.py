"""Errores que un servidor sirve con HTTP 2xx.

El incidente
------------
El 25 de agosto de 2026 el collector de Chilquinta empezó a fallar con::

    chilquinta: no se encontró la lista de órdenes en el volcado
    (objeto con claves: ['code', 'message', 'status']).
    Si el archivo es el correcto, hay que agregar su clave a `_LIST_KEYS`…

`{"code", "message", "status"}` no es un volcado con otra forma: es el sobre de
error de una pasarela, servido **con HTTP 200** para que el transporte lo deje
pasar como si fuera bueno.

El mensaje tenía dos defectos, y el segundo era el grave:

1. Tiraba el `message` del servidor, que era lo único que explicaba qué pasaba.
2. Recomendaba añadir `code`/`message`/`status` a `_LIST_KEYS`. Seguir ese
   consejo habría hecho que el collector tratara el texto de un error como una
   lista de cortes.

Estos tests fijan que ninguno de los dos vuelva.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.collectors.geoservices import ServiceErrorEnvelope, detect_service_error
from app.collectors.power.chilquinta_worker import ChilquintaCollector
from app.collectors.power.outage_parser import records_or_raise
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus

URL = "https://mapainterrupciones.chilquinta.cl/dt/results_006.js"

#: El cuerpo exacto que el log de producción dejó registrado.
PAYLOAD_DEL_INCIDENTE = {"code": 403, "message": "Forbidden", "status": "error"}


# --- El detector -------------------------------------------------------------


def test_reconoce_el_payload_exacto_del_incidente():
    error = detect_service_error(PAYLOAD_DEL_INCIDENTE)

    assert error is not None
    assert error.message == "Forbidden"
    assert error.code == 403
    assert error.keys == ("code", "message", "status")


@pytest.mark.parametrize(
    ("payload", "codigo", "transitorio"),
    [
        ({"code": 429, "message": "Too Many Requests"}, 429, True),
        ({"code": 503, "message": "Service Unavailable"}, 503, True),
        ({"code": 502, "message": "Bad Gateway"}, 502, True),
        ({"code": 404, "message": "Not Found"}, 404, False),
        ({"code": 403, "message": "Forbidden"}, 403, False),
        ({"code": 401, "message": "No autorizado"}, 401, False),
    ],
)
def test_clasifica_lo_que_se_cura_solo_y_lo_que_no(payload, codigo, transitorio):
    """La distinción decide si alguien tiene que hacer algo ahora.

    Un 429 se resuelve esperando la próxima corrida. Un 404 significa que el
    archivo se movió y no hay cadencia que lo arregle.
    """
    error = detect_service_error(payload)

    assert error.code == codigo
    assert error.transient is transitorio


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "error", "message": "Mantenimiento programado"},
        {"error": "Acceso denegado"},
        {"detail": "Not authenticated"},
        {"mensaje": "Servicio no disponible"},
        {"error_description": "invalid_token"},
        {"errorMessage": "Internal failure"},
    ],
)
def test_reconoce_las_variantes_de_sobre_que_usan_otros_servidores(payload):
    """No todos escriben `message`, y el que no se reconozca vuelve al bug."""
    assert detect_service_error(payload) is not None


def test_un_status_de_texto_no_se_confunde_con_un_codigo():
    """Muchos servidores ponen `status: "error"` y el número en `code`."""
    error = detect_service_error({"status": "error", "message": "algo"})
    assert error.code is None
    assert error.transient is False


# --- La guarda contra falsos positivos ---------------------------------------
#
# Es lo que hace seguro llamar al detector desde collectors que hoy funcionan.


@pytest.mark.parametrize(
    ("payload", "por_que"),
    [
        ({"ordenes_totales": 29, "ordenes": [{"orden": "1"}]}, "volcado de Chilquinta"),
        ({"data": []}, "sin cortes ahora mismo"),
        ({"features": [], "type": "FeatureCollection"}, "GeoJSON vacío"),
        ({"original": {"ordenes": []}}, "sobre anidado de un nivel"),
        ({"status": "ok", "message": "consulta exitosa", "data": [{"x": 1}]}, "datos con mensaje"),
        ([], "una lista suelta"),
        ({}, "objeto vacío"),
        ("texto suelto", "no es un objeto"),
        (None, "nada"),
    ],
)
def test_los_datos_legitimos_nunca_se_confunden_con_un_error(payload, por_que):
    """Si hay cualquier lista dentro, no es un error.

    Un sobre de error no trae colecciones; un volcado de cortes siempre trae la
    suya, aunque venga vacía. Esa asimetría es toda la guarda, y es la que
    permite añadir esta comprobación a un camino que ya funcionaba sin miedo a
    romperlo.
    """
    assert detect_service_error(payload) is None, por_que


def test_un_mensaje_junto_a_los_datos_no_dispara_el_detector():
    """El caso que más se parece a un falso positivo.

    Una respuesta legítima puede traer `status` y `message` de cortesía. Lo que
    la distingue es que además trae los datos.
    """
    assert detect_service_error({"status": "ok", "message": "ok", "ordenes": []}) is None


# --- El mensaje que ve el operador -------------------------------------------


def test_ya_no_se_recomienda_tocar_list_keys_ante_un_error_del_servidor():
    """El defecto grave del incidente: un consejo que habría empeorado las cosas.

    Añadir `code`, `message` o `status` a `_LIST_KEYS` haría que el collector
    tratara el texto de un error como una lista de cortes.
    """
    with pytest.raises(CollectorError) as exc:
        records_or_raise(PAYLOAD_DEL_INCIDENTE, company="chilquinta", url=URL)

    assert "_LIST_KEYS" not in str(exc.value)
    assert "no toques el parser" in str(exc.value)


def test_el_mensaje_del_servidor_llega_al_operador():
    """Era lo único que explicaba el problema, y se estaba descartando."""
    with pytest.raises(CollectorError) as exc:
        records_or_raise(
            {"code": 503, "message": "Backend en mantención hasta las 22:00"},
            company="chilquinta",
            url=URL,
        )

    assert "Backend en mantención hasta las 22:00" in str(exc.value)


def test_ante_un_cambio_de_esquema_real_el_consejo_sigue_estando():
    """La otra mitad: no se puede perder el diagnóstico que sí servía.

    Un volcado con la lista bajo otra clave es exactamente el caso para el que
    `_LIST_KEYS` existe.
    """
    with pytest.raises(CollectorError) as exc:
        records_or_raise(
            {"total": 3, "listado": [{"orden": "1"}]}, company="chilquinta", url=URL
        )

    assert "_LIST_KEYS" in str(exc.value)
    assert "listado" in str(exc.value)


def test_el_detalle_estructurado_permite_alertar_sin_leer_prosa():
    """`collector_runs.error` es texto, pero `detail` es consultable."""
    with pytest.raises(CollectorError) as exc:
        records_or_raise(
            {"code": 429, "message": "Too Many Requests"}, company="chilquinta", url=URL
        )

    detalle = exc.value.detail
    assert detalle["server_code"] == 429
    assert detalle["transient"] is True


def test_describe_es_legible_de_un_vistazo():
    error = ServiceErrorEnvelope(message="Forbidden", code=403, transient=False)
    texto = error.describe()

    assert "Forbidden" in texto
    assert "403" in texto
    assert "requiere revisión" in texto


# --- El ciclo completo, como corre en producción -----------------------------


class FakeIngestService:
    """Sustituye a `IngestService` para observar el estado final de la corrida."""

    def __init__(self) -> None:
        self.status: CollectorStatus | None = None
        self.error: str | None = None

    async def start_run(self, **_kwargs) -> object:
        return object()

    async def finish_run(self, _run, *, status, fetched=0, inserted=0,
                         duplicate=0, error=None) -> None:
        self.status, self.error = status, error

    async def ingest_batch(self, events):
        return type("Ingest", (), {"inserted": len(events), "duplicated": 0})()


def collector() -> ChilquintaCollector:
    instancia = ChilquintaCollector.__new__(ChilquintaCollector)
    instancia.url = URL
    instancia.bbox = settings.region_bbox
    instancia.service = FakeIngestService()
    return instancia


def correr(cuerpo: str):
    respx.get(host="mapainterrupciones.chilquinta.cl").mock(
        return_value=httpx.Response(200, text=cuerpo)
    )
    return asyncio.run(collector().run())


@respx.mock
def test_el_incidente_completo_no_propaga_al_orquestador():
    """La corrección de fondo del diagnóstico original.

    El CRON **no se detuvo**: `BaseCollector.run()` atrapa `CollectorError`, deja
    la corrida en `failed` y devuelve normalmente. El `logger.exception` del log
    sale del propio manejador, no de una excepción que escapara.
    """
    resultado = correr(
        'eqfeed_callback({"code":403,"message":"Forbidden","status":"error"})'
    )

    assert resultado.status is CollectorStatus.FAILED
    assert resultado.inserted == 0
    assert "Forbidden" in resultado.error
    assert "_LIST_KEYS" not in resultado.error


@respx.mock
def test_un_error_transitorio_se_anuncia_como_tal():
    resultado = correr(
        'eqfeed_callback({"code":429,"message":"Too Many Requests","status":"error"})'
    )

    assert resultado.status is CollectorStatus.FAILED
    assert "transitorio" in resultado.error
    assert "cinco minutos" in resultado.error


@respx.mock
def test_un_error_permanente_dice_qué_comprobar():
    resultado = correr('eqfeed_callback({"code":404,"message":"Not Found"})')

    assert "requiere revisión" in resultado.error
    assert URL in resultado.error, "el mensaje nombra el archivo que hay que buscar"


@respx.mock
def test_el_camino_feliz_sigue_intacto():
    """La comprobación nueva no puede estorbar a una respuesta buena."""
    cuerpo = (
        'eqfeed_callback({"headers":{},"original":{"ordenes_totales":1,"ordenes":['
        '{"orden":"10209025","etr":"27-08-2026 17:00:00","cant_clientes":"68",'
        '"comuna":"VALPARAISO","segmentos":[[{"latitud_min":-33.045,'
        '"longitud_min":-71.62}]]}]}})'
    )

    resultado = correr(cuerpo)

    assert resultado.status is CollectorStatus.SUCCESS
    assert resultado.error is None


@respx.mock
def test_un_volcado_sin_cortes_sigue_siendo_una_noche_tranquila():
    """Cero cortes no es un error, y el detector no puede convertirlo en uno."""
    resultado = correr(
        'eqfeed_callback({"headers":{},"original":{"ordenes_totales":0,"ordenes":[]}})'
    )

    assert resultado.status is CollectorStatus.SUCCESS
    assert resultado.fetched == 0
