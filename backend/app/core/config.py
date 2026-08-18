"""Configuración central de AlertaV.

Todo se lee de variables de entorno / .env vía pydantic-settings. No hay valores
secretos hardcodeados: `FIRMS_MAP_KEY` y `POSTGRES_PASSWORD` deben venir del
entorno.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Lista que se declara en el .env como CSV en vez de JSON.
#: `NoDecode` desactiva el parseo JSON automático de pydantic-settings para que
#: el validador `mode="before"` reciba el string crudo. Sin esto,
#: `CORS_ORIGINS=http://a,http://b` revienta con un JSONDecodeError.
CsvList = Annotated[list[str], NoDecode]


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

    # -- Base de datos -------------------------------------------------------
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "alertav"
    POSTGRES_PASSWORD: str = "alertav"
    POSTGRES_DB: str = "alertav"
    DB_SCHEMA: str = "alertav"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

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
    def database_url(self) -> str:
        """DSN async (asyncpg) para la aplicación."""
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
