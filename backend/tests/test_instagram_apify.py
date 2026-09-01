"""Collector de Instagram vía Apify: partes puras y delta fetching.

Como en el resto del proyecto, `normalize()` se testea sobre instancias creadas
con `__new__`: sin sesión, sin configuración y sin red. Lo que sí se monta acá
es un doble de repositorio para `unseen()`, porque el delta fetching **es** la
lógica que justifica el módulo y dejarla sin probar sería probar todo menos lo
que importa.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.collectors.geoservices import normalise_text
from app.collectors.social import apify_client
from app.collectors.social.apify_client import (
    ApifyRun,
    describe_items,
    parse_run,
    run_looks_stale,
)
from app.collectors.social.instagram_apify_worker import (
    AGENCY_TERMS,
    CRITICAL_TERMS,
    FIRE_TERMS,
    OPERATIONAL_TERMS,
    TRAFFIC_TERMS,
    InstagramApifyCollector,
    InstagramPost,
    ResolvedPost,
    classify_event_type,
    clean_caption,
    external_id_for,
    is_emergency,
    is_fresh,
    looks_like_digest,
    parse_post,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType, family_of_event

AHORA = datetime(2026, 8, 25, 18, 0, tzinfo=UTC)


@pytest.fixture
def token_de_prueba():
    """Repone `settings.APIFY_TOKEN` al salir.

    `settings` es un singleton de módulo: un test que lo deja modificado le
    cambia la configuración a toda la suite, y el fallo aparece después, en otro
    archivo, con aspecto de bug del código.
    """
    original = settings.APIFY_TOKEN
    settings.APIFY_TOKEN = "token-de-prueba"
    yield "token-de-prueba"
    settings.APIFY_TOKEN = original


def _post(**kwargs) -> InstagramPost:
    base = {
        "short_code": "CxYz123",
        "username": "alertanoticiasvalparaiso",
        "caption": "Colisión en Av. España con Uno Norte, Viña del Mar",
        "image_url": "https://scontent.cdninstagram.com/v/t51/abc.jpg",
        "published_at": AHORA - timedelta(minutes=10),
        "permalink": "https://www.instagram.com/p/CxYz123/",
        "raw": {},
    }
    base.update(kwargs)
    return InstagramPost(**base)  # type: ignore[arg-type]


# --- Limpieza del caption ----------------------------------------------------


def test_clean_caption_quita_emojis_footer_y_hashtags_finales() -> None:
    crudo = (
        "🚨🚨 URGENTE 🚨🚨\n\n"
        "Colisión múltiple en Av. España con Uno Norte, #ViñaDelMar.\n\n"
        "———————\n"
        "Síguenos en Facebook https://fb.com/alerta\n"
        "#valparaiso #alerta #noticias #viral"
    )
    limpio = clean_caption(crudo)

    assert "🚨" not in limpio
    assert "Síguenos" not in limpio
    assert "fb.com" not in limpio
    assert "#" not in limpio
    # El hashtag de EN MEDIO sobrevive sin almohadilla: es la comuna.
    assert "ViñaDelMar" in limpio
    assert "Av. España con Uno Norte" in limpio


def test_clean_caption_quita_menciones_pero_conserva_el_hecho() -> None:
    limpio = clean_caption("Vía @otracuenta: volcamiento en Ruta 68 km 42")
    assert "@otracuenta" not in limpio
    assert "volcamiento en Ruta 68 km 42" in limpio


def test_clean_caption_vacio_es_cadena_vacia() -> None:
    assert clean_caption("") == ""
    assert clean_caption("🚨🚨🚨") == ""
    assert clean_caption(None) == ""  # type: ignore[arg-type]


def test_looks_like_digest_marca_los_recopilatorios() -> None:
    assert looks_like_digest("Resumen del día: tres emergencias en la región")
    assert not looks_like_digest("Choque en Av. Argentina")


# --- Clasificación determinista ----------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Colisión múltiple en Ruta 68", EventType.ACCIDENT),
        ("Atropello en Av. Pedro Montt", EventType.ACCIDENT),
        ("Incendio forestal consume pastizales en Placilla", EventType.WILDFIRE),
        ("Incendio estructural en calle Serrano", EventType.STRUCTURAL_FIRE),
        ("Rescate de persona atrapada en el cerro", EventType.RESCUE),
        # Fuego sin calificar: OTHER a propósito. Ver el docstring de
        # `classify_event_type`.
        ("Se registra un incendio en el sector", EventType.OTHER),
        ("Hoy celebramos los 500 años de la ciudad", None),
        ("", None),
    ],
)
def test_classify_event_type(texto: str, esperado: EventType | None) -> None:
    assert classify_event_type(texto) is esperado


# --- Pre-filtro de relevancia ------------------------------------------------


@pytest.mark.parametrize(
    "caption",
    [
        # Tránsito
        "Colisión múltiple en Ruta 68 a la altura de Placilla",
        "Volcamiento de camión en la Ruta Las Palmas",
        "Atropello de un peatón en Av. Pedro Montt",
        "Desbarrancamiento de un vehículo en la Subida Santos Ossa",
        "Accidente de tránsito con lesionados en Quilpué",
        "Tránsito suspendido por accidente de alta energía",
        # Incendios
        "Incendio forestal en el sector de Placilla",
        "Emanación de gas obliga a evacuar un edificio",
        "Primera alarma de incendio estructural en calle Serrano",
        "Quema de pastizales en Villa Alemana",
        # Claves radiales
        "10-0 en calle Serrano, se despachan carros",
        "Bomberos despacha 10-4 en Ruta 68",
        "Alerta de 10-2 en el sector alto",
        "Se solicita 10-3 en el acantilado",
        # Entidad + contexto operativo
        "Bomberos concurre a una emergencia en el cerro Barón",
        "SAMU trasladó a dos lesionados hasta el Hospital Van Buren",
    ],
)
def test_is_emergency_reconoce_la_jerga(caption: str) -> None:
    assert is_emergency(caption) is True


@pytest.mark.parametrize(
    "caption",
    [
        "El alcalde inauguró la nueva plaza del cerro Alegre",
        "Así se vio el atardecer desde Playa Ancha 🌅",
        "Wanderers gana y sueña con el ascenso",
        "Concurso: te regalamos dos entradas para el festival",
        "Cortes de agua programados para el martes en Viña",
    ],
)
def test_is_emergency_descarta_lo_que_no_es_emergencia(caption: str) -> None:
    assert is_emergency(caption) is False


@pytest.mark.parametrize(
    "caption",
    [
        # La entidad sola NO basta: es el ruido más frecuente de estas cuentas.
        "Bomberos de Valparaíso celebró su aniversario junto al alcalde",
        "Carabineros lanza campaña de seguridad escolar",
        "SENAPRED capacita a dirigentes vecinales de la comuna",
    ],
)
def test_la_entidad_sola_no_dispara(caption: str) -> None:
    """Una entidad dice QUIÉN podría estar involucrado, nunca QUÉ pasó."""
    assert is_emergency(caption) is False


@pytest.mark.parametrize(
    "caption",
    [
        # El post más replicado del año en estas cuentas.
        "Espectacular show de fuegos artificiales en Año Nuevo en el Mar",
        "Simulacro de incendio en el colegio municipal",
        "Se cumplen 10 años del incendio de 2014",
        "Campaña de prevención de incendios forestales de CONAF",
    ],
)
def test_el_ruido_con_forma_de_emergencia_se_excinde(caption: str) -> None:
    assert is_emergency(caption) is False


def test_el_ruido_se_excinde_de_la_frase_mas_larga_a_la_mas_corta() -> None:
    """Si "prevencion de incendios" se borrara antes que su forma larga,
    quedaría suelto un "forestales" —término crítico por sí mismo— y la campaña
    de CONAF pasaría igual."""
    assert is_emergency("Campaña de prevención de incendios forestales") is False
    # Y sigue reconociendo el incendio forestal de verdad.
    assert is_emergency("Incendio forestal activo en Placilla") is True


def test_la_excision_es_quirurgica_y_no_veta_el_post_entero() -> None:
    """Borrar la frase de ruido no puede llevarse por delante la emergencia real
    que venga en el mismo caption."""
    assert is_emergency(
        "Tras el show de fuegos artificiales se registró un choque en Av. España"
    )


def test_las_fechas_no_se_confunden_con_claves_radiales() -> None:
    """`10-12-2026` es una fecha, no un despacho de apoyo. Lo resuelve
    `normalise_code`, reutilizado del worker de Bomberos."""
    assert is_emergency("Nos vemos el 10-12-2026 en la plaza") is False
    assert is_emergency("Actividad el 10-4-2026 en el muelle") is False
    # 10-40 es otra clave por completo: no puede responder a 10-4.
    assert is_emergency("Radio 10-40 transmite desde el puerto") is False


def test_el_apoyo_10_12_necesita_compania() -> None:
    """Un apoyo no describe una emergencia nueva: es un despacho adicional a una
    que ya está en curso. Y `10-12` colisiona con una fecha corta."""
    assert is_emergency("Programación del 10-12 en el teatro") is False
    assert is_emergency("Bomberos solicita 10-12 de urgencia al lugar") is True


def test_los_terminos_estan_normalizados() -> None:
    """Un término con tilde no coincidiría NUNCA y el fallo sería mudo.

    El diccionario se compara contra texto pasado por `normalise_text`
    (unicodedata NFD → sin marcas combinantes → minúsculas), así que cada
    término tiene que ser ya su propia forma normalizada.
    """
    todos = (
        CRITICAL_TERMS | AGENCY_TERMS | OPERATIONAL_TERMS | TRAFFIC_TERMS | FIRE_TERMS
    )
    for termino in todos:
        assert normalise_text(termino) == termino, f"término sin normalizar: {termino!r}"


def test_el_prefiltro_es_sincronico() -> None:
    """El requisito es cero latencia de red: `_is_emergency` no puede ser una
    corrutina ni devolver una."""
    assert not asyncio.iscoroutinefunction(InstagramApifyCollector._is_emergency)
    assert not asyncio.iscoroutine(InstagramApifyCollector._is_emergency("choque"))


@pytest.mark.parametrize(
    "caption",
    [
        "Choque en Av. España",
        "Bomberos concurre a una emergencia",
        "10-12 solicitado por Bomberos",
        "El alcalde inauguró la plaza",
        "",
        "Incendio forestal en Placilla",
    ],
)
def test_el_prefiltro_y_el_clasificador_no_se_contradicen(caption: str) -> None:
    """Invariante: `classify_event_type` devuelve None si y sólo si el
    pre-filtro dijo que no.

    Si se rompiera, habría posts que pagan su llamada al modelo y desaparecen
    después en el `if event_type is not None` de `fetch()`: se gasta y no se
    guarda, que es la peor de las dos combinaciones.
    """
    assert (classify_event_type(caption) is None) == (not is_emergency(caption))


@pytest.mark.parametrize(
    ("caption", "esperado"),
    [
        ("10-0 en calle Serrano", EventType.STRUCTURAL_FIRE),
        ("Despacho 10-2 al sector alto", EventType.WILDFIRE),
        ("Solicitan 10-3 en el acantilado", EventType.RESCUE),
        ("10-4 en Ruta 68", EventType.ACCIDENT),
        # Subtipo: 10-4-1 es rescate vehicular con víctima atrapada, el mismo
        # despacho. La comparación por prefijo lo deja pasar.
        ("Confirman 10-4-1 en la Ruta 68", EventType.ACCIDENT),
        # El separador de familia colapsa: 10-0-4 ES un 10-4.
        ("Clave 10-0-4 en Av. Argentina", EventType.ACCIDENT),
    ],
)
def test_la_clave_radial_manda_sobre_el_vocabulario(
    caption: str, esperado: EventType
) -> None:
    """Es la central diciendo qué despachó: más específico que cualquier
    sinónimo que traiga el caption."""
    assert classify_event_type(caption) is esperado


def test_incendio_generico_no_se_funde_con_los_incendios_de_conaf() -> None:
    """La decisión de mandar el fuego sin calificar a `OTHER` tiene una
    consecuencia concreta y es la que la justifica: cae en otra familia, así que
    el motor no puede usarlo para corroborar un incendio de CONAF.
    """
    tipo = classify_event_type("Fuerte incendio se registra en el sector")
    assert tipo is EventType.OTHER
    assert family_of_event(tipo) != family_of_event(EventType.WILDFIRE)


def test_forestal_gana_a_generico() -> None:
    """El orden de los clasificadores importa: sin él, todo fuego sería el
    genérico o el estructural, según quién se evaluara primero."""
    assert classify_event_type("incendio forestal") is EventType.WILDFIRE


# --- Parseo del item ---------------------------------------------------------


def test_parse_post_lee_el_esquema_del_actor() -> None:
    post = parse_post(
        {
            "shortCode": "CxYz123",
            "caption": "Choque en Av. España 🚗",
            "timestamp": "2026-08-25T17:50:00.000Z",
            "displayUrl": "https://scontent.cdninstagram.com/v/t51/abc.jpg",
            "ownerUsername": "alertanoticiasvalparaiso",
            "url": "https://www.instagram.com/p/CxYz123/",
            "likesCount": 120,
        }
    )
    assert post is not None
    assert post.short_code == "CxYz123"
    assert post.caption == "Choque en Av. España"
    assert post.image_url.endswith("abc.jpg")
    assert post.published_at == datetime(2026, 8, 25, 17, 50, tzinfo=UTC)
    assert post.raw["likesCount"] == 120


def test_parse_post_acepta_alias_de_otro_actor() -> None:
    """Cambiar de Actor del marketplace no debería tocar el parser."""
    post = parse_post(
        {
            "code": "AbC999",
            "text": "Volcamiento en Ruta 68",
            "takenAt": 1_756_144_200,
            "imageUrl": "https://cdn/x.jpg",
            "username": "otracuenta",
        }
    )
    assert post is not None
    assert post.short_code == "AbC999"
    assert post.permalink == "https://www.instagram.com/p/AbC999/"


def test_parse_post_descarta_lo_inservible() -> None:
    assert parse_post({"caption": "sin id"}) is None
    assert parse_post({"shortCode": "X", "caption": "   "}) is None
    assert parse_post({"shortCode": "X", "caption": "🔥🔥🔥"}) is None
    assert parse_post("no soy un objeto") is None
    assert parse_post(None) is None


def test_external_id_es_el_shortcode_y_no_un_hash_del_texto() -> None:
    """Editar el caption no puede generar un id nuevo: duplicaría el accidente."""
    uno = _post(caption="Choque en Av. España")
    dos = _post(caption="ACTUALIZACIÓN: choque en Av. España, dos lesionados")
    assert external_id_for(uno) == external_id_for(dos) == "ig:CxYz123"


# --- Frescura ----------------------------------------------------------------


def test_is_fresh() -> None:
    reciente = _post(published_at=AHORA - timedelta(minutes=30))
    viejo = _post(published_at=AHORA - timedelta(hours=9))
    sin_fecha = _post(published_at=None)

    assert is_fresh(reciente, now=AHORA, max_age_minutes=180)
    assert not is_fresh(viejo, now=AHORA, max_age_minutes=180)
    # Sin fecha se procesa: el filtro por external_id lo atrapa igual y perder
    # un accidente por un campo que el Actor no llenó es peor.
    assert is_fresh(sin_fecha, now=AHORA, max_age_minutes=180)


# --- Estado de la corrida de Apify -------------------------------------------


def test_parse_run_lee_los_metadatos() -> None:
    run = parse_run(
        {
            "data": {
                "id": "run123",
                "status": "SUCCEEDED",
                "finishedAt": "2026-08-25T17:55:00.000Z",
                "defaultDatasetId": "ds456",
            }
        }
    )
    assert run.run_id == "run123"
    assert run.dataset_id == "ds456"
    assert run.as_dict()["run_id"] == "run123"
    # Los metadatos crudos de Apify no viajan a raw_events.
    assert "raw" not in run.as_dict()


def test_parse_run_sin_corridas_es_un_fallo_de_configuracion() -> None:
    with pytest.raises(CollectorError):
        parse_run({"data": None})


def test_run_looks_stale_detecta_el_schedule_muerto() -> None:
    """El fallo silencioso central de esta arquitectura.

    Si el Schedule de Apify se detiene, `runs/last?status=SUCCEEDED` sigue
    devolviendo el dataset de la última corrida buena. Sin esta comprobación el
    collector reportaría `success` con 0 eventos indefinidamente, que es
    indistinguible de un día tranquilo.
    """
    fresca = ApifyRun("r", "SUCCEEDED", datetime.now(UTC) - timedelta(minutes=8), "d", {})
    rancia = ApifyRun("r", "SUCCEEDED", datetime.now(UTC) - timedelta(hours=30), "d", {})
    sin_fecha = ApifyRun("r", "SUCCEEDED", None, "d", {})

    assert run_looks_stale(fresca, 45) == (False, None)

    stale, motivo = run_looks_stale(rancia, 45)
    assert stale and motivo and "Schedule" in motivo

    assert run_looks_stale(sin_fecha, 45)[0] is True


@respx.mock
def test_el_cliente_pega_donde_debe_y_con_la_cabecera_correcta(token_de_prueba) -> None:
    """Verifica las tres cosas que sólo se ven en la petición real.

    1. El id del Actor lleva **tilde**. Con barra, Apify responde un 404 que
       parece un actor inexistente en vez de un id mal escrito.
    2. `status=SUCCEEDED` viaja en las dos llamadas. Sin él, "última corrida"
       incluye la que está corriendo ahora y el dataset llega a medio llenar.
    3. El token va en `Authorization: Bearer`, **nunca** en la query. Una URL con
       el token dentro termina en los logs de acceso y en `collector_runs.error`.
    """
    ruta = "https://api.apify.com/v2/acts/apify~instagram-scraper"
    run = respx.get(f"{ruta}/runs/last").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "run1",
                    "status": "SUCCEEDED",
                    "finishedAt": "2026-08-25T17:55:00.000Z",
                    "defaultDatasetId": "ds1",
                }
            },
        )
    )
    # El endpoint de items devuelve un ARRAY desnudo: es el único de la API que
    # no envuelve la respuesta en {"data": ...}.
    items = respx.get(f"{ruta}/runs/last/dataset/items").mock(
        return_value=httpx.Response(200, json=[{"shortCode": "A", "caption": "hola"}])
    )

    async def _correr():
        async with apify_client.build_client() as client:
            corrida = await apify_client.fetch_last_run(client, "apify/instagram-scraper")
            datos = await apify_client.fetch_items(client, "apify~instagram-scraper", limit=5)
            return (corrida, datos)

    corrida, datos = asyncio.run(_correr())

    assert corrida.run_id == "run1"
    assert datos == [{"shortCode": "A", "caption": "hola"}]

    for llamada in (run.calls.last.request, items.calls.last.request):
        assert llamada.headers["authorization"] == f"Bearer {token_de_prueba}"
        assert "SUCCEEDED" in str(llamada.url)
        assert "token" not in llamada.url.params


def test_el_cliente_sin_token_no_sale_a_la_red() -> None:
    original = settings.APIFY_TOKEN
    settings.APIFY_TOKEN = ""
    try:
        with pytest.raises(CollectorError, match="APIFY_TOKEN"):
            apify_client.build_client()
    finally:
        settings.APIFY_TOKEN = original


def test_describe_items_separa_los_errores_disfrazados() -> None:
    """Un perfil privado o dado de baja NO hace fallar al Actor: empuja un item
    con forma de error y la corrida termina en SUCCEEDED."""
    buenos, problemas = describe_items(
        [
            {"shortCode": "A", "caption": "algo"},
            {"error": "no_items", "username": "cuentaprivada"},
            "esto no es un objeto",
        ]
    )
    assert len(buenos) == 1
    assert len(problemas) == 2
    assert "cuentaprivada" in problemas[0]


# --- Delta fetching ----------------------------------------------------------


class _RepoFalso:
    """Doble de `EventRepository`: registra la llamada y responde lo conocido."""

    def __init__(self, conocidos: set[str]) -> None:
        self.conocidos = conocidos
        self.llamadas = 0

    async def ids_by_external_id(self, source, external_ids):
        self.llamadas += 1
        return {eid: 1 for eid in external_ids if eid in self.conocidos}


class _ServicioFalso:
    def __init__(self, repo: _RepoFalso) -> None:
        self.repo = repo


def _collector_con(conocidos: set[str]) -> tuple[InstagramApifyCollector, _RepoFalso]:
    collector = InstagramApifyCollector.__new__(InstagramApifyCollector)
    repo = _RepoFalso(conocidos)
    collector.service = _ServicioFalso(repo)  # type: ignore[attr-defined]
    return (collector, repo)


def test_unseen_descarta_lo_ya_procesado_con_una_sola_consulta() -> None:
    collector, repo = _collector_con({"ig:viejo1", "ig:viejo2"})
    posts = [
        _post(short_code="viejo1"),
        _post(short_code="nuevo1"),
        _post(short_code="viejo2"),
        _post(short_code="nuevo2"),
    ]

    nuevos = asyncio.run(collector.unseen(posts))

    assert [p.short_code for p in nuevos] == ["nuevo1", "nuevo2"]
    # Una consulta por corrida, no una por post: si esto sube, el delta fetching
    # dejó de ser una optimización y pasó a ser un N+1.
    assert repo.llamadas == 1


def test_unseen_sin_posts_no_toca_la_base() -> None:
    collector, repo = _collector_con(set())
    assert asyncio.run(collector.unseen([])) == []
    assert repo.llamadas == 0


# --- Normalización -----------------------------------------------------------


def _normalize(records):
    collector = InstagramApifyCollector.__new__(InstagramApifyCollector)
    collector.source = EventSource.SOCIAL_MEDIA
    collector.name = "instagram_apify"
    collector.confidence = 0.35
    return collector.normalize(records)


def test_normalize_sin_punto_conserva_la_senal() -> None:
    """Un post sin coordenadas entra igual: se pierde el Paso A del motor, no el
    hecho. Descartarlo sería perder un accidente por no saber dónde."""
    eventos = _normalize(
        [
            ResolvedPost(
                post=_post(),
                event_type=EventType.ACCIDENT,
                streets={},
                point=None,
                apify={"run_id": "run123"},
            )
        ]
    )

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento.lat is None and evento.lon is None
    assert evento.source is EventSource.SOCIAL_MEDIA
    assert evento.type is EventType.ACCIDENT
    assert evento.external_id == "ig:CxYz123"
    assert evento.confidence == pytest.approx(0.35)
    assert evento.raw_data["_apify"]["run_id"] == "run123"
    # La URL de la imagen se guarda marcada como efímera: es una URL firmada del
    # CDN de Instagram y caduca.
    assert evento.raw_data["image_url_efimera"] is True


def test_normalize_recorta_el_timestamp_futuro() -> None:
    """`EventCreate` rechaza los eventos futuros. Un reloj adelantado en la
    fuente no puede costarnos la señal entera."""
    futuro = datetime.now(UTC) + timedelta(hours=2)
    eventos = _normalize(
        [
            ResolvedPost(
                post=_post(published_at=futuro),
                event_type=EventType.ACCIDENT,
                streets={},
                point=None,
                apify={},
            )
        ]
    )
    assert eventos[0].timestamp <= datetime.now(UTC)


def test_normalize_separa_extraccion_de_geocodificacion() -> None:
    """Cuando un punto esté mal, hay que poder saber cuál de los dos pasos
    falló. Guardar sólo la coordenada borra esa distinción para siempre."""
    from app.collectors.nominatim import GeocodeResult

    punto = GeocodeResult(
        lat=-33.0245,
        lon=-71.5518,
        display_name="Av. España, Viña del Mar",
        importance=0.42,
        query="Av. España y Uno Norte, Viña del Mar",
    )
    eventos = _normalize(
        [
            ResolvedPost(
                post=_post(),
                event_type=EventType.ACCIDENT,
                streets={
                    "street_1": "Av. España",
                    "street_2": "Uno Norte",
                    "city": "Viña del Mar",
                },
                point=punto,
                apify={},
            )
        ]
    )

    evento = eventos[0]
    assert evento.lat == pytest.approx(-33.0245)
    assert evento.raw_data["_extraction"]["street_1"] == "Av. España"
    assert evento.raw_data["_geocoding"]["importance"] == pytest.approx(0.42)
    assert evento.raw_data["comuna"] == "Viña del Mar"
