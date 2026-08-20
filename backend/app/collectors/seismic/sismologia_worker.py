"""Centro Sismológico Nacional — sismos de la red chilena.

Por qué hace falta si ya está el USGS
--------------------------------------
Porque el USGS no ve Chile. Su feed global filtra en M2.5 mundial, pero la
cobertura instrumental fuera de Estados Unidos hace que en la práctica ignore
casi todo lo chileno bajo M4.5. El catálogo del CSN del 19 de agosto de 2026
tiene 32 sismos; de esos, **dos** superan M4.0. Treinta eventos que ocurrieron en
Chile y que el sistema no veía.

El CSN es la red oficial del país —Universidad de Chile, certificada ISO 9001— y
publica desde M2.5 con estaciones propias. Para un sistema regional, la
diferencia no es de matiz: es la mayor parte del catálogo.

Qué es un sismo dentro de este sistema
---------------------------------------
Lo mismo que ya era con el USGS, y conviene repetirlo porque el volumen de datos
nuevo puede hacer olvidarlo: **un sismo es un hecho medido, no un siniestro**.

* `type = earthquake` **no está** en `CORRELATABLE_EVENT_TYPES`, y eso no cambia
  con esta fuente. El motor de correlación resuelve incertidumbre por
  corroboración, y un sismo no tiene ninguna que resolver. Peor: con radio de
  1500 m y ventana de 4 h, DBSCAN fundiría el sismo principal con sus réplicas
  en un solo "incidente" y borraría la secuencia, que es justo el dato
  sismológico relevante. Con el CSN el riesgo es mayor, no menor, porque ahora
  entran los enjambres completos.
* Su `confidence` es 1.0 y significa "este sismo ocurrió". La regla de
  `confidence.py` le da peso **0** sobre cualquier fenómeno, igual que al USGS:
  un epicentro a 100 km de profundidad no es la ubicación de una emergencia.

Sobre la "familia sísmica"
--------------------------
Los sismos no pasan por `INCIDENT_FAMILY` porque no llegan a ser incidentes:
viajan por `raw_events` + `seismic_details` y salen por los endpoints
`/seismic`, que es de donde el mapa toma magnitud y profundidad para dibujar el
círculo. Su aislamiento del resto es más fuerte que el de una familia —están
fuera del motor por completo— y añadirles una familia daría a entender que
participan de la correlación.

Idempotencia y revisiones
-------------------------
El CSN asigna a cada evento un identificador estable, visible en la URL de su
informe (`/sismicidad/informes/2026/08/379889.html`). Con
`external_id = csn:379889`, releer el catálogo cada cinco minutos **actualiza**
la fila en vez de duplicar el sismo. Importa más de lo que parece: el CSN revisa
magnitud y profundidad horas después del evento, y esa segunda lectura debe
corregir la fila original, no crear un sismo fantasma al lado.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import request_text
from app.collectors.seismic.csn_parser import (
    CsnQuake,
    page_looks_broken,
    parse_catalog,
    recent_catalog_paths,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.repositories.seismic_repository import SeismicRepository
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: El sismo ocurrió: lo midió la red oficial chilena. No dice nada sobre que haya
#: un siniestro en ese punto — eso lo fija la regla de `confidence.py`, que le da
#: peso 0 sobre el fenómeno.
CSN_CONFIDENCE = 1.0

#: Identificador de la red en `seismic_details.provider`.
CSN_PROVIDER = "csn"

#: Clave namespaced en `raw_data` donde `normalize()` deja los campos tipados
#: para que `after_ingest()` los persista sin volver a parsear el HTML. Mismo
#: patrón que el collector del USGS.
SEISMIC_KEY = "_seismic"


def build_external_id(quake: CsnQuake) -> str:
    """ID estable. El namespace evita chocar con los ids del USGS."""
    return f"csn:{quake.csn_id}"


def build_text(quake: CsnQuake) -> str:
    """Descripción legible en español. La página no entrega uno armado."""
    if quake.magnitude is None:
        cabeza = "Sismo de magnitud no determinada"
    else:
        escala = f" {quake.mag_type}" if quake.mag_type else ""
        cabeza = f"Sismo de magnitud {quake.magnitude:.1f}{escala}"

    partes = [cabeza]
    if quake.place:
        partes.append(quake.place)
    if quake.depth_km is not None:
        partes.append(f"profundidad {quake.depth_km:.0f} km")
    return " — ".join(partes) + " · CSN"


def seismic_row(quake: CsnQuake) -> dict[str, Any]:
    """Campos de `seismic_details`, serializables a JSON.

    Este diccionario viaja dentro de `raw_data` (JSONB) hasta `after_ingest()`,
    así que no puede contener `datetime` ni tipos no serializables.

    Varias columnas quedan en `None` y es correcto: el CSN no publica reportes de
    percepción, nivel PAGER ni significancia — esos son campos del USGS. Ponerles
    un valor inventado sería peor que dejarlos vacíos.
    """
    return {
        "provider": CSN_PROVIDER,
        "usgs_id": quake.csn_id,
        "magnitude": quake.magnitude,
        "mag_type": quake.mag_type,
        "depth_km": quake.depth_km,
        "place": quake.place,
        "felt_reports": None,
        "tsunami": False,
        "pager_alert": None,
        "significance": None,
        # El CSN publica soluciones ya revisadas por analistas humanos; no
        # distingue estados como hace el USGS con automatic/reviewed.
        "review_status": "reviewed",
        "usgs_url": quake.report_url,
        "source_updated_at": None,
    }


class SismologiaCollector(BaseCollector):
    """Lector del catálogo diario de sismos del CSN."""

    name = "csn_sismos"
    source = EventSource.CSN
    default_interval_seconds = 300

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.CSN_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.base_url = settings.CSN_BASE_URL.strip().rstrip("/")
        if not self.base_url:
            raise CollectorError("CSN_BASE_URL no está configurada")
        self.days = settings.CSN_CATALOG_DAYS
        self.bbox = settings.csn_bbox
        self.min_magnitude = settings.CSN_MIN_MAGNITUDE

    def run_params(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "days": self.days,
            "min_magnitude": self.min_magnitude,
            "bbox": self.bbox.as_firms_param(),
        }

    async def fetch(self) -> Sequence[CsnQuake]:
        """Lee los catálogos de los últimos días. Falla de una sola forma.

        Cada día es una página independiente. Que falle una **no** aborta la
        corrida: se avisa y se sigue con las demás, porque perder el catálogo de
        ayer no justifica perder también el de hoy. Sólo si no se pudo leer
        ninguna se levanta `CollectorError`, y ahí la corrida queda `failed` en
        `collector_runs` — nunca `success` con cero sismos, que sería
        indistinguible de un día sin actividad.
        """
        rutas = recent_catalog_paths(datetime.now(UTC), days=self.days)
        sismos: list[CsnQuake] = []
        vistos: set[str] = set()
        fallos: list[str] = []

        async with httpx.AsyncClient(
            timeout=settings.CSN_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
        ) as client:
            for ruta in rutas:
                url = f"{self.base_url}/{ruta}"
                try:
                    html = await request_text(client, url, origin="csn")
                except CollectorError as exc:
                    fallos.append(f"{ruta}: {exc}")
                    continue
                except Exception as exc:  # frontera con una fuente ajena
                    fallos.append(f"{ruta}: {type(exc).__name__}: {exc}")
                    continue

                try:
                    rota, motivo = page_looks_broken(html)
                    delta = parse_catalog(html, base_url=self.base_url)
                except Exception as exc:
                    fallos.append(f"{ruta}: HTML ilegible ({type(exc).__name__}: {exc})")
                    continue

                if rota:
                    self.warn(f"la estructura del catálogo cambió ({ruta}): {motivo}")

                for sismo in delta:
                    # Los catálogos de dos días consecutivos se solapan en la
                    # frontera horaria: el mismo sismo aparece en ambos si su
                    # fecha local y su fecha UTC caen en días distintos.
                    if sismo.csn_id not in vistos:
                        vistos.add(sismo.csn_id)
                        sismos.append(sismo)

        if fallos and not sismos:
            raise CollectorError(
                f"csn: no se pudo leer ningún catálogo ({'; '.join(fallos)[:400]})",
                detail={"rutas": rutas},
            )
        for fallo in fallos:
            self.warn(f"catálogo no leído — {fallo}")

        return sismos

    def normalize(self, records: Sequence[CsnQuake]) -> list[EventCreate]:
        """Sismos → señales del dominio. Función pura."""
        events: list[EventCreate] = []
        fuera_de_caja = 0
        bajo_umbral = 0

        for quake in records:
            if not self.bbox.contains(quake.lat, quake.lon):
                fuera_de_caja += 1
                continue
            if quake.magnitude is not None and quake.magnitude < self.min_magnitude:
                bajo_umbral += 1
                continue

            raw_data: dict[str, Any] = {
                "_collector": self.name,
                # La magnitud y la profundidad se dejan también acá, planas, y no
                # sólo en `_seismic`: el frontend calcula el radio del círculo
                # con ambas, y obligarlo a bajar un nivel dentro de una clave con
                # guion bajo sería exponer nuestra estructura interna como
                # contrato.
                "magnitude": quake.magnitude,
                "depth_km": quake.depth_km,
                "mag_type": quake.mag_type,
                "place": quake.place,
                "report_url": quake.report_url,
                # De qué columna salió la hora. Si algún día aparece un desfase
                # sistemático, esto dice si el culpable fue el CSN o nuestra
                # conversión de zona horaria.
                "time_source": quake.time_source,
                SEISMIC_KEY: seismic_row(quake),
            }

            try:
                events.append(
                    EventCreate(
                        timestamp=quake.time,
                        source=EventSource.CSN,
                        type=EventType.EARTHQUAKE,
                        lat=quake.lat,
                        lon=quake.lon,
                        text=build_text(quake),
                        external_id=build_external_id(quake),
                        confidence=CSN_CONFIDENCE,
                        raw_data=raw_data,
                    )
                )
            except Exception as exc:
                # Una fila mala no puede tumbar el lote. El caso realista es un
                # sismo con la hora en el futuro por unos segundos de desfase de
                # reloj entre el CSN y este servidor.
                logger.debug(
                    "sismo del CSN descartado en validación",
                    extra={"error": str(exc), "csn_id": quake.csn_id},
                )

        if fuera_de_caja:
            logger.info(
                "sismos del CSN fuera del recorte espacial",
                extra={"collector": self.name, "descartados": fuera_de_caja},
            )
        if bajo_umbral:
            logger.info(
                "sismos del CSN bajo el umbral de magnitud",
                extra={"collector": self.name, "descartados": bajo_umbral},
            )
        return events

    async def after_ingest(self, events: Sequence[EventCreate]) -> None:
        """Cuelga el detalle sismológico de las señales recién escritas.

        Mismo patrón que el collector del USGS: va después de la ingesta porque
        `seismic_details` referencia `raw_events.id`, que la fila no tiene hasta
        estar en la base. Los ids se recuperan por `external_id`.
        """
        # Bucle explícito y no comprensión: el `isinstance` dentro de un `if` de
        # comprensión no estrecha el tipo del elemento, y mypy —con razón— no
        # puede probar que el payload sea un dict al desempaquetarlo más abajo.
        pending: list[tuple[str, dict[str, Any]]] = []
        for event in events:
            payload = event.raw_data.get(SEISMIC_KEY)
            if event.external_id and isinstance(payload, dict):
                pending.append((event.external_id, payload))

        if not pending:
            return

        ids = await self.service.repo.ids_by_external_id(
            EventSource.CSN, [external_id for external_id, _ in pending]
        )

        rows: list[dict[str, Any]] = []
        huerfanos = 0
        for external_id, payload in pending:
            raw_event_id = ids.get(external_id)
            if raw_event_id is None:
                huerfanos += 1
                continue
            rows.append({"raw_event_id": raw_event_id, **payload})

        if huerfanos:
            self.warn(
                f"{huerfanos} sismos quedaron sin detalle: su señal no se encontró "
                f"tras la ingesta"
            )

        if rows:
            await SeismicRepository(self.session).upsert_many(rows)
            await self.session.commit()


__all__ = [
    "CSN_CONFIDENCE",
    "CSN_PROVIDER",
    "SEISMIC_KEY",
    "SismologiaCollector",
    "build_external_id",
    "build_text",
    "seismic_row",
]
