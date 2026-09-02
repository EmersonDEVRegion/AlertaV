"""Extracción de calles con Gemini: alucinaciones, caídas y el event loop.

Un LLM en el camino de datos introduce una clase de fallo que el resto del
sistema no tenía: la respuesta llega, es sintácticamente plausible y es falsa.
No hay excepción, no hay 500, no hay nada que un `try` atrape por sí solo. Estos
tests fijan el comportamiento ante cada forma de esa falla.

La regla de fondo: **ante la duda, None**. Un aviso sin coordenadas entra igual
al sistema y se ve en el mapa como una señal sin marcador. Una calle inventada
geocodifica a un punto plausible que nadie va a cuestionar. El primero es un
hueco visible; el segundo, un dato falso invisible.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.collectors.traffic import gemini
from app.collectors.traffic.transporteinforma_worker import extract_streets_via_llm


@pytest.fixture(autouse=True)
def _limpiar_cliente():
    """El cliente se cachea con `lru_cache`; hay que limpiarlo entre tests."""
    gemini._client.cache_clear()
    yield
    gemini._client.cache_clear()


class RespuestaFalsa:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.prompt_feedback = "BLOCK_REASON_SAFETY" if text is None else ""


def montar_gemini(monkeypatch, *, respuesta: Any = None, error: Exception | None = None,
                  retardo: float = 0.0) -> dict[str, Any]:
    """Sustituye la llamada al SDK. Devuelve un registro de lo que se invocó."""
    registro: dict[str, Any] = {"llamadas": 0, "config": None, "contents": None}

    async def generate_content(*, model, contents, config):
        registro["llamadas"] += 1
        registro["model"] = model
        registro["contents"] = contents
        registro["config"] = config
        if retardo:
            await asyncio.sleep(retardo)
        if error is not None:
            raise error
        return respuesta

    cliente = type(
        "Cliente",
        (),
        {"aio": type("Aio", (), {"models": type("Models", (), {
            "generate_content": staticmethod(generate_content)
        })()})()},
    )()

    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(gemini, "_client", lambda: cliente)
    return registro


# --- 1. El camino feliz ------------------------------------------------------


def test_extrae_las_tres_claves(monkeypatch):
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa(
            '{"street_1": "Av. España", "street_2": "Uno Norte", "city": "Viña del Mar"}'
        ),
    )
    resultado = asyncio.run(gemini.extract_streets("Accidente en Av. España con Uno Norte"))

    assert resultado == {
        "street_1": "Av. España",
        "street_2": "Uno Norte",
        "city": "Viña del Mar",
    }


def test_el_contrato_de_salida_no_tiene_claves_de_mas(monkeypatch):
    """Exactamente tres claves: es lo que `build_query` consume."""
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa(
            '{"street_1": "Ruta 68", "street_2": null, "city": null,'
            ' "lat": -33.0, "confianza": "alta"}'
        ),
    )
    resultado = asyncio.run(gemini.extract_streets("Choque en Ruta 68"))

    assert set(resultado) == {"street_1", "street_2", "city"}
    assert "lat" not in resultado, "una coordenada del modelo no puede colarse"


# --- 2. Alucinaciones --------------------------------------------------------


@pytest.mark.parametrize(
    "crudo",
    [
        '```json\n{"street_1": "Ruta 68", "street_2": null, "city": null}\n```',
        '```\n{"street_1": "Ruta 68", "street_2": null, "city": null}\n```',
        '  {"street_1": "Ruta 68", "street_2": null, "city": null}  ',
    ],
)
def test_sobrevive_al_markdown_que_el_modelo_agrega(crudo):
    """El reflejo más común: envolver el JSON en un bloque de código.

    Se ataca en tres capas —prompt, `response_mime_type` y este limpiador—
    porque es el fallo que más veces ocurre y el más barato de tolerar.
    """
    assert gemini.parse_response(crudo) == {
        "street_1": "Ruta 68",
        "street_2": None,
        "city": None,
    }


@pytest.mark.parametrize(
    "crudo",
    [
        "Claro, aquí tienes el JSON que pediste:",  # prosa
        '{"street_1": "Ruta 68",',                   # JSON truncado
        "[]",                                        # lista, no objeto
        "null",
        "42",
        "",
        "   ",
        '{"calle": "Ruta 68"}',                      # claves equivocadas
        '{"street_1": null, "street_2": "Uno Norte", "city": "Viña"}',  # sin principal
        '{"street_1": "", "city": "Viña"}',
        '{"street_1": "N/A", "city": "Viña"}',       # el vacío disfrazado
        '{"street_1": "desconocido"}',
    ],
)
def test_una_respuesta_inservible_devuelve_none(crudo):
    """Todas estas son formas de alucinación observadas en modelos pequeños."""
    assert gemini.parse_response(crudo) is None


def test_sin_calle_principal_no_hay_extraccion(monkeypatch):
    """Quedarse con la ciudad sola daría el centroide comunal.

    Como ubicación de un accidente eso es peor que no tener ubicación: parece un
    dato y no lo es.
    """
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa('{"street_1": null, "street_2": null, "city": "Valparaíso"}'),
    )
    assert asyncio.run(gemini.extract_streets("Restricción vehicular en la región")) is None


# --- 3. Fallos de la API -----------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("500 Internal Server Error"),
        ValueError("API key inválida"),
        ConnectionError("sin red"),
        Exception("cuota agotada"),
    ],
)
def test_un_fallo_de_la_api_devuelve_none_sin_lanzar(monkeypatch, error):
    """`extract_streets` nunca lanza: el aviso entra igual, sin coordenadas."""
    montar_gemini(monkeypatch, error=error)
    assert asyncio.run(gemini.extract_streets("Accidente en Ruta 68")) is None


def test_un_timeout_no_cuelga_la_corrida(monkeypatch):
    """Cinturón sobre el timeout del SDK, por si algún día cambia su defecto."""
    monkeypatch.setattr(gemini.settings, "GEMINI_TIMEOUT_SECONDS", 0.05)
    montar_gemini(monkeypatch, respuesta=RespuestaFalsa("{}"), retardo=5.0)

    inicio = time.monotonic()
    resultado = asyncio.run(gemini.extract_streets("Accidente en Ruta 68"))

    assert resultado is None
    assert time.monotonic() - inicio < 1.0, "no esperó el timeout configurado"


def test_una_respuesta_bloqueada_por_seguridad_devuelve_none(monkeypatch):
    """Un aviso de accidente con heridos puede activar el filtro de Google."""
    montar_gemini(monkeypatch, respuesta=RespuestaFalsa(None))
    assert asyncio.run(gemini.extract_streets("Accidente con heridos graves")) is None


def test_sin_clave_no_se_llama_a_la_api(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "")
    assert gemini.is_configured() is False
    assert asyncio.run(gemini.extract_streets("Accidente en Ruta 68")) is None


# --- 4. La llamada no bloquea el event loop ----------------------------------


def test_la_llamada_cede_el_control_al_event_loop(monkeypatch):
    """La garantía que pidió el requisito, medida en vez de afirmada.

    Se lanza la extracción con un retardo simulado y, en paralelo, una tarea que
    incrementa un contador cada 10 ms. Si la llamada bloqueara el loop —como haría
    un SDK síncrono invocado directamente— el contador se quedaría en cero.

    Es exactamente el escenario de producción: los collectors comparten
    intérprete y event loop con el motor de correlación (`app/workers.py`), así
    que una llamada bloqueante de dos segundos congelaría también la correlación.
    """
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa('{"street_1": "Ruta 68", "street_2": null, "city": null}'),
        retardo=0.25,
    )

    async def escenario() -> tuple[dict | None, int]:
        latidos = 0

        async def corazon() -> None:
            nonlocal latidos
            while True:
                await asyncio.sleep(0.01)
                latidos += 1

        pulso = asyncio.create_task(corazon())
        resultado = await gemini.extract_streets("Accidente en Ruta 68")
        pulso.cancel()
        return resultado, latidos

    resultado, latidos = asyncio.run(escenario())

    assert resultado is not None
    assert latidos >= 10, (
        f"el event loop quedó bloqueado durante la llamada (sólo {latidos} latidos)"
    )


def test_varias_extracciones_corren_concurrentes(monkeypatch):
    """Corolario: si no bloquea, N llamadas tardan lo de una, no lo de N."""
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa('{"street_1": "Ruta 68", "street_2": null, "city": null}'),
        retardo=0.2,
    )

    async def escenario() -> float:
        inicio = time.monotonic()
        await asyncio.gather(*(gemini.extract_streets(f"Accidente {i}") for i in range(5)))
        return time.monotonic() - inicio

    transcurrido = asyncio.run(escenario())
    assert transcurrido < 0.6, f"parece haberse serializado: {transcurrido:.2f}s"


# --- 5. El prompt fuerza el formato ------------------------------------------


def test_la_configuracion_exige_json_en_el_decodificador(monkeypatch):
    """No basta con pedirlo en el prompt: se restringe la generación.

    `response_mime_type` + `response_schema` hacen que la API imponga el formato,
    en vez de confiar en que el modelo obedezca la instrucción.
    """
    registro = montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa('{"street_1": "Ruta 68", "street_2": null, "city": null}'),
    )
    asyncio.run(gemini.extract_streets("Accidente en Ruta 68"))

    config = registro["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == gemini.RESPONSE_SCHEMA
    assert config.temperature == 0.0, "la extracción debe ser determinista"
    assert config.system_instruction == gemini.SYSTEM_INSTRUCTION


def test_el_prompt_prohibe_lo_que_hay_que_prohibir():
    instruccion = gemini.SYSTEM_INSTRUCTION

    assert "SOLO con un objeto JSON" in instruccion
    assert "Sin markdown" in instruccion
    assert "```json" in instruccion, "debe nombrar el bloque de código que no quiere"
    assert "No inventes" in instruccion
    assert "coordenadas" in instruccion
    assert '"street_1"' in instruccion and '"street_2"' in instruccion


def test_el_esquema_declara_las_tres_claves_y_admite_nulos():
    esquema = gemini.RESPONSE_SCHEMA
    assert set(esquema["properties"]) == {"street_1", "street_2", "city"}
    assert esquema["properties"]["street_2"]["nullable"] is True
    assert set(esquema["required"]) == {"street_1", "street_2", "city"}


def test_el_texto_de_entrada_se_recorta(monkeypatch):
    """Un bloque mal recortado del portal sería pagar tokens por ruido."""
    registro = montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa('{"street_1": "Ruta 68", "street_2": null, "city": null}'),
    )
    asyncio.run(gemini.extract_streets("Accidente. " * 2000))

    assert len(registro["contents"]) <= gemini.MAX_INPUT_CHARS


# --- 6. Integración con el worker --------------------------------------------


def test_el_worker_usa_gemini_cuando_hay_clave(monkeypatch):
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa(
            '{"street_1": "Av. Argentina", "street_2": "Pedro Montt", "city": "Valparaíso"}'
        ),
    )
    resultado = asyncio.run(
        extract_streets_via_llm("Colisión en Avenida Argentina esquina Pedro Montt.")
    )
    assert resultado["street_1"] == "Av. Argentina"


def test_el_worker_cae_a_la_heuristica_si_gemini_falla(monkeypatch):
    """Media capa funcionando es mejor que ninguna.

    La alternativa era que una caída de Google apagara la extracción entera.
    """
    montar_gemini(monkeypatch, error=RuntimeError("503"))

    resultado = asyncio.run(
        extract_streets_via_llm("Colisión en Avenida Argentina esquina Pedro Montt, Valparaíso.")
    )

    assert resultado is not None, "la heurística debía tomar el relevo"
    assert resultado["street_1"] == "Avenida Argentina"
    assert resultado["street_2"] == "Pedro Montt"


def test_el_worker_cae_a_la_heuristica_sin_clave(monkeypatch):
    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "")
    resultado = asyncio.run(
        extract_streets_via_llm("Accidente en Av. España con Uno Norte, Viña del Mar.")
    )
    assert resultado["street_1"] == "Av. España"


def test_ambos_caminos_producen_el_mismo_contrato(monkeypatch):
    """El consumidor no debe saber cuál corrió."""
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaFalsa(
            '{"street_1": "Av. España", "street_2": "Uno Norte", "city": "Viña del Mar"}'
        ),
    )
    aviso = "Accidente en Av. España con Uno Norte, Viña del Mar."
    con_modelo = asyncio.run(extract_streets_via_llm(aviso))

    monkeypatch.setattr(gemini.settings, "GEMINI_API_KEY", "")
    con_reglas = asyncio.run(extract_streets_via_llm(aviso))

    assert set(con_modelo) == set(con_reglas) == {"street_1", "street_2", "city"}


# =============================================================================
#  Partes que no son texto: el warning que ensuciaba cada corrida
# =============================================================================
#
# Los modelos con razonamiento devuelven la respuesta repartida en varias
# `Part`: una con el JSON y otras con el rastro del pensamiento
# (`thought_signature`, y a veces un resumen marcado con `thought=True`).
# `response.text` está definido para el caso de una sola parte de texto, así que
# ante esa mezcla emitía en CADA llamada:
#
#     Warning: there are non-text parts in the response: ['thought_signature']
#
# El aviso era correcto y el dato también —nunca se perdió un despacho por
# esto—, pero un warning que se repite en cada corrida es lo que entrena a un
# equipo a ignorar el log. Estos tests fijan que `response_text` extrae el JSON
# sin tocar el accesor del SDK, y que el resumen del razonamiento NO se cuela:
# concatenado al JSON lo volvería imparseable, que sería cambiar ruido por una
# extracción perdida.


class ParteFalsa:
    """Una `Part` del SDK. `text` es None en las partes que no son texto."""

    def __init__(self, text=None, *, thought: bool = False, signature=None) -> None:
        self.text = text
        self.thought = thought
        self.thought_signature = signature


class RespuestaConPartes:
    """Respuesta con `candidates`, como la que devuelve un modelo que razona.

    `text` lanza a propósito: es la garantía de que `response_text` **no** usa
    el accesor del SDK cuando hay partes utilizables. Si alguien lo reintrodujera
    aguas abajo, estos tests fallan en vez de volver el warning en silencio.
    """

    def __init__(self, partes) -> None:
        contenido = type("Content", (), {"parts": partes})()
        self.candidates = [type("Candidate", (), {"content": contenido})()]
        self.prompt_feedback = ""

    @property
    def text(self):
        raise AssertionError("response_text no debe caer en el accesor del SDK")


JSON_VIA = '{"street_1": "Av. España", "street_2": null, "city": "Valparaíso"}'


def test_ignora_la_firma_de_pensamiento_y_devuelve_el_json():
    respuesta = RespuestaConPartes(
        [
            ParteFalsa(signature=b"\x0a\x1b firma binaria"),
            ParteFalsa(JSON_VIA),
        ]
    )
    assert gemini.response_text(respuesta) == JSON_VIA


def test_descarta_el_resumen_del_razonamiento():
    """`thought=True` es texto de verdad, y por eso hay que excluirlo aparte.

    Si se colara, `json.loads` recibiría prosa antes del objeto y la extracción
    se perdería entera: el ruido del log se habría cambiado por un despacho sin
    ubicación.
    """
    respuesta = RespuestaConPartes(
        [
            ParteFalsa("El usuario pide las calles del aviso. Voy a…", thought=True),
            ParteFalsa(JSON_VIA),
        ]
    )
    assert gemini.response_text(respuesta) == JSON_VIA
    assert gemini.parse_response(gemini.response_text(respuesta))["street_1"] == "Av. España"


def test_concatena_varias_partes_de_texto():
    """El JSON puede llegar partido en dos. Unirlas es el trabajo del accesor."""
    respuesta = RespuestaConPartes(
        [ParteFalsa('{"street_1": "Av. Argentina", '), ParteFalsa('"street_2": null, "city": null}')]
    )
    assert gemini.parse_response(gemini.response_text(respuesta))["street_1"] == "Av. Argentina"


def test_sin_candidatos_cae_al_accesor_del_sdk():
    """Bloqueo de seguridad o una forma que este código no previó."""
    assert gemini.response_text(RespuestaFalsa("hola")) == "hola"
    assert gemini.response_text(RespuestaFalsa(None)) == ""


def test_una_respuesta_solo_con_partes_no_textuales_se_trata_como_vacia():
    class SinTexto(RespuestaConPartes):
        @property
        def text(self):
            return None

    vacia = SinTexto([ParteFalsa(signature=b"firma")])
    assert gemini.response_text(vacia) == ""


def test_el_extractor_completo_atraviesa_las_partes(monkeypatch):
    """De punta a punta: el modelo razona y la extracción llega igual."""
    montar_gemini(
        monkeypatch,
        respuesta=RespuestaConPartes(
            [ParteFalsa(signature=b"firma"), ParteFalsa(JSON_VIA)]
        ),
    )
    resultado = asyncio.run(gemini.extract_streets("Choque en Av. España, Valparaíso"))
    assert resultado == {"street_1": "Av. España", "street_2": None, "city": "Valparaíso"}
