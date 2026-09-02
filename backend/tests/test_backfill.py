"""El rescate de los eventos que quedaron guardados y mudos.

Los collectors sólo miran hacia adelante. Cuando la extracción de calles falla,
el evento se guarda sin coordenadas —perder el hecho por no saber dónde sería
peor— pero `cluster_unassigned_events` filtra por `geom IS NOT NULL` y esa fila
nunca llega a ser un incidente. El filtro delta (`unseen`) la condena a
quedarse así: descarta el post por `external_id` en cada corrida siguiente.

El 2026-09-02 eso tuvo nombre. El accidente de Av. España quedó en `raw_events`
con el texto completo, el tipo correcto y `lat: null`. Se arregló el extractor
esa misma noche y el evento siguió sin aparecer, porque el arreglo no alcanzaba
hacia atrás.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.enums import EventSource, EventType
from app.schemas.event import IngestResult
from app.services import backfill as modulo

AHORA = datetime.now(UTC)


def evento_mudo(
    *,
    external_id: str = "ig:DczSS2BxyJi",
    texto: str = (
        "Un accidente de tránsito se ha registrado en Av. España, "
        "a la altura del nudo Barón."
    ),
):
    """Un `RawEvent` como el 15575: completo salvo por las coordenadas."""
    from app.models.event import RawEvent

    event = RawEvent()
    event.timestamp = AHORA - timedelta(minutes=30)
    event.source = EventSource.SOCIAL_MEDIA
    event.type = EventType.ACCIDENT
    event.lat = None
    event.lon = None
    event.text = texto
    event.external_id = external_id
    event.confidence = 0.35
    event.raw_data = {"_extraction": {"mode": "gemini"}, "_geocoding": None}
    return event


class RepoFalso:
    def __init__(self, eventos):
        self.eventos = eventos
        self.consulta = None

    async def list_ungeocoded(self, *, since, limit, types=None):
        self.consulta = {"since": since, "limit": limit, "types": types}
        return self.eventos[:limit]


def montar(monkeypatch, *, eventos, punto, ingest=None):
    """Sustituye repositorio, geocodificador e ingesta. Sin red ni base."""
    repo = RepoFalso(eventos)
    ingeridos: list = []

    monkeypatch.setattr(modulo, "EventRepository", lambda _s: repo)

    class ClienteFalso:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    monkeypatch.setattr(modulo, "build_geo_client", lambda *a, **k: ClienteFalso())

    async def geocode_falso(texto, *, geo_client):
        return punto(texto)

    monkeypatch.setattr(modulo, "geocode_text", geocode_falso)

    class ServicioFalso:
        def __init__(self, _s):
            pass

        async def ingest_batch(self, events):
            ingeridos.extend(events)
            return ingest or IngestResult(
                received=len(events), inserted=0, duplicated=len(events)
            )

    monkeypatch.setattr(modulo, "IngestService", ServicioFalso)
    return repo, ingeridos


def punto_bueno(_texto):
    return (
        {"street_1": "Av. España", "city": "Valparaíso", "reference": "nudo Barón"},
        SimpleNamespace(
            lat=-33.0289,
            lon=-71.5936,
            as_dict=lambda: {"lat": -33.0289, "lon": -71.5936, "importance": 0.42},
        ),
    )


def punto_nulo(_texto):
    return ({}, None)


# --- 1. El rescate -----------------------------------------------------------


@pytest.mark.asyncio
async def test_rescata_el_evento_que_estaba_mudo(monkeypatch):
    _, ingeridos = montar(monkeypatch, eventos=[evento_mudo()], punto=punto_bueno)

    resultado = await modulo.backfill_geocoding(object())

    assert resultado.examined == 1
    assert resultado.geocoded == 1
    assert resultado.updated == 1
    assert len(ingeridos) == 1
    assert (ingeridos[0].lat, ingeridos[0].lon) == (-33.0289, -71.5936)


@pytest.mark.asyncio
async def test_conserva_el_external_id_para_actualizar_y_no_duplicar(monkeypatch):
    """El rescate entra por el upsert normal, que empareja por external_id.

    Si el id cambiara, el mismo accidente aparecería dos veces en el mapa: una
    fila muda y otra ubicada. Es el mismo defecto que ya obligó a extraer
    `dispatches_to_events` a función libre, para que las dos puertas de Bomberos
    produjeran el MISMO id.
    """
    _, ingeridos = montar(monkeypatch, eventos=[evento_mudo()], punto=punto_bueno)

    await modulo.backfill_geocoding(object())

    assert ingeridos[0].external_id == "ig:DczSS2BxyJi"
    assert ingeridos[0].source is EventSource.SOCIAL_MEDIA


@pytest.mark.asyncio
async def test_una_insercion_es_un_defecto_y_se_denuncia(monkeypatch):
    """`inserted` acá significa que el emparejamiento falló y se creó un gemelo.

    Como el resultado igual se ve «exitoso», tiene que salir en `errors` o el
    duplicado pasaría inadvertido.
    """
    montar(
        monkeypatch,
        eventos=[evento_mudo()],
        punto=punto_bueno,
        ingest=IngestResult(received=1, inserted=1, duplicated=0),
    )

    resultado = await modulo.backfill_geocoding(object())

    assert any("INSERTARON" in e for e in resultado.errors)


# --- 2. Lo que NO hace -------------------------------------------------------


@pytest.mark.asyncio
async def test_un_texto_que_sigue_sin_ubicarse_se_queda_como_estaba(monkeypatch):
    """No inventa.

    La alternativa —geocodificar al centroide de la comuna— produce un punto
    plausible y falso, y el mapa no lo distingue de un dato real. La ausencia al
    menos se ve.
    """
    _, ingeridos = montar(monkeypatch, eventos=[evento_mudo()], punto=punto_nulo)

    resultado = await modulo.backfill_geocoding(object())

    assert resultado.unresolved == 1
    assert resultado.updated == 0
    assert ingeridos == [], "sin punto no se reingresa nada"


@pytest.mark.asyncio
async def test_un_fallo_de_nominatim_no_se_lleva_el_lote(monkeypatch):
    """Los otros veinticuatro eventos no tienen la culpa de esta esquina."""

    def a_veces(texto):
        if "Barón" in texto:
            raise RuntimeError("Nominatim 503")
        return punto_bueno(texto)

    _, ingeridos = montar(
        monkeypatch,
        eventos=[evento_mudo(), evento_mudo(external_id="ig:OTRO", texto="Choque en Ruta 68.")],
        punto=a_veces,
    )

    resultado = await modulo.backfill_geocoding(object())

    assert resultado.errors, "el fallo tiene que quedar registrado"
    assert resultado.geocoded == 1, "el otro evento sí se rescató"
    assert len(ingeridos) == 1


# --- 3. Los presupuestos -----------------------------------------------------


@pytest.mark.asyncio
async def test_el_limite_es_un_presupuesto_de_tiempo_real(monkeypatch):
    """Nominatim admite 1 req/s: 25 eventos son 25 segundos de corrida.

    Subirlo mucho convierte esta llamada en algo que expira a medio camino.
    """
    repo, _ = montar(
        monkeypatch, eventos=[evento_mudo() for _ in range(50)], punto=punto_bueno
    )

    resultado = await modulo.backfill_geocoding(object(), limit=5)

    assert repo.consulta["limit"] == 5
    assert resultado.examined == 5


@pytest.mark.asyncio
async def test_solo_mira_la_ventana_que_el_mapa_muestra(monkeypatch):
    """Rescatar un evento de hace una semana no sirve: entraría como incidente
    y el motor lo marcaría rancio en la misma pasada."""
    repo, _ = montar(monkeypatch, eventos=[evento_mudo()], punto=punto_bueno)

    await modulo.backfill_geocoding(object(), hours=6)

    edad = datetime.now(UTC) - repo.consulta["since"]
    assert timedelta(hours=5, minutes=55) < edad < timedelta(hours=6, minutes=5)


@pytest.mark.asyncio
async def test_sin_pendientes_no_toca_la_red(monkeypatch):
    montar(monkeypatch, eventos=[], punto=punto_bueno)

    resultado = await modulo.backfill_geocoding(object())

    assert resultado == modulo.BackfillResult()
