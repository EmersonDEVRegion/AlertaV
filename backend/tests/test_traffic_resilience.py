"""Resiliencia de los workers de tránsito ante fuentes ajenas que fallan.

El portal del MTT es un WordPress que puede rediseñarse cualquier martes. El
requisito operativo es categórico: **bajo ninguna circunstancia una caída de
esta fuente puede tumbar el orquestador**. (El lector RSS de Bomberos, que
compartía estos tests, se borró el 2026-09-23.)

Esa garantía tiene dos mitades y las dos se prueban acá:

1. `fetch()` convierte *cualquier* fallo en `CollectorError`. Ni un timeout, ni
   un 500, ni un HTML patológico escapan con otro tipo.
2. `BaseCollector.run()` atrapa esa excepción, la registra en `collector_runs` y
   devuelve un `CollectorResult` con estado `failed` y cero eventos. El llamador
   —el runner, y por encima `app/workers.py`— nunca ve una excepción.

Sobre por qué el estado es `failed` y no `success`
--------------------------------------------------
Un collector que devolviera lista vacía con estado `success` ante una caída
sería indistinguible, mirando los datos, de una noche sin rescates. Ese es el
modo de fallo que este proyecto persigue en todos sus módulos: el hueco existe,
pero nadie lo ve, y se descubre semanas después cuando alguien pregunta por qué
no hay accidentes registrados desde el 12 de agosto. La lista de eventos SÍ
queda vacía —que es lo que importa para no inventar datos— pero la corrida queda
marcada.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.collectors.traffic.transporteinforma_worker import (
    TransporteInformaCollector,
    page_looks_broken,
    parse_notices,
)
from app.core.exceptions import CollectorError
from app.models.enums import CollectorStatus

PORTAL_URL = "https://portal.test/valparaiso/"


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    """Anula el backoff entre reintentos.

    `request_response` espera 1,5 s y luego 3 s antes de rendirse, que es lo
    correcto contra un servicio real y absurdo contra un mock: sumaba 77
    segundos a la suite. Se anula el sueño, no los reintentos — lo que se
    verifica es cuántas veces se llama, no cuánto se espera.
    """
    import app.collectors.geoservices as geoservices

    async def sin_dormir(_seconds: float) -> None:
        return None

    monkeypatch.setattr(geoservices.asyncio, "sleep", sin_dormir)


# --- Dobles de prueba --------------------------------------------------------


class FakeRepo:
    """Base vacía: el delta no conoce ningún aviso."""

    async def puntos_conocidos(self, _source, _external_ids):
        return {}


class FakeIngestService:
    """Sustituye a `IngestService` para no necesitar base de datos.

    Registra lo que `BaseCollector.run()` le pide, que es exactamente lo que se
    quiere observar: con qué estado terminó la corrida y con qué error.
    """

    def __init__(self) -> None:
        self.status: CollectorStatus | None = None
        self.error: str | None = None
        self.inserted = 0
        self.repo = FakeRepo()

    async def start_run(self, **_kwargs) -> object:
        return object()

    async def finish_run(self, _run, *, status, fetched=0, inserted=0,
                         duplicate=0, error=None) -> None:
        self.status = status
        self.error = error

    async def ingest_batch(self, events):
        self.inserted = len(events)
        return type("Ingest", (), {"inserted": len(events), "duplicated": 0})()


def mtt(url: str = PORTAL_URL) -> TransporteInformaCollector:
    collector = TransporteInformaCollector.__new__(TransporteInformaCollector)
    collector.url = url
    collector.max_geocodes = 5
    collector.max_llm_calls = 5
    collector.service = FakeIngestService()
    return collector


def correr(collector):
    """Ejecuta el ciclo completo del collector, como lo haría el runner."""
    return asyncio.run(collector.run())


# --- 2. Fallos del portal del MTT --------------------------------------------


@respx.mock
@pytest.mark.parametrize("status_code", [500, 503])
def test_mtt_un_5xx_no_escapa_como_excepcion(status_code):
    respx.get(PORTAL_URL).mock(return_value=httpx.Response(status_code))

    resultado = correr(mtt())

    assert resultado.status is CollectorStatus.FAILED
    assert resultado.inserted == 0


@respx.mock
def test_mtt_un_timeout_no_escapa_como_excepcion():
    respx.get(PORTAL_URL).mock(side_effect=httpx.ReadTimeout("agotado"))

    resultado = correr(mtt())
    assert resultado.status is CollectorStatus.FAILED


@respx.mock
def test_mtt_un_dom_irreconocible_se_avisa_sin_perder_la_corrida():
    """El rediseño es el fallo que más tarda en detectarse.

    No hay excepción, no hay 500: la página carga perfecto y el scraper deja de
    ver avisos. Sólo un aviso explícito convoca a la persona que tiene que mirar.
    """
    respx.get(PORTAL_URL).mock(
        return_value=httpx.Response(
            200, text="<html><body><main><p>Bienvenido</p></main></body></html>"
        )
    )

    resultado = correr(mtt())

    assert resultado.status is CollectorStatus.PARTIAL
    assert resultado.inserted == 0
    assert "estructura del portal cambió" in (resultado.error or "")


@respx.mock
def test_mtt_una_respuesta_que_no_es_html_se_detecta():
    respx.get(PORTAL_URL).mock(return_value=httpx.Response(200, text="{}"))

    resultado = correr(mtt())
    assert resultado.status is CollectorStatus.PARTIAL


# --- 3. La garantía de fondo -------------------------------------------------


@respx.mock
@pytest.mark.parametrize(
    "fallo",
    [
        httpx.ConnectTimeout("timeout"),
        httpx.ReadTimeout("timeout"),
        httpx.ConnectError("dns"),
        httpx.TooManyRedirects("bucle"),
        httpx.RemoteProtocolError("protocolo"),
    ],
)
def test_ningun_fallo_de_red_escapa_de_fetch(fallo):
    """`fetch()` falla de UNA sola forma: `CollectorError`.

    Es el contrato del que depende todo lo demás. Si algún día se escapara otro
    tipo, `BaseCollector.run()` igual lo atraparía —tiene un `except Exception`
    de último recurso— pero el error llegaría a `collector_runs` sin el contexto
    que este módulo sabe agregar.
    """
    respx.get(PORTAL_URL).mock(side_effect=fallo)

    with pytest.raises(CollectorError):
        asyncio.run(mtt().fetch())


@respx.mock
def test_el_orquestador_sobrevive_a_la_fuente_caida():
    """La prueba que importa operativamente.

    Con la fuente caída, `run()` devuelve normalmente y el runner puede seguir
    con el resto de los collectors.
    """
    respx.get(PORTAL_URL).mock(side_effect=httpx.ConnectError("caída"))

    resultados = [correr(mtt())]

    assert all(r.status is CollectorStatus.FAILED for r in resultados)
    assert all(r.inserted == 0 for r in resultados)
    # Ninguna excepción llegó hasta acá: si hubiera escapado, el test habría
    # terminado en error antes de esta línea.


# --- 4. El scraper del MTT sobre HTML realista -------------------------------

HTML_PORTAL = """
<html><body>
  <header><div class="elementor-widget-container">Menú de navegación</div></header>
  <main>
    <article>
      <div class="elementor-widget-container">
        <div class="elementor-widget-container">
          Precaución: accidente vehicular en Av. España con Uno Norte,
          Viña del Mar. Tránsito lento hacia el poniente.
        </div>
      </div>
    </article>
    <article>
      <div class="elementor-widget-container">
        Restricción vehicular para patentes 1 y 2 el día de hoy en Valparaíso.
      </div>
    </article>
    <article>
      <div class="elementor-widget-container">
        Colisión múltiple en Ruta 68 a la altura del kilómetro 42. Se recomienda
        precaución.
      </div>
    </article>
    <div class="elementor-widget-container">Suscríbete a nuestro boletín</div>
  </main>
