"""Configuración central de AlertaV.

Todo se lee de variables de entorno / .env vía pydantic-settings. No hay valores
secretos hardcodeados: `FIRMS_MAP_KEY` y `POSTGRES_PASSWORD` deben venir del
entorno.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, PostgresDsn, computed_field, field_validator
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
    DEBUG: bool = True
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
        "geojson|https://earthquake.usgs.gov/earthquakes/feed/v1.0/"
        "summary/2.5_day.geojson"
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

    # -- Accidentes viales: Waze ---------------------------------------------
    # Feed del Waze for Cities / CCP. Es un endpoint privado que Waze entrega a
    # cada municipio o gobierno con convenio: NO hay una URL pública que sirva
    # para todos, y por eso no hay valor por defecto. Vacío = collector apagado.
    WAZE_FEED_URL: str = ""
    #: Tipos de alerta a conservar. El feed trae ACCIDENT, JAM, ROAD_CLOSED,
    #: WEATHERHAZARD, HAZARD y POLICE; sólo el primero es un siniestro.
    WAZE_ALERT_TYPES: CsvList = Field(default_factory=lambda: ["ACCIDENT"])
    WAZE_TIMEOUT_SECONDS: float = 30.0
    WAZE_POLL_INTERVAL_SECONDS: int = 300  # 5 min
    #: Antigüedad máxima de un reporte. Waze mantiene alertas vivas mientras los
    #: conductores las confirmen; una de hace seis horas ya no describe el
    #: presente del tránsito.
    WAZE_MAX_AGE_MINUTES: int = Field(default=120, ge=5, le=1440)

    # -- Accidentes viales: despachos de Bomberos ----------------------------
    #: Feed RSS de la central. Se lee a través de un puente tipo RSSHub porque la
    #: central publica en una red social y no expone API.
    #:
    #: `rsshub.app` es una instancia pública, compartida y sin SLA: devuelve 429
    #: y 503 con frecuencia. Sirve para calibrar, pero para operar conviene
    #: apuntar a una instancia propia (`docker run diygod/rsshub`) y cambiar sólo
    #: esta variable. Vacío = collector apagado.
    BOMBEROS_DISPATCH_URL: str = "https://rsshub.app/twitter/user/CentralCBV"
    #: Claves radiales que denotan rescate vehicular. Se comparan por prefijo tras
    #: normalizar, de modo que `10-4` también captura `10-0-4` y `10-4-1`. Ver
    #: `bomberos_10_4_worker.matches_key`.
    BOMBEROS_ACCIDENT_KEYS: CsvList = Field(default_factory=lambda: ["10-4"])
    BOMBEROS_TIMEOUT_SECONDS: float = 30.0
    BOMBEROS_POLL_INTERVAL_SECONDS: int = 180  # 3 min

    # -- Accidentes viales: Transporte Informa -------------------------------
    #: Portal del MTT para la Región de Valparaíso. Es HTML (WordPress con
    #: Elementor), no una API: se raspa. Vacío = apagado.
    TRANSPORTE_INFORMA_URL: str = "https://www.transporteinforma.cl/valparaiso/"
    TRANSPORTE_INFORMA_TIMEOUT_SECONDS: float = 30.0
    TRANSPORTE_INFORMA_POLL_INTERVAL_SECONDS: int = 600  # 10 min
    #: Tope de geocodificaciones por corrida. A 1 s por llamada (ver
    #: NOMINATIM_MIN_INTERVAL_SECONDS), 20 avisos son 20 segundos de corrida.
    #: Sin tope, un día de temporal con 300 avisos dejaría al worker cinco
    #: minutos colgado del rate limit ajeno.
    TRANSPORTE_INFORMA_MAX_GEOCODES: int = Field(default=20, ge=1, le=200)

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
    #: peticiones anónimas se rechazan. Cambiar el correo por el del operador.
    NOMINATIM_USER_AGENT: str = "AlertaV/0.1 (contacto: alertav@example.cl)"
    #: Sesgo de la búsqueda hacia Chile. Evita que "Avenida Argentina" resuelva
    #: en Buenos Aires, que es exactamente lo que hace Nominatim sin esto.
    NOMINATIM_COUNTRY_CODES: str = "cl"

    # -- Motor de correlación -------------------------------------------------
    # Todos estos valores son hipótesis de partida, no constantes físicas. Se
    # calibran contra la ventana de recolección con `/events/{id}/neighbours`.
    #: Radio de agrupación espacial, en metros reales.
    CORRELATION_RADIUS_M: float = Field(default=1500.0, ge=100.0, le=20_000.0)
    #: Ventana hacia atrás de señales que el motor considera en cada pasada.
    CORRELATION_WINDOW_HOURS: int = Field(default=4, ge=1, le=168)
    #: Antigüedad máxima de un incidente para que una señal nueva se le adhiera.
    #: Mayor que la ventana de agrupación: un incendio de CONAF vive días y sus
    #: señales de corroboración siguen llegando mucho después del primer racimo.
    CORRELATION_MATCH_WINDOW_HOURS: int = Field(default=12, ge=1, le=336)
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

    # -- Reportes ciudadanos: anti-spam --------------------------------------
    #: Segundos entre reportes de una misma IP. 0 desactiva el límite, que es
    #: lo que quieren los tests y una demo local.
    CITIZEN_REPORT_MIN_INTERVAL_SECONDS: int = Field(default=600, ge=0, le=86_400)
    #: Minutos que sobrevive un incidente sostenido SÓLO por reportes ciudadanos
    #: sin corroborar. Pasado ese plazo se descarta y desaparece del mapa.
    #: Ver `IncidentRepository.expire_uncorroborated_citizen`.
    CITIZEN_UNCORROBORATED_TTL_MINUTES: int = Field(default=5, ge=1, le=1440)
    #: Confianza por debajo o igual a la cual un incidente se considera "sin
    #: corroborar" para el descarte temprano. Coincide con
    #: `CITIZEN_INITIAL_CONFIDENCE`: en cuanto otra fuente aporta, la suma lo
    #: sube por encima y el incidente sale solo de esta regla.
    CITIZEN_UNCORROBORATED_MAX_CONFIDENCE: float = Field(
        default=0.40, ge=0.0, le=1.0
    )

    # -- Ingesta -------------------------------------------------------------
    INGEST_MAX_BATCH_SIZE: int = 1000
    # Tolerancia para eventos con timestamp futuro (desfase de reloj de fuentes)
    INGEST_FUTURE_TOLERANCE_SECONDS: int = 300

    @field_validator(
        "CORS_ORIGINS",
        "FIRMS_SOURCES",
        "CONAF_STATES",
        "CONAF_REGIONS",
        "SENAPRED_REGIONS",
        "USGS_EVENT_TYPES",
        "WAZE_ALERT_TYPES",
        "BOMBEROS_ACCIDENT_KEYS",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Permite definir listas como CSV en el .env."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

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
        return urlunsplit(
            (scheme, parts.netloc, parts.path, parts.query if keep_query else "", "")
        )

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
