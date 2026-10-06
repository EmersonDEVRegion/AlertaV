"""Configuración central de AlertaV.

Todo se lee de variables de entorno / .env vía pydantic-settings. No hay valores
secretos hardcodeados: `FIRMS_MAP_KEY` y `POSTGRES_PASSWORD` deben venir del
entorno.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, PostgresDsn, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Lista que se declara en el .env como CSV en vez de JSON.
#: `NoDecode` desactiva el parseo JSON automático de pydantic-settings para que
#: el validador `mode="before"` reciba el string crudo. Sin esto,
#: `CORS_ORIGINS=http://a,http://b` revienta con un JSONDecodeError.
CsvList = Annotated[list[str], NoDecode]

#: Esquemas que pueden aparecer en un `DATABASE_URL` copiado de un panel de
#: proveedor. Se normalizan al driver que corresponde en cada caso: la app habla
#: asyncpg, Alembic habla psycopg2.
_DSN_SCHEMES = {"postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2"}


#: Marcas de un valor de plantilla que nunca debió llegar a producción. Las
#: revisa `Settings._produccion_sin_huecos` y los tests de User-Agent.
PLACEHOLDERS: tuple[str, ...] = (
    "example.cl",
    "example.com",
    "TU_CORREO",
    "github.com/alertav",
    "<",
)


#: Un correo dentro de un texto libre («AlertaV/1.0 (x@y.cl)», «mailto:x@y.cl»).
_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Anulaciones de User-Agent: cualquiera con tildes rompe httpx al construirse.
_AGENTES: tuple[str, ...] = (
    "NOMINATIM_USER_AGENT",
    "TRANSPORTE_INFORMA_USER_AGENT",
    "GBV_USER_AGENT",
    "ESVAL_USER_AGENT",
    "LOCAL_NEWS_USER_AGENT",
)

#: Largo mínimo de la clave de un proxy de salida. Va en texto claro por
#: internet (Basic auth en el `CONNECT`), así que tiene que ser larga y
#: aleatoria: lo que la protege es no poder adivinarla.
_MIN_CLAVE_PROXY = 24


def _problemas_del_proxy(url: str, nombre: str) -> list[str]:
    """Qué le falta a la URL de un proxy de salida. Vacía = no hay proxy.

    Nunca repite la URL en el mensaje: trae la clave, y el error del
    validador termina en los logs de Render.
    """
    valor = url.strip()
    if not valor:
        return []
    try:
        partes = urlsplit(valor)
        puerto = partes.port
    except ValueError:
        return [f"{nombre}: no es una URL válida"]
    problemas: list[str] = []
    if partes.scheme not in {"http", "https"}:
        problemas.append(f"{nombre}: el esquema tiene que ser http o https")
    if not partes.hostname or puerto is None:
        problemas.append(f"{nombre}: falta el host o el puerto")
    clave = partes.password or ""
    if not partes.username or len(clave) < _MIN_CLAVE_PROXY or not valor.isascii():
        problemas.append(
            f"{nombre}: necesita usuario y una clave ASCII de "
            f"{_MIN_CLAVE_PROXY} caracteres o más"
        )
    return problemas


class BoundingBox(BaseSettings):
    """Caja envolvente en WGS84 (grados decimales)."""

    west: float
    south: float
    east: float
    north: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def as_firms_param(self) -> str:
        """Formato que espera la API de área de NASA FIRMS: west,south,east,north."""
        return f"{self.west},{self.south},{self.east},{self.north}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Aplicación ----------------------------------------------------------
    PROJECT_NAME: str = "AlertaV — Fire Data Collector"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: CsvList = Field(default_factory=lambda: ["http://localhost:5173"])
    #: Vercel publica cada rama en un subdominio distinto
    #: (`alertav-git-<rama>-<org>.vercel.app`). Enumerarlos uno por uno en
    #: `CORS_ORIGINS` es imposible, así que los previews se cubren con una
    #: expresión regular. Vacío = desactivado. Debe anclarse con `^...$`.
    CORS_ORIGIN_REGEX: str = ""
    #: La API no usa cookies ni sesiones: no hay nada que enviar con
    #: credenciales. Dejarlo en False evita además la trampa silenciosa de
    #: `allow_origins=["*"]`, que el navegador ignora si hay credenciales.
    CORS_ALLOW_CREDENTIALS: bool = False
    #: Token de las rutas de operación (`/collectors/*` salvo `/health`,
    #: `/incidents/correlate`): `Authorization: Bearer <token>`. Ver
    #: `app.api.deps.require_operator`. Vacío en producción = esas rutas
    #: responden 503 (fallan cerradas). Si se define, mínimo 32 caracteres ASCII:
    #: `python -c "import secrets; print(secrets.token_hex(32))"`.
    OPERATOR_TOKEN: str = ""
    #: Identidad ante los servicios que se consultan. Ver `app/core/identidad.py`.
    #: El correo es opcional: vacío, se toma el `mailto:` de `VAPID_SUBJECT` o el
    #: correo de `NOMINATIM_USER_AGENT` (ver `contacto_email`).
    CONTACT_EMAIL: str = ""
    CONTACT_URL: str = "https://github.com/EmersonDEVRegion/AlertaV"

    # -- Base de datos -------------------------------------------------------
    #: DSN completo. Si viene definido, **manda sobre los `POSTGRES_*`**: es lo
    #: que entregan Supabase, Neon o Render de una sola pieza y descomponerlo a
    #: mano sólo invita a erratas. En local se deja vacío y siguen mandando las
    #: variables sueltas de abajo.
    DATABASE_URL: str = ""
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "alertav"
    POSTGRES_PASSWORD: str = "alertav"
    POSTGRES_DB: str = "alertav"
    DB_SCHEMA: str = "alertav"
    DB_ECHO: bool = False
    #: Tres procesos comparten la misma base (API + collectors + correlación) y
    #: cada uno abre su propio pool. El techo real de conexiones es
    #: `(POOL_SIZE + MAX_OVERFLOW) * 3`. En la capa gratuita de Supabase conviene
    #: bajarlo a 2/3; los valores de acá son los de desarrollo local.
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    #: Modo SSL de asyncpg: "", "prefer", "require", "verify-full"…
    #: Vacío = sin TLS (local). Cualquier base gestionada exige "require".
    DB_SSL_MODE: str = ""
    #: Los poolers en modo transacción (PgBouncer, Supavisor puerto 6543) no
    #: soportan prepared statements: asyncpg crea `asyncpg_stmt_N` en una
    #: conexión y lo reusa en otra, y la consulta revienta con
    #: `prepared statement "asyncpg_stmt_N" does not exist`. Activar esto apaga
    #: la caché de sentencias. Innecesario contra una conexión directa o un
    #: pooler en modo sesión.
    DB_DISABLE_PREPARED_STATEMENTS: bool = False

    # -- Dominio geográfico: Región de Valparaíso (continental) --------------
    REGION_WEST: float = -72.0
    REGION_SOUTH: float = -33.8
    REGION_EAST: float = -69.8
    REGION_NORTH: float = -32.0
    # Durante la ventana de recolección conviene NO descartar datos: se marcan
    # como fuera de región y se decide después. Ponerlo en True sólo cuando el
    # dataset esté calibrado.
    REJECT_OUTSIDE_REGION: bool = False

    # -- Cadencia de los collectors ------------------------------------------
    #: Dispersión aleatoria aplicada a CADA espera del bucle, como fracción del
    #: intervalo. 0 = metrónomo exacto.
    #:
    #: El runner ya dispersaba el PRIMER disparo (`_STARTUP_JITTER`) para que un
    #: reinicio no lanzara doce collectors en el mismo segundo. Eso resolvía el
    #: pico del arranque y dejaba intacto el problema de régimen: pasada esa
    #: primera espera, cada collector queda golpeando su fuente en un ciclo
    #: perfectamente regular durante semanas.
    #:
    #: Dos consecuencias, y la segunda es la que motiva esta variable:
    #:
    #: * **Sincronización.** Dos cadencias con un divisor común vuelven a
    #:   coincidir sin remedio: 600 s (Transporte Informa) y 900 s (prensa
    #:   local) comparten período 1800, así que cada media hora exacta los dos
    #:   scrapers salían a la red juntos. El arranque escalonado no lo impide,
    #:   sólo elige en qué segundo del ciclo ocurre.
    #: * **Huella.** Una petición cada 600 s exactos desde una IP de datacenter
    #:   es la firma más fácil de reconocer que puede dejar un cliente. Ningún
    #:   navegador hace eso. Es lo que un WAF clasifica como bot antes de mirar
    #:   el volumen — que en nuestro caso es de 144 peticiones diarias, es decir,
    #:   menos que una persona leyendo el portal en el almuerzo.
    #:
    #: ±10 % sobre 600 s son ±60 s: suficiente para que el patrón deje de ser
    #: reconocible y para que dos collectors no vuelvan a alinearse, y demasiado
    #: poco para mover la latencia de una capa que se mide en minutos.
    COLLECTOR_JITTER_RATIO: float = Field(default=0.10, ge=0.0, le=0.5)
    #: Collectors corriendo A LA VEZ en el proceso de workers. Acota tres
    #: presupuestos que comparten: conexiones (el pool de producción es de 2+3),
    #: memoria (512 MB) y CPU (0,1 vCPU). Quien espera no pierde su turno: sólo
    #: se atrasa unos segundos, y la dispersión de arriba ya los separa.
    COLLECTOR_MAX_CONCURRENCY: int = Field(default=4, ge=1, le=20)

    # -- NASA FIRMS ----------------------------------------------------------
    FIRMS_MAP_KEY: str = ""
    FIRMS_BASE_URL: str = "https://firms.modaps.eosdis.nasa.gov"
    # Sensores a consultar. NRT = near real time.
    FIRMS_SOURCES: CsvList = Field(
        default_factory=lambda: [
            "VIIRS_SNPP_NRT",
            "VIIRS_NOAA20_NRT",
            "VIIRS_NOAA21_NRT",
            "MODIS_NRT",
        ]
    )
    FIRMS_DAY_RANGE: int = Field(default=1, ge=1, le=10)
    FIRMS_TIMEOUT_SECONDS: float = 60.0
    FIRMS_POLL_INTERVAL_SECONDS: int = 900  # 15 min

    # -- CONAF ---------------------------------------------------------------
    # Capa de incendios del Sistema de Información Territorial de CONAF,
    # publicada por la organización GEPRIF en ArcGIS. Campos verificados:
    # id, nombre, estado, f_inicio, f_control, f_extincion, sup_total, lat, lon,
    # comuna, provincia, region.
    #
    # Formato: `kind|url[|layer]`, varias fuentes separadas por `;`. `kind` puede
    # ser arcgis, wfs o geojson. Declarar aquí un respaldo (por ejemplo el WFS
    # del SIT cuando esté disponible) no requiere tocar código.
    CONAF_SOURCES: str = (
        "arcgis|https://services5.arcgis.com/A1ELWse9bRAi2JiV/arcgis/rest/"
        "services/incendios_base/FeatureServer|0"
    )
    #: Ventana hacia atrás. Un incendio cambia de estado durante días; releerlos
    #: mantiene actualizado el registro vía upsert sin duplicar nada.
    CONAF_LOOKBACK_DAYS: int = Field(default=7, ge=1, le=90)
    #: Estados a conservar (vacío = todos). En temporada: "En Combate,Controlado".
    CONAF_STATES: CsvList = Field(default_factory=list)
    #: Regiones a conservar. La comparación ignora tildes y mayúsculas.
    CONAF_REGIONS: CsvList = Field(default_factory=lambda: ["Valparaíso"])
    #: Si la fuente no trae el campo región, se usa el bounding box configurado.
    CONAF_FILTER_BY_REGION: bool = True
    #: Cláusula WHERE cruda. Vacío = se construye desde CONAF_LOOKBACK_DAYS.
    CONAF_WHERE: str = ""
    #: Corrección de zona horaria de la fuente, en minutos. ArcGIS especifica que
    #: los campos fecha viajan en epoch UTC y ese es el supuesto por defecto (0).
    #: Si al calibrar contra un incendio conocido aparece un desfase constante,
    #: se corrige acá sin desplegar. Ver README.
    CONAF_TIME_OFFSET_MINUTES: int = 0
    CONAF_TIMEOUT_SECONDS: float = 45.0
    CONAF_PAGE_SIZE: int = Field(default=1000, ge=1, le=5000)
    CONAF_POLL_INTERVAL_SECONDS: int = 300  # 5 min

    # -- SENAPRED ------------------------------------------------------------
    # SENAPRED no publica una API documentada de alertas en tiempo real. Se lee
    # la capa de alertas vigentes que alimenta los visores institucionales.
    # Campos verificados: Region, Alerta, Razon, Comunas, Ambito, Fecha, Evento.
    SENAPRED_SOURCES: str = (
        "arcgis|https://services6.arcgis.com/dxNWeb35zWPRjaNL/arcgis/rest/"
        "services/SENAPRED_-_Alertas_vigentes_por_region_(vista)/FeatureServer|0"
    )
    #: Regiones a conservar; comparación insensible a tildes y mayúsculas.
    SENAPRED_REGIONS: CsvList = Field(default_factory=lambda: ["Valparaíso"])
    #: Las alertas de ámbito nacional afectan también a la V región.
    SENAPRED_INCLUDE_NATIONAL: bool = True
    SENAPRED_FILTER_BY_REGION: bool = True
    SENAPRED_TIME_OFFSET_MINUTES: int = 0
    SENAPRED_TIMEOUT_SECONDS: float = 45.0
    SENAPRED_PAGE_SIZE: int = Field(default=1000, ge=1, le=5000)
    SENAPRED_POLL_INTERVAL_SECONDS: int = 600  # 10 min

    # -- USGS — sismos en tiempo real ----------------------------------------
    # Feed público del United States Geological Survey. Sin credenciales, sin
    # cuota declarada. `2.5_day` = magnitud ≥ 2.5 de las últimas 24 h; el feed
    # se regenera cada minuto, así que releerlo cada 5 no pierde eventos.
    # Alternativas si se quiere más o menos ruido: `4.5_day`, `all_hour`,
    # `significant_week`. Mismo formato de declaración que CONAF/SENAPRED, con
    # cadena de respaldos: `geojson|url`, separadas por ';'.
    USGS_SOURCES: str = (
        "geojson|https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
    )
    #: Caja de la zona central de Chile. **No** es `region_bbox`: un sismo a 200 km
    #: de Valparaíso se siente en Valparaíso, así que el recorte útil para sismos
    #: es más ancho que el de incendios. Se filtra en el cliente porque el feed
    #: resumen es global y no admite parámetros de consulta.
    USGS_WEST: float = -73.0
    USGS_SOUTH: float = -35.0
    USGS_EAST: float = -69.0
    USGS_NORTH: float = -31.0
    #: Magnitud mínima a ingerir, sobre la que ya trae el feed. 0.0 = todo lo que
    #: llegue. Subirlo filtra micro-sismicidad sin cambiar de feed.
    USGS_MIN_MAGNITUDE: float = Field(default=0.0, ge=0.0, le=10.0)
    #: Tipos de evento del catálogo a conservar. El feed incluye explosiones de
    #: cantera y eventos de hielo bajo el mismo esquema; ingerirlos como sismos
    #: sería un error de dominio, no de mapeo.
    USGS_EVENT_TYPES: CsvList = Field(default_factory=lambda: ["earthquake"])
    #: Ingerir también las soluciones automáticas (`status = automatic`), que se
    #: revisan y corrigen horas después. Conviene dejarlo en True: el upsert por
    #: `external_id` actualiza la fila cuando llega la solución revisada, y para
    #: una app de emergencias llegar tarde es peor que llegar con ±0.2 de magnitud.
    USGS_INCLUDE_AUTOMATIC: bool = True
    USGS_TIMEOUT_SECONDS: float = 30.0
    USGS_POLL_INTERVAL_SECONDS: int = 300  # 5 min

    # -- Cortes de suministro eléctrico --------------------------------------
    #: Volcado de cortes de Chilquinta. **No es un endpoint**: es un archivo
    #: estático que la empresa regenera, igual que el KMZ de CGE. Chilquinta no
    #: publica API; su visor lee este archivo y nada más.
    #:
    #: El `006` del nombre es el código de filial —el mismo que el visor lleva en
    #: `/mapas?emp=006`— y va en la URL porque es parte del nombre del archivo,
    #: no un parámetro. Si el grupo publicara otras filiales, se cubrirían
    #: cambiando ese número.
    #:
    #: El nombre de la variable conserva el sufijo `_API_URL` por consistencia
    #: con las otras fuentes y para no romper los `.env` desplegados, pero lo que
    #: apunta es un archivo, y además **JSONP**: el JSON viene envuelto en
    #: `eqfeed_callback( … )`. Ver `chilquinta_worker`.
    #:
    #: Hubo seis intentos apuntando esta variable a `…/obtieneImage`, una ruta
    #: que devuelve 401 y que el visor nunca llama. Si alguien la encuentra en un
    #: log viejo y quiere "restaurarla", que lea antes el módulo del collector.
    CHILQUINTA_API_URL: str = "https://mapainterrupciones.chilquinta.cl/dt/results_006.js"
    #: Archivo de afectaciones de CGE. **No es un endpoint JSON**: es un KMZ
    #: —un ZIP con un KML dentro— que la plataforma de CGE regenera cada pocos
    #: minutos y sirve como estático. Es el archivo sobre el que se dibuja su
    #: visor, y la razón de que la raíz `/afectaciones/` devolviera HTML: ahí no
    #: hay API que encontrar, sólo la página que consume este archivo.
    #:
    #: El nombre de la variable conserva el sufijo `_API_URL` por consistencia
    #: con las otras fuentes y para no romper los `.env` desplegados, pero lo que
    #: apunta es un archivo. `CgeCollector` lo descarga crudo y lo descomprime en
    #: memoria; ver `cge_worker.load_records` y `kmz_parser`.
    CGE_API_URL: str = "https://mapa-afectaciones.grupocge.cl/afectaciones/mapa_cge.kmz"
    POWER_TIMEOUT_SECONDS: float = 30.0
    #: Cadencia de ambos collectors. Un corte cambia de estado —clientes
    #: afectados, hora estimada de reposición— cada pocos minutos durante el
    #: evento, así que 5 minutos es el compromiso entre frescura y cortesía con
    #: un servidor que no nos pertenece.
    POWER_POLL_INTERVAL_SECONDS: int = 300  # 5 min

    # -- Cortes de agua: Esval -------------------------------------------------
    #: Cortes vigentes de la Oficina Virtual de Esval. Es la API interna de su
    #: frontend: devuelve `{"data": [...]}` con fechas, calles, motivo y el
    #: número `sisda` de cada corte, **pero ninguna coordenada**. Incluye además
    #: los de Aguas del Valle (IV Región), que se descartan por `empresaId`.
    ESVAL_CORTES_URL: str = "https://ov.esval.cl/api-ov/api/EstadoDeServicio/CortesActivos"
    #: KML de zonas de corte del visor oficial (`tupuntodeagua.esval.cl`), que es
    #: de donde salen el punto y el polígono de cada corte. Se une con la API
    #: por `sisda`. Si falla, la corrida sigue sin coordenadas.
    ESVAL_ZONAS_KML_URL: str = (
        "https://tupuntodeagua.esval.cl/script/generaKmlZonasCorte.aspx"
    )
    #: `empresaId` de Esval en la API. La cabecera `XCodempresa` **no** filtra:
    #: con 1, con 2 o sin ella la respuesta es la misma (verificado el
    #: 2026-09-23), así que el filtro se hace acá.
    ESVAL_EMPRESA_ID: int = 1
    #: Parámetro `region` del KML. La V Región es la 5.
    ESVAL_REGION_KML: int = 5
    #: Navegador + identificación, el criterio de `TRANSPORTE_INFORMA_USER_AGENT`.
    #: Sin tildes: las cabeceras HTTP van en latin-1.
    #: Vacío = navegador + identidad de `app.core.identidad` (repo y correo de
    #: contacto). Con valor, se manda tal cual: ASCII, sin tildes.
    ESVAL_USER_AGENT: str = ""
    ESVAL_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, le=120)
    #: 10 minutos. Son dos GET livianos por corrida; un corte de emergencia se
    #: publica en cualquier momento, pero no cambia minuto a minuto.
    ESVAL_POLL_INTERVAL_SECONDS: int = Field(default=600, ge=120, le=86400)
    #: Proxy en Chile por el que salen las dos peticiones a Esval:
    #: `http://usuario:clave@host:puerto`. **Es un secreto.**
    #:
    #: Esval sólo responde a IP chilenas: sus dos hosts descartan en silencio
    #: todo lo de afuera, datacenters y hogares por igual (probado el
    #: 2026-09-29 desde 40 nodos fuera de Chile y 8 dentro; `plan.md`, §S1).
    #: Render no tiene regiones en Sudamérica, así que desde ahí la API no
    #: responde nunca. El proxy (tinyproxy en Oracle Cloud, `infra/proxy-cl/`)
    #: sólo deja pasar `CONNECT` a esos dos hosts: el TLS va de punta a punta y
    #: el servidor chileno no puede alterar lo que llega.
    #:
    #: Vacío = directo, que es lo que sirve en local desde Chile. En producción,
    #: vacío hace que el collector falle al construirse con el motivo, en vez de
    #: esperar tres timeouts por corrida.
    ESVAL_PROXY_URL: str = ""

    # -- Sismos: Centro Sismológico Nacional ---------------------------------
    # El CSN es la razón de ser de este collector: su umbral de detección en
    # Chile baja hasta M2.5, mientras el feed global del USGS filtra en M2.5
    # mundial pero en la práctica ignora casi todo lo chileno bajo M4.5. Para un
    # sistema regional esa diferencia es la mayor parte del catálogo.
    CSN_BASE_URL: str = "https://www.sismologia.cl"
    #: Días de catálogo a leer por corrida. Dos y no uno: a las 00:30 de Chile
    #: el catálogo del día nuevo tiene una fila y todo lo reciente vive en el del
    #: día anterior. Con uno solo, cada medianoche se perdería de vista una hora
    #: larga de sismos.
    CSN_CATALOG_DAYS: int = Field(default=2, ge=1, le=7)
    #: Magnitud mínima a conservar. 0.0 = todo lo que publique el CSN, que es lo
    #: que se quiere: un enjambre de microsismos es información sismológica real.
    CSN_MIN_MAGNITUDE: float = Field(default=0.0, ge=0.0, le=10.0)
    #: Recorte espacial. Más ancho que `region_bbox` por la misma razón que el
    #: del USGS: un sismo a 200 km de Valparaíso se siente en Valparaíso, y
    #: aplicarle a un fenómeno de escala regional el criterio pensado para
    #: incendios puntuales dejaría fuera justo el contexto que da valor al dato.
    CSN_WEST: float = -73.0
    CSN_SOUTH: float = -35.0
    CSN_EAST: float = -69.0
    CSN_NORTH: float = -31.0
    CSN_TIMEOUT_SECONDS: float = 30.0
    CSN_POLL_INTERVAL_SECONDS: int = 300  # 5 min

    # -- Accidentes viales: despachos de Bomberos ----------------------------
    #
    # `BOMBEROS_DISPATCH_URL` fue eliminada. Apuntaba a un puente tipo RSSHub
    # sobre la cuenta de la central, y ese camino está **muerto**, no degradado:
    # la ruta de Twitter de RSSHub dejó de existir cuando X cerró su API, y el
    # espejo de xcancel que la reemplazaba tampoco responde. Un ajuste que no
    # puede tomar ningún valor que funcione no es configuración: es una trampa
    # para el próximo que despliegue, que la rellena, ve el collector arrancar y
    # tarda días en descubrir que la fuente no existe.
    #
    # Los despachos entran hoy por `POST /api/v1/apify/webhook`, que Apify llama
    # al terminar cada corrida del Actor de X/Twitter. Ver
    # `app/api/v1/endpoints/apify.py`. Las claves, el handle y el tope de
    # llamadas al modelo siguen acá porque los usan los dos caminos.
    #
    #: Claves radiales que se ingieren como despacho. Se comparan por prefijo de
    #: tupla tras normalizar, de modo que `10-4` también captura `10-0-4` y
    #: `10-4-1`. Ver `vocabulary.matches_key` y `vocabulary.parse_key`.
    #:
    #: La familia entera y no sólo el rescate vehicular: la 10-0/10-1
    #: (estructural), la 10-2 (pastizales) y la 10-3 (rescate) describen
    #: emergencias que el resto del sistema ya sabe clasificar —están en
    #: `CODE_TYPES`— y dejarlas fuera desperdiciaba la fuente de mayor confianza
    #: del catálogo para todo lo que no fuera un choque.
    #:
    #: `12` es la forma literal "CLAVE 12" que publica el Cuerpo de Valparaíso,
    #: sin familia por delante. Necesita `parse_key` para ser reconocida: hasta
    #: ese cambio, configurarla no producía error **ni coincidencia**.
    #: Claves del CBV que se ingieren. **Sólo emergencias reales.**
    #:
    #: La lista anterior —`10-0` … `10-4` y `12`— era de otro sistema de claves.
    #: En el CBV, `10` es «abastecer o aspirar agua» y `12` es «Academia de
    #: Cuerpo»: una academia podía entrar al mapa como emergencia confirmada
    #: con confianza 1.00, y ninguna clave de accidente real estaba configurada.
    #:
    #: Quedan fuera a propósito 7, 8, 10, 11, 12, 13 y 14 — ver
    #: `vocabulary.NON_INCIDENT_CODES`, que explica cada una.
    #:
    #: Agregar una clave nueva exige antes darle significado en `CLAVE_MEANINGS`
    #: y tipo en `CODE_TYPES`. Sin tipo, el despacho entra como `OTHER` y un
    #: incendio de la fuente de peso 1.00 queda fuera de la familia donde el
    #: mapa lo busca.
    BOMBEROS_ACCIDENT_KEYS: CsvList = Field(
        default_factory=lambda: [
            "1-1", "1-2", "1-3",           # incendio estructural
            "2-1", "2-2", "2-3",           # incendio forestal
            "3-1", "3-2",                  # incendio vehicular
            "4-1", "4-2",                  # materiales peligrosos
            "5-1", "5-2",                  # rescate vehicular = siniestro vial
            "6-1", "6-2", "6-3", "6-4", "6-5",  # rescate de personas
            "9-1", "9-2", "9-3",           # túneles
            "15",                          # otros servicios
        ]
    )
    #: Claves del CBVM (@CBVM132, Viña del Mar y Concón) que se ingieren.
    #:
    #: Lista aparte y no la de arriba, porque es **otro sistema de claves**:
    #: `3` es incendio vehicular (el CBV usa 3-1/3-2), `9` es emergencia
    #: estructural industrial (en el CBV son túneles), `10` es «otros servicios»
    #: (en el CBV es abastecer agua y NO se ingiere). Ver `CBVM_CODE_TYPES` en
    #: `vocabulary`, con la tabla comparativa.
    #:
    #: Quedan fuera 7, 8, 11, 12, 13 y 16 —internas o sin hecho en el mapa, ver
    #: `CBVM_NON_INCIDENT_CODES`— y la 14, que las dos versiones de la tabla
    #: publicada no se ponen de acuerdo en qué es. Si la central la usa, el
    #: webhook lo avisa como «clave no configurada».
    BOMBEROS_CBVM_KEYS: CsvList = Field(
        default_factory=lambda: [
            "1-1", "1-2", "1-3",           # incendio estructural
            "2-1", "2-2", "2-3",           # incendio forestal
            "3",                           # incendio vehicular
            "4-1", "4-2",                  # gases / materiales peligrosos
            "5-1", "5-2",                  # rescate vehicular = siniestro vial
            "6-1", "6-2", "6-3",           # rescate de personas
            "9",                           # emergencia estructural industrial
            "10",                          # otros servicios
            "15",                          # accidente aéreo
        ]
    )
    #: Claves de Los Andes - Calle Larga (@despachoscbla) que se ingieren.
    #:
    #: Esquema NACIONAL: las emergencias son la familia 10 y el `0` del medio
    #: NO se colapsa (`10-0-4` es un incendio, `10-4` un rescate vehicular). Ver
    #: `CBLA` en `vocabulary`. Se configuran por familia; lo que cada familia
    #: tiene de servicio (10-5-5 higienización, 10-8-3 monitoreo, 10-8-7
    #: apertura de inmueble, 10-8-9 presunta desgracia) lo saca
    #: `CBLA_INTERNAS` aunque comparta prefijo. Quedan fuera 10-9 (servicios),
    #: 10-10 (re-ignición), 10-11, 10-12, 10-15 y toda la radio (familias 0–9).
    BOMBEROS_CBLA_KEYS: CsvList = Field(
        default_factory=lambda: [
            "10-0",                        # estructural (10-0-1 … 10-0-6)
            "10-1",                        # incendio vehicular
            "10-2",                        # vegetación, basural, contenedor
            "10-3",                        # rescates (animales y 10-3-10 incluidos)
            "10-4",                        # rescate vehicular
            "10-5",                        # materiales peligrosos
            "10-6",                        # gas
            "10-7",                        # eléctrico
            "10-8",                        # no clasificado (árboles, clima, agua…)
            "10-14",                       # aparato aéreo caído
            "10-16",                       # incendio en túnel
        ]
    )
    #: Claves PROVISIONALES de Quilpué (@CBQuilpue) y Quillota (@cbquillota).
    #:
    #: No hay tabla publicada de ninguno de los dos: sólo se ingieren las
    #: familias 1–6 de la costa, que coinciden en el CBV, el CBVM y Villa
    #: Alemana (ver `CBQUILPUE` en `vocabulary`). La 17-x de Quilpué y la 15 de
    #: Quillota son internas; el resto avisa «clave no configurada».
    BOMBEROS_QUILPUE_KEYS: CsvList = Field(
        default_factory=lambda: [
            "1-1", "1-2", "1-3",
            "2-1", "2-2", "2-3", "2-4",
            "3",
            "4-1", "4-2", "4-3",
            "5-1", "5-2", "5-3", "5-4",
            "6-1", "6-2", "6-3", "6-5", "6-6", "6-7",
        ]
    )
    BOMBEROS_QUILLOTA_KEYS: CsvList = Field(
        default_factory=lambda: [
            "1-1", "1-2", "1-3",
            "2-1", "2-2", "2-3", "2-4",
            "3",
            "4-1", "4-2", "4-3",
            "5-1", "5-2", "5-3", "5-4",
            "6-1", "6-2", "6-3", "6-5", "6-6", "6-7",
        ]
    )
    #: Tope de consultas a Nominatim por entrega del webhook.
    #:
    #: Mismo mecanismo y mismo motivo que `TRANSPORTE_INFORMA_MAX_GEOCODES`: el
    #: geocodificador respeta 1 req/s, así que un lote grande podría tener la
    #: entrega del inbox ocupada durante minutos. Lo que exceda el tope entra sin
    #: coordenadas, que es el estado en el que entraban todos los despachos
    #: antes de que este paso existiera.
    #:
    #: 25 y no 100 (`APIFY_WEBHOOK_MAX_ITEMS`) porque una entrega real trae unos
    #: pocos despachos: el resto de los items del dataset son tuits de la cuenta
    #: que el filtro por clave ya descartó.
    BOMBEROS_MAX_GEOCODES: int = Field(default=25, ge=0, le=200)
    #: Cuenta de RESPALDO para citar y decodificar un despacho cuyo tuit no dice
    #: quién lo publicó. Desde que el Task raspa dos centrales, cada despacho
    #: usa la cuenta que trae el propio tuit (`author.userName` o la URL) y el
    #: diccionario de su Cuerpo; esto sólo rige para Actors que no informan el
    #: autor.
    BOMBEROS_SOURCE_HANDLE: str = "@CGI_CBV"
    #: Tope de decodificaciones por corrida. Un despacho es una llamada al
    #: modelo; una noche de temporal con 200 avisos no puede convertirse en 200
    #: llamadas facturadas. Lo que exceda el tope cae a las reglas, que no
    #: cuestan nada, y queda anotado en `raw_data._extraction.mode`.
    BOMBEROS_MAX_LLM_CALLS: int = Field(default=25, ge=0, le=500)

    # -- Accidentes viales: Transporte Informa -------------------------------
    #: Portal del MTT para la Región de Valparaíso. Es HTML (WordPress), no una
    #: API: se raspa. Vacío = apagado.
    #:
    #: Ojo con dos cosas que cambiaron en el rediseño de 2026 y que dejaron este
    #: collector devolviendo cero avisos:
    #:
    #: * la región pasó de **ruta a subdominio**. `www…/valparaiso/` da 404;
    #: * apunta a `/estado-de-la-movilidad/` y no a la portada. Es la sección que
    #:   lista los avisos de tránsito; la portada sólo muestra los destacados.
    #:
    #: Se buscaron salidas mejores que raspar y las dos están cerradas: la REST
    #: API de WordPress responde `401 Rest API disabled` y el feed RSS da error.
    TRANSPORTE_INFORMA_URL: str = "https://valparaiso.transporteinforma.cl/estado-de-la-movilidad/"
    #: 15 s, más corto que los 30 del resto de las fuentes. No es una
    #: preferencia: es el único collector cuyo ciclo completo tiene un techo
    #: duro. Tras el GET vienen hasta `MAX_GEOCODES` llamadas a Nominatim a 1 s
    #: cada una, y con el intervalo en 600 s la corrida tiene que caber holgada
    #: dentro de su propia cadencia. Un portal que tarda 30 s en responder no
    #: está lento, está caído; esperarlo el doble no mejora el resultado y sí
    #: retrasa las geocodificaciones que vienen detrás.
    TRANSPORTE_INFORMA_TIMEOUT_SECONDS: float = 15.0
    #: 10 min. **Se mantiene, y la decisión está razonada en el docstring de
    #: `app/collectors/traffic/transporteinforma_worker.py`.**
    #:
    #: En resumen: la carga sobre el MTT es de UN GET por ciclo —144 al día— y
    #: ampliar la capa táctica no la cambió en absoluto, porque lo que creció es
    #: lo que se hace con el HTML después de traerlo. Lo que expone a un bloqueo
    #: no es ese volumen sino el PATRÓN: una petición cada 600 s exactos desde
    #: una IP de datacenter. Eso se ataca con `COLLECTOR_JITTER_RATIO` y con el
    #: respeto al 429, no bajando una frecuencia que es justamente el valor de
    #: esta fuente.
    TRANSPORTE_INFORMA_POLL_INTERVAL_SECONDS: int = 600  # 10 min
    #: User-Agent propio del scraper del MTT.
    #:
    #: Antes usaba `NOMINATIM_USER_AGENT`, que estaba a mano y era falso: le
    #: decía al portal del Ministerio que quien lo visitaba era el cliente de
    #: OpenStreetMap. Un operador del MTT mirando su log no tenía forma de saber
    #: quién le pegaba ni a quién escribirle.
    #:
    #: Lleva navegador y plataforma porque un WordPress detrás de un WAF trata
    #: un UA sin ellos como bot y devuelve 403, y lleva **el nombre del proyecto
    #: y una URL de contacto** porque esa es la parte que importa: la diferencia
    #: entre un scraper que se identifica y uno que se esconde es que al primero
    #: le piden bajar la frecuencia y al segundo le bloquean la IP.
    #:
    #: **Sin tildes, y no por estilo.** Las cabeceras HTTP se codifican en
    #: latin-1 y httpx rechaza de plano un valor con caracteres fuera de ASCII:
    #: la primera versión decía "V Región" y hacía que `AsyncClient` lanzara
    #: `UnicodeEncodeError` antes de abrir la conexión. El collector no habría
    #: fallado al raspar sino al construirse — cada corrida, sin llegar nunca a
    #: la red, con un mensaje que no menciona la cabecera por ninguna parte.
    #: Vacío = navegador + identidad de `app.core.identidad` (repo y correo de
    #: contacto). Con valor, se manda tal cual: ASCII, sin tildes.
    TRANSPORTE_INFORMA_USER_AGENT: str = ""
    #: Tope de geocodificaciones por corrida. A 1 s por llamada (ver
    #: NOMINATIM_MIN_INTERVAL_SECONDS), 20 avisos son 20 segundos de corrida.
    #: Sin tope, un día de temporal con 300 avisos dejaría al worker cinco
    #: minutos colgado del rate limit ajeno.
    TRANSPORTE_INFORMA_MAX_GEOCODES: int = Field(default=20, ge=1, le=200)

    # -- Infraestructura vial dañada: MOP / Dirección de Vialidad ------------
    #: Capa **0** del MapServer de emergencias viales. Es la que trae todas las
    #: emergencias vigentes como punto —el propio MOP lleva las lineales a punto
    #: de forma referencial—; la 1 son sólo las puntuales y la 2 las lineales.
    #:
    #: Apuntar a `/MapServer` a secas, sin el `/0`, devuelve los metadatos del
    #: servicio y ninguna emergencia: el collector le añade `/query` a lo que
    #: encuentre acá, así que la URL tiene que llevar ya el número de capa.
    #:
    #: Servicio abierto, sin credencial. Se consulta con `f=json` y NO con
    #: `f=geojson`: este servidor declara `supportedQueryFormats: "JSON, AMF"` y
    #: ante geojson responde con el cuerpo vacío en vez de con un error.
    MOP_VIALIDAD_URL: str = (
        "https://rest-sit.mop.gob.cl/arcgis/rest/services/VIALIDAD/Emergencias_Vialidad/MapServer/0"
    )
    MOP_VIALIDAD_TIMEOUT_SECONDS: float = 45.0
    #: Una hora, y no los 300 s de las fuentes de siniestros.
    #:
    #: El servicio declara en su propia descripción que se actualiza **los lunes
    #: alrededor de las 15:00**, y a diario sólo mientras dure un evento de
    #: emergencia. A 5 minutos serían 2 016 peticiones por cada dato nuevo
    #: contra un servidor público, para leer 30 filas idénticas.
    #:
    #: Una hora es el compromiso: durante un temporal —que es cuando el MOP sí
    #: publica a diario— la capa se refresca con retraso máximo de una hora, que
    #: para infraestructura dañada es de sobra.
    MOP_VIALIDAD_POLL_INTERVAL_SECONDS: int = Field(default=3600, ge=300, le=86400)

    # -- Vehículos: GBV SpA (feed paralelo, fuera del mapa) ------------------
    #: Raíz del sitio. El collector arma desde acá las tres secciones que lee
    #: —`/denuncias/`, `/vehiculos-recuperados` y `/vehiculos-abandonados/`— y
    #: resuelve los enlaces relativos de cada tarjeta contra la página donde
    #: aparecieron (`../denuncias/…` en una, `./denuncias/…` en otra).
    GBV_BASE_URL: str = "https://gbvspa.cl"
    #: Navegador + identificación, el mismo criterio que
    #: `TRANSPORTE_INFORMA_USER_AGENT`: un WAF trata a un UA sin navegador como
    #: bot, y quien opera el sitio —una ONG— tiene derecho a saber quién le pega
    #: y a quién escribirle. Sin tildes: las cabeceras HTTP van en latin-1.
    #: Vacío = navegador + identidad de `app.core.identidad` (repo y correo de
    #: contacto). Con valor, se manda tal cual: ASCII, sin tildes.
    GBV_USER_AGENT: str = ""
    GBV_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, le=120)
    #: 30 minutos. GBV publica unas pocas denuncias al día y la primera página
    #: del listado cubre semanas: consultar más seguido sólo le pegaría más a un
    #: servidor chico para leer la misma portada.
    GBV_POLL_INTERVAL_SECONDS: int = Field(default=1800, ge=300, le=86400)
    #: Tope de fichas de detalle por corrida. El detalle es lo único que trae
    #: marca, modelo, color y fecha; lo que exceda el tope NO se ingresa a
    #: medias, se difiere a la corrida siguiente —sigue siendo "nuevo" para el
    #: delta— para que ninguna fila quede para siempre sin su detalle.
    GBV_MAX_DETALLES: int = Field(default=15, ge=1, le=100)
    #: Páginas del listado de denuncias que se pueden leer en una corrida. Sólo
    #: se pasa a la siguiente si la anterior venía entera nueva (hubo una caída
    #: larga); en régimen, una.
    GBV_MAX_PAGINAS: int = Field(default=2, ge=1, le=10)
    #: Pausa entre peticiones consecutivas al sitio. Cortesía, no rate limit.
    GBV_PAUSA_SEGUNDOS: float = Field(default=0.5, ge=0.0, le=10.0)

    # -- Apify ----------------------------------------------------------------
    #
    # Desde 2026-09-22 Apify queda para UNA cosa: el Task de X que raspa a las
    # centrales de Bomberos (desde el 2026-09-30, cinco: ver `SISTEMAS_CLAVES`) y entrega por
    # `/apify/webhook`. Las capas de Instagram y de prensa por X se borraron el
    # 2026-09-23: lo que cubrían lo toma la prensa local por RSS, sin costo.
    #
    #: Cada cuántos minutos corre el Schedule del Task de X en el panel de
    #: Apify. **No lo dispara**: es lo que la salud espera, y con él decide
    #: cuándo el webhook de Bomberos lleva demasiado callado. Tres cadencias sin
    #: entrega = `stale`. Si el Schedule se espacia para cuidar la cuota, hay que
    #: subirlo acá también, o las tres familias quedan marcadas en falso.
    APIFY_X_SCHEDULE_MINUTES: int = Field(default=30, ge=5, le=1440)

    #: Token de la API de Apify. Viaja SIEMPRE en la cabecera `Authorization`,
    #: nunca en la query: una URL con el token dentro termina en los logs de
    #: acceso y en el mensaje de cualquier `CollectorError`, que se serializa a
    #: `collector_runs.error`. Vacío = el webhook no puede leer los datasets.
    APIFY_TOKEN: str = ""
    APIFY_BASE_URL: str = "https://api.apify.com/v2"
    APIFY_TIMEOUT_SECONDS: float = 30.0

    # -- Apify: webhook de entrada -------------------------------------------
    #
    # Apify **avisa** al terminar una corrida y nosotros leemos el dataset que
    # nos nombra. El endpoint sólo encola el aviso en `collector_runs`; lo
    # procesa el proceso de workers (ver `apify_webhook_service`).
    #
    #: Secreto compartido con Apify. Se compara con la cabecera
    #: `X-AlertaV-Apify-Secret` (o `Authorization: Bearer …`) de cada llamada.
    #:
    #: Vacío = **sin verificar**, y eso es deliberado y peligroso a la vez. La
    #: ruta es pública por naturaleza: Apify llama desde sus IPs, no desde la
    #: nuestra. Sin secreto, cualquiera que descubra la URL puede hacer que este
    #: backend lea un dataset ajeno y lo ingiera como despachos de Bomberos, que
    #: es la fuente de confianza 1.00 del sistema — la que lleva un incidente a
    #: certeza sin corroboración. Se deja vacío por defecto para que un
    #: despliegue de prueba arranque, y el endpoint **avisa en cada llamada**
    #: mientras siga así.
    APIFY_WEBHOOK_SECRET: str = ""
    #: Actors (o Tasks) autorizados a entregar por el webhook. Vacío = **cualquiera**.
    #:
    #: El secreto responde "¿quién llama?"; esto responde "¿qué trae?", y son dos
    #: preguntas distintas. La ruta `/apify/webhook` no es genérica: está cableada
    #: a Bomberos —`EventSource.BOMBEROS`, `parse_tweet` buscando claves 10-x— y
    #: cualquier dataset que le llegue se ingiere como despachos, que es la
    #: fuente de confianza 1.00 del sistema. Un secreto es un secreto de cuenta,
    #: no de Actor: TODOS los webhooks del panel comparten el mismo, así que sin
    #: esta lista basta apuntar un segundo Actor a la URL para que sus items
    #: entren por la puerta de Bomberos.
    #:
    #: El daño real que evita no es la inyección, es el **silencio**. Un Actor
    #: equivocado no revienta: sus items no tienen claves, así que la corrida
    #: cierra en `success` con 0 insertados y deja una fila verde en
    #: `collector_runs` etiquetada `bomberos_apify_webhook`. Esa fila es
    #: exactamente la señal que se mira para responder "¿está llegando el
    #: webhook?", y un Actor ajeno disparando cada media hora la falsifica: la
    #: tabla se ve viva mientras los despachos de X llevan días sin entrar.
    #:
    #: **El valor NO es `usuario~actor`.** Esa forma sirve para las rutas de la
    #: API, pero el webhook manda el id corto (`nfp1fpt5gUlBwPcor`). Se saca del
    #: rechazo: el log del `ignored` cita los ids que llegaron, listos para
    #: pegar acá. Admite varios separados por coma
    #: —el del Actor y el del Task son distintos— y se compara contra ambos.
    APIFY_BOMBEROS_ACTOR_IDS: CsvList = Field(default_factory=list)
    #: Items a leer del dataset que anuncia el webhook, del más nuevo al más
    #: viejo.
    APIFY_WEBHOOK_MAX_ITEMS: int = Field(default=100, ge=1, le=1000)
    #: Antigüedad máxima de un tuit para tomarlo como descripción del presente.
    #: Una corrida del Actor puede arrastrar el timeline entero de la cuenta; sin
    #: este corte, la primera llamada del webhook ingeriría meses de despachos
    #: con la hora de hoy y llenaría el mapa de siniestros que ya se resolvieron.
    APIFY_WEBHOOK_MAX_AGE_MINUTES: int = Field(default=180, ge=5, le=1440)
    #: Cuentas de X que el Task tiene que traer en CADA entrega, sin arroba.
    #:
    #: Existe por lo que pasó del 2026-08-31 al 2026-09-29: el Actor de X dejó
    #: de devolver tuits en el plan Free —diez items `{"noResults": true}` por
    #: corrida— y cada entrega cerró en `success` con 0 insertados. Un mes sin
    #: un despacho con la salud en verde. Con el orden «más recientes primero»
    #: y sin filtro de fecha, un Actor que funciona trae SIEMPRE los últimos
    #: tuits de cada cuenta, aunque sean de ayer. Así que una entrega sin un
    #: solo tuit propio de una de estas cuentas no es calma: es ceguera.
    #:
    #: - Ninguna de las cuentas aparece → `degraded` (la salud lo muestra).
    #: - Falta alguna → `partial`, con la cuenta nombrada en el detalle.
    #:
    #: Vacía = no se exige nada (así corren los tests).
    #:
    #: Desde el 2026-10-06 incluye a @TTIValparaiso (ver
    #: `APIFY_X_TRANSITO_HANDLES`): el canario la trae como a las centrales y su
    #: ausencia es la misma ceguera.
    APIFY_X_CUENTAS_ESPERADAS: CsvList = Field(
        default_factory=lambda: [
            "CGI_CBV", "CBVM132", "despachoscbla", "CBQuilpue", "cbquillota",
            "TTIValparaiso",
        ]
    )
    #: Cuentas de X de **tránsito** que viajan en el mismo Task que las
    #: centrales de Bomberos (desde el 2026-10-06).
    #:
    #: Hoy una: @TTIValparaiso, TransporteInforma Región de Valparaíso, el canal
    #: del MTT en X. Es la misma fuente que `transporte_informa` lee en la web,
    #: pero fresca: la web arrastra avisos de semanas. Sus tuits NO se leen con
    #: una tabla de claves —no tienen— sino con la tubería del MTT
    #: (`clasificar_transito` → Gemini → Nominatim) y entran como
    #: `transporte_informa`, confianza 0,80, en la corrida
    #: `transporte_informa_x`. Vacía = sus tuits se descartan como cualquier
    #: cuenta sin tabla.
    APIFY_X_TRANSITO_HANDLES: CsvList = Field(default_factory=lambda: ["TTIValparaiso"])
    #: Antigüedad máxima de un tuit de tránsito. Más larga que la de un
    #: despacho (`APIFY_WEBHOOK_MAX_AGE_MINUTES`): un corte de ruta o un desvío
    #: siguen vigentes horas. Seis horas es la `edad_max` de la familia
    #: `traffic` en el motor: lo más viejo ya no se agruparía igual.
    APIFY_X_TRANSITO_MAX_AGE_MINUTES: int = Field(default=360, ge=5, le=1440)
    #: Llamadas al modelo y a Nominatim por entrega para los tuits de tránsito.
    #: Una entrega trae a lo sumo 6 por cuenta (`maxItemsPerTarget`).
    APIFY_X_TRANSITO_MAX_GEOCODES: int = Field(default=6, ge=0, le=50)
    #: Tasks (o Actors) cuya entrega es el **canario** diario, no la corrida
    #: con ventana de tiempo.
    #:
    #: Desde el 2026-09-30 el Task principal pide sólo lo publicado en los
    #: últimos minutos (`within_time`), así que una entrega vacía es normal y
    #: la ceguera ya no se puede medir en ella. La mide el canario: un Task sin
    #: ventana, una vez al día, con los últimos tuits de cada cuenta. Sus
    #: entregas se registran como `bomberos_apify_canario` y SÓLO en ellas se
    #: exige ver cada cuenta de `APIFY_X_CUENTAS_ESPERADAS`.
    #:
    #: Vacía = no hay canario y la ceguera se mide en cada entrega, como antes
    #: (así corren los tests viejos y un despliegue sin ventana).
    APIFY_X_CANARIO_IDS: CsvList = Field(default_factory=list)
    #: Cada cuántas horas corre el canario. Es lo que la salud espera de él:
    #: tres cadencias sin entrega = `stale`.
    APIFY_X_CANARIO_HORAS: int = Field(default=24, ge=1, le=168)
    #: Cada cuánto el proceso de workers mira el inbox del webhook. Es la
    #: latencia máxima entre el aviso de Apify y el despacho en el mapa: una
    #: consulta indexada cada 10 s cuesta nada y mantiene la promesa de
    #: «segundos, no minutos» del webhook.
    APIFY_INBOX_POLL_SECONDS: int = Field(default=10, ge=2, le=300)

    # -- Prensa local: Alerta Noticias y Pura Noticia -------------------------
    #: Portales de la V Región, raspados de forma nativa y a costo cero. Formato
    #: `slug|nombre|feed_url|portada_url[|confianza]`, separando varios con `;` —
    #: el mismo idioma que `FIRMS_SOURCES` y `OPENMETEO_COMUNAS`. Vacío =
    #: collector apagado (el constructor lanza y la corrida queda `failed` a la
    #: vista).
    #:
    #: Las dos URL son opcionales por separado, y los valores por defecto usan
    #: esa asimetría porque los portales son distintos de verdad:
    #:
    #: * **Alerta Noticias** (verificado el 3 de septiembre de 2026) es WordPress
    #:   7.1 y `category/valparaiso/feed/` devuelve RSS 2.0 ya acotado a la
    #:   región, con `<guid>` estable, `<pubDate>` con hora y `<category>` con la
    #:   comuna. Se declara feed Y portada: la segunda es el respaldo si el feed
    #:   cae o llega vacío. Lleva confianza propia — ver la nota de abajo.
    #: * **Pura Noticia** redirige `www.puranoticia.cl` a `puranoticia.pnt.cl`,
    #:   que NO es WordPress y **no publica RSS**: el documento no trae
    #:   `<link rel="alternate">` y `/rss`, `/rss.xml` y `/feed` devuelven cuerpo
    #:   vacío. Su campo de feed va en blanco a propósito — apuntarlo a un feed
    #:   inexistente costaría una petición fallida y una advertencia por corrida,
    #:   para siempre. Se lee su sección regional por HTML.
    #:
    #: Ojo con las dos portadas: son la sección regional y no la raíz. La raíz
    #: mezcla nacional, internacional y deportes, y ese filtro por sección es lo
    #: único que acota geográficamente a dos medios que no son regionales.
    #: **Sitio del Suceso salió de rotación el 2026-09-03**, y no por estar
    #: caído: su feed responde y está al día — verificado desde un navegador el
    #: mismo día. Lo que devuelve 403 es nuestra petición, y no por las
    #: cabeceras: ya mandamos `LOCAL_NEWS_USER_AGENT` y `Accept-Language: es-CL`.
    #: Cloudflare está rechazando por reputación de IP de datacenter y por la
    #: huella TLS de httpx, que no se arreglan desde el cliente.
    #:
    #: Se desregistra como se desregistró Waze: la fila queda escrita y comentada
    #: en vez de borrada, porque el código sigue siendo correcto y el día que
    #: aflojen la regla vuelve descomentando una línea. Mantenerlo activo costaba
    #: una petición fallida y una advertencia cada 15 minutos, para siempre —el
    #: ruido que enseña a ignorar el amarillo—. La cuenta @sitiodelsuceso ya lo
    #: cubre parcialmente por la ruta de prensa de X.
    #:
    #:     sitiodelsuceso|Sitio del Suceso|https://www.sitiodelsuceso.cl/feed/|
    #:     https://www.sitiodelsuceso.cl/
    #:
    #: `alertanoticias` entró el mismo día y por el hueco que dejó Instagram: es
    #: el mismo publicador que @alertanoticiasvalparaiso, la cuenta más grande de
    #: las tres, que lleva semanas en `restricted_page`. Su WordPress publica
    #: `category/valparaiso/feed/`, ya filtrado a la región, gratis y sin Apify.
    #:
    #: Entra con **0.35 propio y no con el 0.60 del resto**, que es el quinto
    #: campo de la fila. Es el mismo número que el worker de Instagram le da al
    #: mismo publicador, y por el mismo motivo: republica lo que le llega por
    #: mensaje directo, sin segunda fuente y sin corrección. Leerlo por RSS en vez
    #: de por Apify cambia cómo llega el texto, no quién lo escribió.
    #:
    #: `margamarga` y `quintaprensa` entraron el 2026-09-22, cuando se retiraron
    #: los Tasks de Instagram y de prensa de Apify. Suman el Marga Marga
    #: (Quilpué, Villa Alemana, Limache, Olmué) y el interior, que ninguna otra
    #: fuente de prensa cubría. Los dos son WordPress con RSS estándar.
    #:
    #: **Sólo feed, sin portada de respaldo**, y es deliberado. La portada de
    #: Prensa Marga Marga es un tema de WordPress cuyo ticker «Noticias de última
    #: hora» son los diez posts más recientes SIN FECHA visible —el más viejo, de
    #: mayo de 2025—. El camino HTML deja pasar lo que no trae fecha y lo
    #: estampa con la hora de la corrida, así que un feed caído convertiría un
    #: homicidio de hace un año en una emergencia de hoy.
    #:
    #: Ojo con la expectativa: medido el 2026-09-22, Prensa Marga Marga publica
    #: una nota por semana (la última, del 11-09) y Quinta Prensa no publica
    #: desde el 06-07. Lo que esos medios cuentan al minuto va a su Instagram,
    #: no a la web. Entran porque no cuestan nada y cubren territorio, no porque
    #: vayan a ser rápidos.
    #:
    #: **Quinta Prensa salió de rotación el 2026-09-30**: su última nota es del
    #: 06-07-2026. Queda escrita y comentada, como Sitio del Suceso, por si vuelve:
    #:
    #:     quintaprensa|Quinta Prensa|https://www.quintaprensa.cl/feed/|
    #:
    #: **Quinta Visión Ahora entró el mismo día (§L).** WordPress con RSS, muy
    #: activa (10 notas en dos horas y media el 30-09) y con «AHORA:» en lo
    #: urgente. `<category>` trae «Región Valparaíso» y la comuna, pero el feed
    #: mezcla notas nacionales: por eso el sexto campo exige «Región Valparaíso»
    #: (ver `NewsPortal.categoria_requerida`). Sólo feed, sin portada, y con la
    #: confianza de medio por defecto (0,60): tiene redacción propia.
    #:
    #: **Y salió de rotación esa misma tarde**, como Sitio del Suceso: Cloudflare
    #: le responde a Render con el desafío «Just a moment…» (HTTP 403). Desde una
    #: IP chilena el feed responde, pero pasarlo por el proxy de Oracle sería
    #: esquivar a propósito su protección contra bots, y la regla de este
    #: collector es no hacerlo (ver `LocalNewsCollector._client`). Vuelve si el
    #: medio nos habilita o si afloja la regla; la fila queda escrita:
    #:
    #:     quintavision|Quinta Visión Ahora|https://www.quintavisionahora.cl/feed/|||
    #:     Región Valparaíso
    LOCAL_NEWS_SOURCES: str = (
        "alertanoticias|Alerta Noticias|"
        "https://alertanoticias.cl/category/valparaiso/feed/|"
        "https://alertanoticias.cl/category/valparaiso/|0.35;"
        "puranoticia|Pura Noticia||https://puranoticia.pnt.cl/region-valparaiso;"
        "margamarga|Prensa Marga Marga|https://prensamargamarga.cl/feed/|"
    )
    #: Cabeceras de navegador. El `User-Agent` por defecto de httpx
    #: (`python-httpx/0.28.1`) es lo primero que mira una regla básica de
    #: Cloudflare o un plugin de seguridad de WordPress, y la respuesta es un 403
    #: —o un desafío servido con HTTP 200, que es peor porque parece una página—.
    #:
    #: No confundir con `NOMINATIM_USER_AGENT`: ese servicio exige lo contrario,
    #: un agente identificable con contacto real, y rechaza a los anónimos. Dos
    #: contratos opuestos, dos clientes, dos cabeceras.
    #:
    #: Esto NO resuelve un desafío JavaScript de verdad, y no pretende hacerlo:
    #: si alguno de los portales lo activa, lo correcto es dejar de raspar y
    #: pedir acceso, no perseguirlo.
    #: Vacío = navegador + identidad de `app.core.identidad` (repo y correo de
    #: contacto). Con valor, se manda tal cual: ASCII, sin tildes.
    LOCAL_NEWS_USER_AGENT: str = ""
    LOCAL_NEWS_TIMEOUT_SECONDS: float = 30.0
    #: Cadencia. 15 minutos: un medio redacta y publica, no transmite. Consultar
    #: cada cinco devolvería tres veces la misma portada y golpearía a un
    #: servidor ajeno sin ganar nada.
    LOCAL_NEWS_POLL_INTERVAL_SECONDS: int = 900  # 15 min
    #: Noticias a mirar por portal y por corrida, de la más nueva a la más vieja.
    LOCAL_NEWS_MAX_ITEMS_PER_PORTAL: int = Field(default=40, ge=1, le=200)
    #: Antigüedad máxima para considerar que la nota describe el presente. Cuatro
    #: horas, más holgado que las tres de Instagram: un medio publica después de
    #: confirmar, y esa demora editorial es justamente lo que lo hace valer 0.60.
    LOCAL_NEWS_MAX_AGE_MINUTES: int = Field(default=240, ge=5, le=1440)
    #: Tope de geocodificaciones por corrida. El más bajo de las capas que
    #: comparten el limitador de Nominatim (MTT 20, Bomberos 25, prensa 10): una
    #: noticia llega después que el aviso oficial, así que si hay que recortar
    #: algo, que sea esto.
    LOCAL_NEWS_MAX_GEOCODES: int = Field(default=10, ge=1, le=200)
    #: Confianza de una señal de prensa. 0.60 es el techo de la banda de `MEDIA`
    #: en `confidence.py` (`max_weight`), aunque `SOURCE_BASE_CONFIDENCE[MEDIA]`
    #: diga 0.70: el motor recorta a 0.60 igual, y emitir el número que el motor
    #: va a usar evita archivar en `raw_events` una confianza que nadie respeta.
    LOCAL_NEWS_CONFIDENCE: float = Field(default=0.60, ge=0.0, le=1.0)

    # -- Meteorología: lluvia y riesgo de inundación -------------------------
    #: API de pronóstico de Open-Meteo. Sin credencial y sin registro en su nivel
    #: abierto (10.000 llamadas/día, uso no comercial, atribución CC BY 4.0).
    #:
    #: Las comunas viajan en LOTES —`latitude` y `longitude` aceptan listas
    #: separadas por coma— así que una corrida cuesta un puñado de llamadas y no
    #: una por comuna. Ver `OPENMETEO_CHUNK_SIZE` para por qué dejaron de viajar
    #: las 36 juntas, y `app/collectors/weather/openmeteo_client.py` para el
    #: emparejamiento por posición, que es la parte delicada de ese ahorro.
    OPENMETEO_URL: str = "https://api.open-meteo.com/v1/forecast"
    #: Modelo. `best_match` deja que Open-Meteo elija por coordenada el de mayor
    #: resolución disponible, que para Chile central son los globales de 9-11 km.
    #: Fijar uno concreto (`ecmwf_ifs025`, `gfs_seamless`) sólo tiene sentido para
    #: comparar pronósticos, no para operar.
    OPENMETEO_MODEL: str = "best_match"
    OPENMETEO_TIMEOUT_SECONDS: float = 30.0
    #: Cadencia. 30 minutos, y no los 300 s de las fuentes de siniestros, porque
    #: la naturaleza del dato es otra: los modelos globales se recalculan cada 3 a
    #: 6 horas, así que preguntar cada cinco minutos devolvería treinta y cinco
    #: veces el mismo número. Media hora acota el retraso frente a un ciclo nuevo
    #: a 30 minutos y gasta 48 llamadas al día de las 10.000 disponibles.
    #:
    #: El piso de 300 s no es capricho: por debajo de eso no hay ganancia posible
    #: —el modelo no ha cambiado— y sí un servicio ajeno consultado de más.
    #: Bajarlo durante un temporal no sirve; lo que sirve es que la ventana de
    #: 24 h se recalcula en cada corrida.
    OPENMETEO_POLL_INTERVAL_SECONDS: int = Field(default=1800, ge=300, le=86_400)
    #: Días de pronóstico a pedir. Dos, no uno: la ventana de 24 h que se evalúa
    #: es móvil, así que a las 22:00 hacen falta las horas de mañana. Pedir siete
    #: días sería traer seis de payload que nadie mira.
    OPENMETEO_FORECAST_DAYS: int = Field(default=2, ge=1, le=7)
    #: Horas hacia adelante que se evalúan.
    OPENMETEO_WINDOW_HOURS: int = Field(default=24, ge=1, le=48)
    #: Comunas a consultar. Vacío = las 36 continentales de
    #: `app/collectors/weather/comunas.py`. Formato `nombre|lat|lon`, separando
    #: varias con `;` — el mismo que `FIRMS_SOURCES` y compañía.
    OPENMETEO_COMUNAS: str = ""
    #: Comunas por petición. **Doce, y no las 36 de una vez.**
    #:
    #: Las 36 juntas venían fallando en producción con
    #: `JSONDecodeError: Expecting ',' delimiter` a mitad del cuerpo: 36 objetos
    #: × 7 variables × 48 pasos es un JSON de cientos de kB que el nivel abierto
    #: de Open-Meteo corta antes de terminar de escribirlo. Un cuerpo truncado no
    #: es un error de la API —llega con 200 y sin `{"error": true}`— así que el
    #: único síntoma es el decodificador reventando, y la corrida entera se
    #: pierde por la comuna número treinta y tantos.
    #:
    #: Doce mantiene cada respuesta en decenas de kB y la URL muy por debajo de
    #: los límites de longitud, a cambio de tres llamadas por corrida en vez de
    #: una: 144 al día contra las 10.000 del nivel abierto. El ahorro que este
    #: cliente cuida sigue intacto —lo caro era una llamada POR COMUNA, no tres
    #: por corrida— y ahora un lote que falle no se lleva a los otros dos.
    OPENMETEO_CHUNK_SIZE: int = Field(default=12, ge=1, le=100)
    #: --- Grilla de lluvia (mapa de calor) -----------------------------------
    #:
    #: Además de las 36 comunas, una grilla regular sobre la región y el mar de
    #: enfrente, para pintar la lluvia como un campo continuo (el mapa de calor
    #: de la capa). La guarda un trabajo del proceso de workers en
    #: `weather_grids`; ver `app/services/rain_grid_service.py`.
    #:
    #: La caja es la MISMA que `MAP_MAX_BOUNDS` de la PWA (`config/map.ts`):
    #: todo lo que el mapa deja mirar tiene dato, y el único borde del campo queda
    #: en el límite del mapa, adonde casi nadie llega (§L, 30-09-2026). Antes la
    #: caja era la región y el corte se veía como una recta sobre la cordillera.
    #: Si se cambia `MAP_MAX_BOUNDS`, se cambia esto: lo vigila
    #: `frontend/src/config/rainGridBounds.test.ts`.
    #:
    #: Presupuesto: con paso 0,2° la caja son 25 × 26 = 650 puntos, leídos cada
    #: 3 h (8 corridas al día). Si Open-Meteo contara cada punto como una llamada
    #: (su página de precios no lo aclara), son 5200 al día, que sumadas a las
    #: 1728 de las comunas quedan en 6928, bajo las 10.000 del nivel abierto y por
    #: debajo de las 8568 que gastaba la caja anterior leída cada hora.
    #:
    #: Por qué cada 3 h y no cada hora: los modelos globales se recalculan cada 3
    #: a 6 h, así que leer cada hora devolvía casi siempre la misma foto. Y por qué
    #: 0,2° y no 0,15°: con 0,15° la caja entera son 1122 puntos y no cabe en el
    #: presupuesto; la interpolación bilineal de la PWA suaviza la diferencia.
    RAIN_GRID_ENABLED: bool = True
    RAIN_GRID_STEP_DEGREES: float = Field(default=0.2, ge=0.05, le=1.0)
    RAIN_GRID_WEST: float = -73.4
    RAIN_GRID_SOUTH: float = -35.6
    RAIN_GRID_EAST: float = -68.6
    RAIN_GRID_NORTH: float = -30.6
    RAIN_GRID_POLL_INTERVAL_SECONDS: int = Field(default=10_800, ge=900, le=86_400)
    #: Puntos por petición. Una sola variable × 48 pasos por punto: 25 puntos
    #: dejan cada respuesta en ~40 KB, lejos del truncado que describe
    #: `OPENMETEO_CHUNK_SIZE`.
    RAIN_GRID_CHUNK_SIZE: int = Field(default=25, ge=1, le=100)
    #: Pausa entre lotes de la grilla, en segundos. Open-Meteo corta con HTTP 429
    #: por encima de ~600 puntos por minuto (cuenta cada coordenada como una
    #: llamada): la primera corrida de la caja de 650 puntos, sin pausa, cayó en
    #: el lote 25 de 26 (§L, 30-09-2026). Con 4 s, los 26 lotes toman ~100 s y
    #: quedan en ~375 puntos por minuto.
    RAIN_GRID_BATCH_PAUSE_SECONDS: float = Field(default=4.0, ge=0, le=60)
    #: Reintento tras una corrida fallida. Con la cadencia de 3 h, esperar la
    #: próxima corrida dejaba el mapa con la foto vieja media tarde.
    RAIN_GRID_RETRY_SECONDS: int = Field(default=900, ge=60, le=86_400)
    #: Horas hacia adelante sobre las que se toma el máximo.
    RAIN_GRID_HOURS: int = Field(default=24, ge=1, le=48)
    #: Umbrales de `riesgo_inundacion`. Cualquiera de los tres levanta el flag.
    #: NO son umbrales oficiales de la DMC ni de SENAPRED: son una hipótesis
    #: calibrable, elegida por la geografía del caso (cerros con pendiente fuerte,
    #: quebradas canalizadas, drenaje urbano antiguo). La forma de fijarlos es
    #: cruzar esta capa con los avisos de vía cortada de Transporte Informa a lo
    #: largo de un invierno. Ver el encabezado de `weather/umbrales.py`.
    #:
    #: Cada regla tiene dos tramos desde la v2: `aviso` (ámbar en el widget) y
    #: `critical` (rojo). La intensidad horaria describe el DRENAJE urbano
    #: saturándose y se publica como amenaza `lluvia`; los acumulados en 3 y 24 h
    #: describen el SUELO perdiendo infiltración y se publican como `remocion`.
    #: Son dos mecanismos con dos respuestas distintas.
    OPENMETEO_INTENSITY_MM_H: float = Field(default=5.0, gt=0.0, le=200.0)
    OPENMETEO_INTENSITY_CRITICAL_MM_H: float = Field(default=10.0, gt=0.0, le=400.0)
    OPENMETEO_ACCUM_3H_MM: float = Field(default=15.0, gt=0.0, le=500.0)
    OPENMETEO_ACCUM_3H_CRITICAL_MM: float = Field(default=25.0, gt=0.0, le=800.0)
    OPENMETEO_ACCUM_24H_MM: float = Field(default=40.0, gt=0.0, le=1000.0)
    OPENMETEO_ACCUM_24H_CRITICAL_MM: float = Field(default=60.0, gt=0.0, le=2000.0)
    #: Milímetros en la ventana por debajo de los cuales la comuna no genera
    #: evento POR LLUVIA. Sin este piso, 36 comunas × 48 corridas al día llenarían
    #: `raw_events` de filas que dicen "no llovió".
    #:
    #: Desde la v2 ya no es el único motivo de emisión: una comuna con 0,0 mm y
    #: un índice UV de 12 sí genera evento. Ver `Pronostico.hay_senal`.
    OPENMETEO_MIN_INGEST_MM: float = Field(default=0.2, ge=0.0, le=100.0)

    # -- Meteorología: propagación de incendios (regla 30-30-30) -------------
    #: El «Factor 30-30-30» que CONAF y la prensa usan para comunicar riesgo de
    #: propagación: ≥30 °C, ≤30 % de humedad y ráfagas ≥30 km/h **en la misma
    #: hora**. Es el tramo CRÍTICO.
    #:
    #: Su límite hay que decirlo: no es un índice validado —no lo respalda un
    #: modelo de combustible— y la propia CONAF mostró en Laguna Verde,
    #: Valparaíso, que con 18 °C, 48 % y 20 km/h un incendio puede ser igual de
    #: devastador. En la costa de esta región el 30-30-30 casi nunca se cumple y
    #: los incendios ocurren igual.
    OPENMETEO_FIRE_TEMP_C: float = Field(default=30.0, gt=0.0, le=60.0)
    OPENMETEO_FIRE_HUMIDITY_PCT: float = Field(default=30.0, gt=0.0, le=100.0)
    OPENMETEO_FIRE_GUST_KMH: float = Field(default=30.0, gt=0.0, le=200.0)
    #: Tramo de AVISO, y ésta sí es una decisión propia y no un estándar: cubre
    #: el régimen costero en que esta región se quema de verdad. Queda por encima
    #: de las cifras de Laguna Verde a propósito — bajar hasta ahí dejaría el
    #: widget en ámbar todo el verano, que es la forma más rápida de que nadie
    #: vuelva a mirarlo.
    OPENMETEO_FIRE_WATCH_TEMP_C: float = Field(default=25.0, gt=0.0, le=60.0)
    OPENMETEO_FIRE_WATCH_HUMIDITY_PCT: float = Field(default=40.0, gt=0.0, le=100.0)
    OPENMETEO_FIRE_WATCH_GUST_KMH: float = Field(default=25.0, gt=0.0, le=200.0)
    #: Ventana táctica del fuego y del viento. 12 h y no 24: es el horizonte de
    #: un turno operativo. Anunciar a las 22:00 la condición de mañana a las
    #: 16:00 no cambia ninguna decisión de esta noche.
    OPENMETEO_FIRE_WINDOW_HOURS: int = Field(default=12, ge=1, le=48)

    # -- Meteorología: viento por sí solo ------------------------------------
    #: Independiente del 30-30-30 porque el mecanismo es otro: a 60 km/h se
    #: suspende el combate aéreo y empiezan a caer ramas sobre el tendido —la
    #: capa de cortes de luz de este mismo sistema— y a 80 km/h el daño
    #: estructural es esperable con o sin fuego. Un temporal invernal de 70 km/h
    #: y 12 °C no cumple ninguna condición de incendio y sigue importando.
    OPENMETEO_GUST_WATCH_KMH: float = Field(default=60.0, gt=0.0, le=300.0)
    OPENMETEO_GUST_CRITICAL_KMH: float = Field(default=80.0, gt=0.0, le=400.0)

    # -- Meteorología: calor con riesgo a la salud ---------------------------
    #: NO es una declaración de «ola de calor», y el vocabulario del código lo
    #: refleja. La DMC define ola de calor por percentil 90 diario de la
    #: climatología de cada estación durante tres días consecutivos: este
    #: collector no tiene la serie 1991-2020 ni ve tres días, así que llamarlo
    #: así sería mentir sobre el aval.
    #:
    #: Lo que sí se puede medir es el riesgo fisiológico, que es un número
    #: absoluto: 32 °C es donde la DMC empieza a emitir avisos por altas
    #: temperaturas para los valles interiores de esta región, y 36 °C es el
    #: techo de los avisos que efectivamente emitió para Valparaíso en 2026.
    OPENMETEO_HEAT_WATCH_C: float = Field(default=32.0, gt=0.0, le=60.0)
    OPENMETEO_HEAT_CRITICAL_C: float = Field(default=36.0, gt=0.0, le=60.0)
    #: Noche tropical. No dispara sola: AGRAVA un aviso de calor a crítico. La
    #: carga epidemiológica del calor no la produce el pico de las 15:00 sino la
    #: ausencia de alivio nocturno — si la mínima no baja de 20 °C, el cuerpo no
    #: recupera entre exposiciones.
    OPENMETEO_TROPICAL_NIGHT_C: float = Field(default=20.0, gt=0.0, le=40.0)
    #: 24 h: la máxima de mañana sí cambia lo que alguien hace esta noche
    #: (hidratación, horario de trabajo en terreno, adultos mayores).
    OPENMETEO_HEAT_WINDOW_HOURS: int = Field(default=24, ge=1, le=48)

    # -- Meteorología: índice UV ---------------------------------------------
    #: Los ÚNICOS umbrales de esta capa que son un estándar internacional y no
    #: una hipótesis: bandas «muy alto» (rojo, ≥8) y «extremo» (morado, ≥11) del
    #: índice UV global de la OMS/OMM, idénticas en las escalas de la EPA y del
    #: ICNIRP. No se mueven: cambiarlos sería inventar una escala nueva con el
    #: nombre y los colores de una que la gente ya reconoce.
    OPENMETEO_UV_WATCH: float = Field(default=8.0, gt=0.0, le=20.0)
    OPENMETEO_UV_CRITICAL: float = Field(default=11.0, gt=0.0, le=20.0)
    #: La ventana más corta del módulo, y ahí está la gracia. El UV sólo existe
    #: de día y su bloque útil es el entorno del mediodía solar: 6 h encienden el
    #: aviso la mañana del día peligroso y lo apagan al atardecer, en vez de
    #: dejarlo prendido 24 h por algo que va a pasar mañana.
    OPENMETEO_UV_WINDOW_HOURS: int = Field(default=6, ge=1, le=24)
    #: Desvío máximo, en grados, entre el punto pedido y el centro de la celda de
    #: grilla que devuelve la API. Por encima de eso se avisa: es la guarda contra
    #: que la respuesta llegue en otro orden que el pedido, que es lo único que
    #: empareja cada pronóstico con su comuna. 0.5° ≈ 50 km, holgado a propósito
    #: —una celda de 11 km en la costa puede caer lejos— para que el aviso
    #: signifique "el orden se movió" y no "la grilla es gruesa".
    OPENMETEO_MAX_DRIFT_DEGREES: float = Field(default=0.5, gt=0.0, le=10.0)

    # -- Gemini: extracción de entidades desde texto libre -------------------
    #: Clave de la API de Google AI Studio. Vacía = el extractor cae a la
    #: heurística de reglas y lo deja anotado en cada señal. No se apaga el
    #: collector: media capa funcionando es mejor que ninguna.
    GEMINI_API_KEY: str = ""
    #: Modelo. Un `flash-lite` estable: la tarea es extracción de entidades de
    #: una frase, no razonamiento, y la latencia entra en el presupuesto de una
    #: corrida que además paga 1 s por geocodificación.
    #:
    #: Se fija un modelo ESTABLE y no un alias `-latest` a propósito: `latest`
    #: se intercambia solo con cada versión nueva y un cambio de comportamiento
    #: del extractor llegaría a producción sin que nadie desplegara nada. Con un
    #: nombre fijo, actualizar es una decisión.
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_TIMEOUT_SECONDS: float = 20.0
    #: Tope de llamadas por corrida. Acota el gasto y la latencia: un día de
    #: temporal con 300 avisos no puede convertirse en 300 llamadas facturadas.
    GEMINI_MAX_CALLS_PER_RUN: int = Field(default=25, ge=1, le=500)
    #: Temperatura 0: se quiere extracción determinista, no redacción. Con
    #: temperatura alta el modelo "mejora" los nombres de calle, que es
    #: exactamente lo que no debe hacer.
    GEMINI_TEMPERATURE: float = Field(default=0.0, ge=0.0, le=2.0)

    # -- Nominatim (OpenStreetMap) -------------------------------------------
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"
    #: Intervalo MÍNIMO entre llamadas, en segundos. La política de uso de
    #: Nominatim exige un máximo de 1 req/s **por IP**, y el castigo por
    #: excederlo es el bloqueo de la IP, no un 429. Como todo el backend sale por
    #: una sola IP, el limitador es global al proceso: ver
    #: `app.collectors.nominatim.RateLimiter`. No bajar de 1.0.
    NOMINATIM_MIN_INTERVAL_SECONDS: float = Field(default=1.0, ge=1.0, le=60.0)
    NOMINATIM_TIMEOUT_SECONDS: float = 20.0
    #: Nominatim exige un User-Agent identificable con contacto real; las
    #: peticiones anónimas se rechazan. Vacío = `AlertaV/1.0 (+<repo>; <correo>)`
    #: de `app.core.identidad`; con valor, se manda tal cual.
    NOMINATIM_USER_AGENT: str = ""
    #: Sesgo de la búsqueda hacia Chile. Evita que "Avenida Argentina" resuelva
    #: en Buenos Aires, que es exactamente lo que hace Nominatim sin esto.
    NOMINATIM_COUNTRY_CODES: str = "cl"

    # -- Overpass (cruces de calles) -------------------------------------------
    #: Cuando una fuente nombra dos calles («LAS MONJAS / ANDRES BELLO»), el
    #: cruce se calcula con Overpass sobre los datos de OSM. Ver
    #: `app.collectors.overpass`. Apagado = el punto queda sobre una calle.
    OVERPASS_ENABLED: bool = True
    #: Servidores públicos, en orden. Si uno responde 429 o 5xx, el siguiente.
    OVERPASS_URLS: list[str] = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    OVERPASS_TIMEOUT_SECONDS: float = Field(default=20.0, ge=5.0, le=120.0)
    #: Radio de búsqueda alrededor del punto de Nominatim cuando la comuna no
    #: tiene caja propia (`nominatim.COMUNA_VIEWBOX`). Una avenida larga puede
    #: tener el punto de Nominatim lejos del cruce.
    OVERPASS_AROUND_M: float = Field(default=4000.0, ge=500.0, le=20_000.0)
    #: Dos calles que no comparten nodo cuentan como cruce si pasan a menos de
    #: esto (una calle que muere a metros de la otra, un dibujo impreciso).
    OVERPASS_MAX_GAP_M: float = Field(default=60.0, ge=0.0, le=300.0)

    # -- Motor de correlación -------------------------------------------------
    # Todos estos valores son hipótesis de partida, no constantes físicas. Se
    # calibran contra la ventana de recolección con `/events/{id}/neighbours`.
    #: Radio de agrupación espacial, en metros reales.
    CORRELATION_RADIUS_M: float = Field(default=1500.0, ge=100.0, le=20_000.0)
    #: Radio, brecha temporal y edad máxima POR FAMILIA
    #: (`app/services/correlation/perfiles.py`), filtro temporal al adherir una
    #: señal a un incidente y ventana por hora de ingesta para lo que llega
    #: tarde. En `false`, el motor vuelve exactamente a lo anterior: un solo
    #: `CORRELATION_RADIUS_M` y ventana por `timestamp`. Es el interruptor de
    #: emergencia si la calibración nueva agrupa peor.
    CORRELATION_PERFILES: bool = True
    #: Sólo se crean incidentes con señales dentro de la V Región (o a ~2 km),
    #: medido contra `comunas_region` (migración 0015). La caja `REGION_*`
    #: cubre media Región Metropolitana: sin esto, los cortes de CGE en
    #: Santiago aparecían en el mapa como incidentes «sin comuna». Sin la tabla
    #: no filtra nada. En `false`, vuelve a agrupar todo lo de la caja.
    CORRELATION_SOLO_REGION: bool = True
    #: ¿La prensa (`EventSource.MEDIA`) entra al motor? Desde el 2026-10-06,
    #: **no**: una noticia llega con horas de atraso y abría pines que ya no
    #: describían el presente (con `CORRELATION_MIN_SIGNALS_FOR_INCIDENT=1`,
    #: una sola nota bastaba). Ahora la prensa sólo se lee en
    #: `GET /feed/noticias`: no abre incidentes, no se pega a uno, no mueve
    #: confianza ni punto, y no genera avisos push. Con el interruptor apagado
    #: el motor además desvincula la prensa de los incidentes abiertos y
    #: descarta los que sólo tenían prensa. En `true`, vuelve lo de antes.
    CORRELATION_PRENSA: bool = False
    #: Ventana hacia atrás de señales que el motor considera en cada pasada.
    CORRELATION_WINDOW_HOURS: int = Field(default=4, ge=1, le=168)
    #: Antigüedad máxima de un incidente para que una señal nueva se le adhiera.
    #: Mayor que la ventana de agrupación: un incendio de CONAF vive días y sus
    #: señales de corroboración siguen llegando mucho después del primer racimo.
    CORRELATION_MATCH_WINDOW_HOURS: int = Field(default=12, ge=1, le=336)
    #: Ventana del vínculo por sector (`link_method = 'sector_text'`): una señal
    #: se une a un incidente de su misma familia que nombra el mismo sector si
    #: éste tuvo señales en las últimas N horas. Mucho más corta que
    #: `CORRELATION_MATCH_WINDOW_HOURS` porque la evidencia es más débil —dos
    #: textos que dicen "Miraflores Alto", no dos puntos que se tocan— y un
    #: sector es grande: dos incendios en él con medio día de diferencia son
    #: dos incendios.
    CORRELATION_SECTOR_WINDOW_HOURS: int = Field(default=3, ge=1, le=48)
    #: UTM 19S. Proyección métrica que cubre la Región de Valparaíso; permite
    #: usar ST_ClusterDBSCAN con `eps` en metros en vez de grados.
    CORRELATION_UTM_SRID: int = 32719
    #: Tope de señales por pasada. Acota el costo de un backlog.
    CORRELATION_MAX_EVENTS_PER_PASS: int = Field(default=5000, ge=1, le=100_000)
    #: Señales mínimas para abrir un incidente desde fuentes no institucionales.
    #: En 1 el mapa muestra todo (con su confianza a la vista); en 2 sólo lo
    #: corroborado. Una fuente confirmatoria abre incidente siempre.
    CORRELATION_MIN_SIGNALS_FOR_INCIDENT: int = Field(default=1, ge=1, le=10)
    #: Sin señales nuevas por este tiempo, el incidente pasa a `stale`.
    CORRELATION_STALE_HOURS: int = Field(default=12, ge=1, le=720)
    #: Una alerta se considera vigente mientras la capa la siga publicando. Se
    #: mide sobre `updated_at` (último refresco del upsert), no sobre
    #: `timestamp`, que es la fecha de declaración y puede ser de hace días.
    CORRELATION_ALERT_VALIDITY_HOURS: int = Field(default=24, ge=1, le=720)
    #: ¿Adosar alertas de ámbito regional o nacional a cada incidente?
    #: Por defecto NO: una alerta temprana preventiva nacional por temporada de
    #: incendios está vigente todo el verano y adosarla a todo teñiría el mapa
    #: entero sin aportar información sobre ningún incidente en particular.
    CORRELATION_ATTACH_REGIONAL_ALERTS: bool = False
    #: Ventana que el endpoint `/incidents/active` considera "activo".
    CORRELATION_ACTIVE_WINDOW_HOURS: int = Field(default=48, ge=1, le=720)
    #: Cadencia del worker de correlación.
    CORRELATION_POLL_INTERVAL_SECONDS: int = Field(default=120, ge=15, le=86_400)

    # -- Reportes ciudadanos: anti-spam y publicación (§C, 2026-09-30) -------
    #: Segundos entre reportes de un mismo DISPOSITIVO. Se cuenta en la base
    #: (`raw_events`), así que sobrevive a un deploy o a un reinicio, que era lo
    #: que borraba el límite en memoria. 0 desactiva los límites (tests, demo).
    CITIZEN_REPORT_MIN_INTERVAL_SECONDS: int = Field(default=600, ge=0, le=86_400)
    #: Reportes que admite una misma RED (/24 en IPv4, /48 en IPv6) dentro de la
    #: ventana de arriba. Más de uno a propósito: una familia, una oficina o el
    #: CGNAT de un operador móvil salen por la misma dirección, y ver el mismo
    #: incendio desde el mismo edificio no es spam.
    CITIZEN_REPORTS_PER_NETWORK: int = Field(default=3, ge=1, le=100)
    #: Segundos mínimos entre dos envíos de la misma IP, en memoria. Es sólo un
    #: freno de ráfagas antes de tocar la base: el límite de verdad es el de
    #: dispositivo y red.
    CITIZEN_REPORT_BURST_SECONDS: int = Field(default=20, ge=0, le=3600)
    #: Reportes ciudadanos independientes que hacen falta para que un incidente
    #: sostenido SÓLO por ciudadanos aparezca en el mapa. Independientes = de
    #: dispositivos distintos Y de redes distintas. Con cualquier otra fuente
    #: (Bomberos, CONAF, FIRMS, prensa…) el incidente se publica igual que antes.
    CITIZEN_QUORUM: int = Field(default=3, ge=1, le=20)
    #: Minutos que tiene un incidente sólo ciudadano para juntar el quórum o una
    #: segunda fuente. Pasado ese plazo sin lograrlo se descarta (`dismissed`).
    #: Mientras tanto no se publica, así que el plazo puede ser holgado: es el
    #: tiempo que tienen los vecinos para reportar lo mismo.
    CITIZEN_UNCORROBORATED_TTL_MINUTES: int = Field(default=15, ge=1, le=1440)
    #: Horas que un incidente sólo ciudadano ya publicado sigue en el mapa sin
    #: reportes nuevos. Más corto que `CORRELATION_STALE_HOURS`: nadie oficial
    #: lo sostiene.
    CITIZEN_ONLY_STALE_HOURS: int = Field(default=3, ge=1, le=72)
    #: Freno global: si en los últimos 10 minutos entran más reportes que esto
    #: en toda la región, ningún incidente sólo ciudadano se publica hasta que
    #: baje. En una emergencia real grande hay otras fuentes; una avalancha de
    #: reportes sin ninguna otra señal huele a ataque. 0 lo desactiva.
    CITIZEN_GLOBAL_BRAKE_PER_10MIN: int = Field(default=40, ge=0, le=10_000)
    #: Precisión GPS máxima aceptada, en metros. Un navegador que ubica por IP
    #: informa kilómetros: ese punto no sirve para correlacionar nada.
    CITIZEN_MAX_ACCURACY_M: float = Field(default=1000.0, ge=10.0, le=100_000.0)
    #: Margen de la geocerca alrededor de la caja de la región, en grados. Un
    #: reporte fuera de la V Región continental se rechaza.
    CITIZEN_GEOFENCE_MARGIN_DEG: float = Field(default=0.05, ge=0.0, le=2.0)
    #: Sal del HMAC con que se guardan el dispositivo y la red de quien reporta.
    #: Vacía = se usa `OPERATOR_TOKEN`; sin ninguna de las dos, una constante
    #: (sólo aceptable fuera de producción). Cambiarla hace que los reportes
    #: viejos y los nuevos dejen de reconocerse entre sí, nada más.
    CITIZEN_HASH_SALT: str = ""
    #: Clave secreta de Cloudflare Turnstile. Vacía = sin verificación (local y
    #: hasta que se configure en Render). La clave del sitio va en el frontend
    #: (`VITE_TURNSTILE_SITE_KEY`).
    TURNSTILE_SECRET_KEY: str = ""
    TURNSTILE_VERIFY_URL: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    TURNSTILE_TIMEOUT_SECONDS: float = Field(default=5.0, ge=1.0, le=30.0)
    #: Moderación del texto con Gemini. Sin `GEMINI_API_KEY` el texto queda
    #: pendiente (oculto) hasta que un operador lo apruebe.
    CITIZEN_MODERATION_ENABLED: bool = True
    #: Tope de textos que se mandan a Gemini por pasada del motor.
    CITIZEN_MODERATION_MAX_PER_PASS: int = Field(default=10, ge=1, le=100)
    #: Textos más viejos que esto ya no se moderan solos: quedan para el operador.
    CITIZEN_MODERATION_MAX_AGE_HOURS: int = Field(default=24, ge=1, le=720)

    # -- Caché de respuestas de lectura ----------------------------------------
    #: Segundos que se reutiliza una respuesta de las rutas que la PWA sondea
    #: (incidentes, salud, sismos, cortes). Los datos cambian cada 2 minutos
    #: como mínimo (el motor), así que 20 s no se notan, y convierten N personas
    #: mirando el mapa en una sola consulta a la base cada 20 s. 0 la apaga.
    API_RESPONSE_CACHE_SECONDS: int = Field(default=20, ge=0, le=300)

    # -- Notificaciones push (Web Push) --------------------------------------
    #: Clave privada VAPID: el escalar P-256 de 32 bytes en base64url. La
    #: pública se deriva de ésta y la PWA la pide a `/push/public-key`, así que
    #: no hay una segunda variable que pueda quedar desincronizada. Se genera
    #: con `python scripts/generate_vapid_keys.py`.
    #:
    #: **Cambiarla invalida todas las suscripciones.** El navegador ata cada
    #: suscripción a la clave pública con la que se creó; con otra, el servicio
    #: de push responde 403 a cada envío. Vacía = push desactivado: la API lo
    #: informa y el notificador no arranca.
    VAPID_PRIVATE_KEY: str = ""
    #: Contacto del emisor, exigido por el RFC 8292: `mailto:` o `https://`. Los
    #: servicios de push escriben ahí antes de bloquear a un emisor que abusa.
    VAPID_SUBJECT: str = ""
    #: Interruptor general. Con las claves puestas y esto en False la API sigue
    #: aceptando suscripciones pero nadie envía nada: sirve para cortar los
    #: avisos durante una falla sin perder a los suscritos.
    PUSH_ENABLED: bool = True
    #: Cadencia del notificador. No tiene sentido que sea más corta que la del
    #: motor de correlación, que es quien crea los incidentes que se avisan.
    PUSH_POLL_INTERVAL_SECONDS: int = Field(default=60, ge=15, le=3600)
    #: Radio de aviso de emergencias para una suscripción nueva, en metros.
    PUSH_INCIDENT_RADIUS_M: float = Field(default=5000.0, ge=500.0, le=20_000.0)
    #: Radio de aviso por categoría para quien no eligió el suyo, en metros
    #: (§K, 2026-10-05). Cada persona lo cambia en la PWA; 0 = no avisar esa
    #: categoría. Las categorías son las familias del motor
    #: (`models.enums.INCIDENT_FAMILY`) más los cortes de agua, que no son
    #: incidentes. `PUSH_INCIDENT_RADIUS_M` queda para lo que no cae en ninguna.
    PUSH_RADIO_FIRE_M: float = Field(default=5000.0, ge=0.0, le=20_000.0)
    PUSH_RADIO_TRAFFIC_M: float = Field(default=2000.0, ge=0.0, le=20_000.0)
    PUSH_RADIO_POWER_M: float = Field(default=1000.0, ge=0.0, le=20_000.0)
    PUSH_RADIO_HYDRO_M: float = Field(default=3000.0, ge=0.0, le=20_000.0)
    PUSH_RADIO_OTHER_M: float = Field(default=2000.0, ge=0.0, le=20_000.0)
    PUSH_RADIO_WATER_M: float = Field(default=1000.0, ge=0.0, le=20_000.0)
    #: Avisos de cortes de agua de Esval. Sólo cortes que empezaron hace menos
    #: de esto: uno que lleva días no es novedad.
    PUSH_WATER_CUT_MAX_AGE_HOURS: int = Field(default=12, ge=1, le=72)
    #: Confianza mínima para avisar de un incidente. 0.30 es el borde del tramo
    #: `possible`: una señal aislada (tramo `unsafe`) no despierta a nadie. Los
    #: incidentes confirmados por CONAF o Bomberos y los cortes de luz se avisan
    #: siempre, porque su fuente ya es la autoridad sobre el hecho.
    PUSH_INCIDENT_MIN_CONFIDENCE: float = Field(default=0.30, ge=0.0, le=1.0)
    #: Fuentes DISTINTAS que tienen que sostener un incidente para avisarlo, si
    #: ninguna es oficial. Con 1, un solo píxel de FIRMS (0.40) o una sola alerta
    #: de Waze (0.40) ya superan el umbral de arriba: la chimenea de Ventanas
    #: despertaría a Quintero cada noche. Con 2, hace falta que alguien más vea
    #: lo mismo.
    PUSH_INCIDENT_MIN_SOURCES: int = Field(default=2, ge=1, le=10)
    #: Un incidente que apareció hace más que esto ya no se avisa aunque recién
    #: alcance el umbral. Evita que, tras una caída del notificador, lleguen de
    #: golpe los avisos de toda la tarde.
    PUSH_INCIDENT_MAX_AGE_MINUTES: int = Field(default=180, ge=10, le=1440)
    #: Magnitud mínima de un sismo para avisar. Por debajo de 3,5 casi nadie lo
    #: siente aunque esté encima.
    PUSH_SEISMIC_MIN_MAGNITUDE: float = Field(default=3.5, ge=0.0, le=10.0)
    #: Antigüedad máxima de un sismo para avisarlo. El CSN y el USGS publican
    #: con minutos de retraso y los collectors pasan cada 5; más allá de media
    #: hora el aviso ya no le dice nada nuevo a quien lo sintió.
    PUSH_SEISMIC_MAX_AGE_MINUTES: int = Field(default=30, ge=5, le=360)
    #: Tope del radio de percepción, el mismo que usa el mapa
    #: (`MAX_REACH_KM` en `frontend/src/domain/seismicReach.ts`).
    PUSH_SEISMIC_MAX_REACH_KM: float = Field(default=400.0, ge=10.0, le=2000.0)
    #: Fallos seguidos (que no sean 404/410) tras los cuales una suscripción se
    #: da por perdida y se borra.
    PUSH_MAX_CONSECUTIVE_FAILURES: int = Field(default=10, ge=1, le=1000)
    #: Días que se guarda el registro de envíos. Sólo sirve para no repetir un
    #: aviso y para auditar; pasado un mes no cumple ninguna de las dos cosas.
    PUSH_DELIVERY_RETENTION_DAYS: int = Field(default=30, ge=1, le=365)
    #: Segundos entre notificaciones de prueba desde una misma IP.
    PUSH_TEST_MIN_INTERVAL_SECONDS: int = Field(default=60, ge=0, le=86_400)
    #: Servicios de push aceptados, por dominio (vale el dominio y cualquier
    #: subdominio). El servidor hace un POST a la URL que registre cualquiera:
    #: sin esta lista, alguien podría suscribir `http://169.254.169.254/...` o
    #: un servicio interno y usar el notificador para golpearlo. Son los de
    #: Chrome/Android/Samsung (FCM), Firefox, Safari/iOS y Edge.
    PUSH_ALLOWED_ENDPOINT_HOSTS: CsvList = Field(
        default_factory=lambda: [
            "fcm.googleapis.com",
            "android.googleapis.com",
            "push.services.mozilla.com",
            "push.apple.com",
            "notify.windows.com",
        ]
    )

    # -- Ingesta -------------------------------------------------------------
    # Tolerancia para eventos con timestamp futuro (desfase de reloj de fuentes)
    INGEST_FUTURE_TOLERANCE_SECONDS: int = 300

    @field_validator(
        "CORS_ORIGINS",
        "FIRMS_SOURCES",
        "CONAF_STATES",
        "CONAF_REGIONS",
        "SENAPRED_REGIONS",
        "USGS_EVENT_TYPES",
        "BOMBEROS_ACCIDENT_KEYS",
        "BOMBEROS_CBVM_KEYS",
        "BOMBEROS_CBLA_KEYS",
        "BOMBEROS_QUILPUE_KEYS",
        "BOMBEROS_QUILLOTA_KEYS",
        "APIFY_BOMBEROS_ACTOR_IDS",
        "APIFY_X_CUENTAS_ESPERADAS",
        "APIFY_X_CANARIO_IDS",
        "APIFY_X_TRANSITO_HANDLES",
        "PUSH_ALLOWED_ENDPOINT_HOSTS",
        "OVERPASS_URLS",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Permite definir listas como CSV en el .env."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _produccion_sin_huecos(self) -> Settings:
        """En producción, la app no arranca con un secreto flojo o un placeholder.

        Existe porque la auditoría del 2026-09-23 encontró que todo lo de abajo
        tenía un valor por defecto que dejaba la puerta abierta en silencio: un
        webhook sin secreto acepta despachos de peso 1.00 de cualquiera, y un
        User-Agent con `TU_CORREO` le miente a Nominatim sobre a quién escribir
        antes de bloquear la IP.

        Si el arranque falla, Render deja corriendo el despliegue anterior: un
        error de configuración se ve en el panel en vez de llegar a producción.
        Sólo se exige lo que ya está configurado hoy; `OPERATOR_TOKEN` vacío NO
        impide arrancar (las rutas de operación responden 503), pero si se
        define tiene que ser largo.
        """
        if self.ENVIRONMENT != "production":
            return self

        errores: list[str] = []

        secreto = self.APIFY_WEBHOOK_SECRET.strip()
        if len(secreto) < 32 or not secreto.isascii():
            errores.append("APIFY_WEBHOOK_SECRET: mínimo 32 caracteres ASCII")
        if not [actor for actor in self.APIFY_BOMBEROS_ACTOR_IDS if actor.strip()]:
            errores.append(
                "APIFY_BOMBEROS_ACTOR_IDS vacío: el webhook aceptaría cualquier Actor"
            )

        token = self.OPERATOR_TOKEN.strip()
        if token and (len(token) < 32 or not token.isascii()):
            errores.append("OPERATOR_TOKEN: mínimo 32 caracteres ASCII")

        revisar = ["FIRMS_MAP_KEY"]
        if self.PUSH_ENABLED:
            revisar.append("VAPID_SUBJECT")
        for nombre in revisar:
            valor = str(getattr(self, nombre)).strip()
            if not valor or any(marca in valor for marca in PLACEHOLDERS):
                errores.append(f"{nombre} vacío o con un placeholder ({valor!r})")

        # El User-Agent de Nominatim puede ir vacío (se arma solo), pero si se
        # define no puede ser una plantilla, y en cualquier caso tiene que haber
        # un correo real a quién escribir: es la condición de uso del servicio.
        agente = self.NOMINATIM_USER_AGENT.strip()
        if agente and any(marca in agente for marca in PLACEHOLDERS):
            errores.append(f"NOMINATIM_USER_AGENT con un placeholder ({agente!r})")
        if not self.contacto_email:
            errores.append(
                "sin correo de contacto: definir CONTACT_EMAIL (o un mailto: en "
                "VAPID_SUBJECT, o un correo en NOMINATIM_USER_AGENT)"
            )
        for nombre in _AGENTES:
            if not str(getattr(self, nombre)).isascii():
                errores.append(f"{nombre}: las cabeceras HTTP sólo admiten ASCII")

        # Vacío no impide arrancar: el collector de Esval falla solo, con el
        # motivo, y el resto del backend no depende de él. Pero si se define,
        # tiene que ser un proxy con credenciales: uno abierto en internet es
        # un proxy para cualquiera.
        errores.extend(_problemas_del_proxy(self.ESVAL_PROXY_URL, "ESVAL_PROXY_URL"))

        if errores:
            raise ValueError(
                "configuración de producción inválida: " + "; ".join(errores)
            )
        return self

    @property
    def contacto_email(self) -> str | None:
        """Correo a quién escribir, para los User-Agent. None si no hay uno real.

        Por orden: `CONTACT_EMAIL`, el `mailto:` de `VAPID_SUBJECT`, el correo
        que traiga `NOMINATIM_USER_AGENT`. Los dos últimos son los que Render ya
        tiene configurados; el primero existe para no depender de ellos.
        """
        candidatos = (
            self.CONTACT_EMAIL,
            self.VAPID_SUBJECT.removeprefix("mailto:"),
            self.NOMINATIM_USER_AGENT,
        )
        for texto in candidatos:
            encontrado = _CORREO.search(texto or "")
            if encontrado and not any(m in encontrado.group(0) for m in PLACEHOLDERS):
                return encontrado.group(0)
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def region_bbox(self) -> BoundingBox:
        return BoundingBox(
            west=self.REGION_WEST,
            south=self.REGION_SOUTH,
            east=self.REGION_EAST,
            north=self.REGION_NORTH,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usgs_bbox(self) -> BoundingBox:
        """Zona central de Chile para el recorte de sismos. Ver `USGS_WEST`."""
        return BoundingBox(
            west=self.USGS_WEST,
            south=self.USGS_SOUTH,
            east=self.USGS_EAST,
            north=self.USGS_NORTH,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def csn_bbox(self) -> BoundingBox:
        """Recorte del catálogo del CSN. Ver `CSN_WEST`.

        Es una caja aparte de `usgs_bbox` aunque hoy tengan los mismos valores:
        las dos redes tienen umbrales y coberturas distintos, y tarde o temprano
        habrá razón para recortar una y no la otra. Compartir la caja obligaría
        a mover ambas a la vez.
        """
        return BoundingBox(
            west=self.CSN_WEST,
            south=self.CSN_SOUTH,
            east=self.CSN_EAST,
            north=self.CSN_NORTH,
        )

    def _rewrite_dsn(self, scheme: str, *, keep_query: bool) -> str | None:
        """Reescribe `DATABASE_URL` al driver pedido. None si no está definida.

        Se toca sólo el esquema y, cuando corresponde, el query string. El
        usuario y la contraseña se dejan intactos —vienen percent-encoded desde
        el panel del proveedor y volver a codificarlos los rompería.

        `keep_query` distingue los dos drivers: psycopg2 entiende `sslmode`,
        `options` y compañía; asyncpg no y aborta la conexión si se los pasan.
        Para asyncpg el TLS viaja aparte, en `DB_SSL_MODE`.
        """
        raw = self.DATABASE_URL.strip()
        if not raw:
            return None

        parts = urlsplit(raw)
        if parts.scheme not in _DSN_SCHEMES:
            raise ValueError(
                f"DATABASE_URL usa el esquema '{parts.scheme}'; se esperaba uno de "
                f"{sorted(_DSN_SCHEMES)}"
            )
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query if keep_query else "", ""))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """DSN async (asyncpg) para la aplicación."""
        override = self._rewrite_dsn("postgresql+asyncpg", keep_query=False)
        if override:
            return override
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """DSN síncrono (psycopg2) — lo usa Alembic."""
        override = self._rewrite_dsn("postgresql+psycopg2", keep_query=True)
        if override:
            return override
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg2",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_HOST,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            )
        )

    @property
    def db_connect_args(self) -> dict[str, object]:
        """`connect_args` de asyncpg. Vacío en local; poblado en producción."""
        args: dict[str, object] = {}
        if self.DB_SSL_MODE:
            args["ssl"] = self.DB_SSL_MODE
        if self.DB_DISABLE_PREPARED_STATEMENTS:
            # `statement_cache_size` es de asyncpg; `prepared_statement_cache_size`
            # es del dialecto de SQLAlchemy. Hay que apagar los dos: cada uno
            # cachea por su cuenta.
            args["statement_cache_size"] = 0
            args["prepared_statement_cache_size"] = 0
        return args


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