</body></html>
"""


def test_mtt_extrae_los_bloques_con_palabras_clave():
    avisos = parse_notices(HTML_PORTAL)
    textos = [aviso.text for aviso in avisos]

    assert any("Av. España" in t for t in textos)
    assert any("Ruta 68" in t for t in textos)
    assert any("Restricción vehicular" in t for t in textos)
    assert not any("boletín" in t for t in textos), "el pie no es un aviso"
    assert not any("navegación" in t for t in textos), "el menú tampoco"


def test_mtt_no_duplica_por_los_contenedores_anidados():
    """Elementor anida contenedores: el mismo aviso aparece en varios niveles.

    Sin deduplicar, un accidente entraría tres veces y el motor lo leería como
    tres corroboraciones independientes del mismo hecho, inflando su confianza
    con evidencia que es una sola.
    """
    avisos = parse_notices(HTML_PORTAL)
    textos = [a.text for a in avisos]

    espana = [t for t in textos if "Av. España" in t]
    assert len(espana) == 1, f"el aviso se duplicó por anidamiento: {espana}"
    assert len(textos) == len(set(textos))


def test_mtt_cada_aviso_tiene_id_estable():
    """Idempotencia: releer el portal cada 10 min no duplica el aviso."""
    primera = {a.notice_id for a in parse_notices(HTML_PORTAL)}
    segunda = {a.notice_id for a in parse_notices(HTML_PORTAL)}
    assert primera == segunda
    assert len(primera) == len(parse_notices(HTML_PORTAL))


def test_mtt_page_looks_broken_distingue_vacio_de_roto():
    vacia_pero_valida = """<html><body><article>
      <div class="elementor-widget-container">Sin novedades de tránsito hoy.</div>
    </article></body></html>"""
    roto, _ = page_looks_broken(vacia_pero_valida)
    assert roto is False, "una jornada sin incidentes no es un DOM roto"

    roto, motivo = page_looks_broken("<html><body><p>Hola</p></body></html>")
    assert roto is True
    assert motivo


def test_mtt_las_palabras_clave_son_la_red_gruesa_no_el_filtro_final():
    """"Restricción" es un aviso de tránsito pero NO un siniestro.

    El filtro por palabras clave selecciona bloques relevantes; decidir si hay
    accidente es trabajo de `looks_like_accident`, con reglas deterministas.
    """
    from app.collectors.traffic.transporteinforma_worker import looks_like_accident

    avisos = parse_notices(HTML_PORTAL)
    restriccion = next(a for a in avisos if "Restricción" in a.text)
    accidente = next(a for a in avisos if "Av. España" in a.text)

    assert looks_like_accident(restriccion.text) is False
    assert looks_like_accident(accidente.text) is True
