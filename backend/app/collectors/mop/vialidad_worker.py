"""MOP / Dirección de Vialidad — emergencias de infraestructura vial.

Qué publica esta fuente, y qué NO
----------------------------------
Rutas dañadas: socavaciones, derrumbes, puentes con paso restringido, árboles
caídos, esteros crecidos que cubren la calzada. **No publica siniestros.** No
hay un solo choque en este servicio, y esa distinción decide todo lo demás de
este módulo.

    https://rest-sit.mop.gob.cl/arcgis/rest/services/VIALIDAD/Emergencias_Vialidad/MapServer/0

Es un ArcGIS MapServer abierto, sin credencial. Tres capas: la **0** trae todas
las emergencias vigentes como punto —las lineales llevadas a punto de forma
referencial por el propio MOP—, la 1 sólo las puntuales y la 2 las lineales.
Este collector lee la 0: para superponer en un mapa, un punto por emergencia es
lo que se quiere, y dejar que el MOP haga esa reducción es preferible a
inventarse un centroide acá.

Por qué es capa de CONTEXTO y no un disparador
-----------------------------------------------
El propio servicio declara su cadencia: se actualiza **los lunes alrededor de
las 15:00**, y a diario sólo mientras dure un evento de emergencia. No es
tiempo real y no pretende serlo.

De ahí las tres decisiones que definen su lugar en el sistema:

* **Emite `road_closure`**, nunca `accident`. Ese tipo está fuera de
  `CORRELATABLE_EVENT_TYPES` y fuera de `EVENT_TO_INCIDENT_TYPE`: no genera
  incidentes, no mueve la confianza de ninguno y no se agrupa con nada. La
  migración 0008 explica el daño concreto que eso evita —un accidente cuya
  confianza sube porque hay una faena a tres cuadras—, y acá el riesgo es
  idéntico.
* **Confianza 0.0.** Que una ruta rural lleve tres semanas socavada no es
  evidencia de que esté ocurriendo algo ahora. Mismo criterio que
  `EventSource.WEATHER`.
* **Cadencia horaria, no de cinco minutos.** Consultar cada 5 min un archivo que
  cambia una vez por semana son 2 016 peticiones para 1 dato nuevo. La cadencia
  es un acto de cortesía con un servidor público, además de una cuenta.

Su valor está en la superposición: cuando alguien reporta un accidente en la
cuesta y esta capa muestra que la ruta lleva días «Parcialmente Operativa» por
un derrumbe, la lectura del reporte cambia. Es el mismo papel que juega la capa
de lluvia.

El filtro regional va en el WHERE, y `REGION` no es lo que parece
------------------------------------------------------------------
`REGION` es un **código numérico en texto**: Valparaíso es `'05'`, no
«Valparaíso». Filtrar por nombre —el primer intento, el obvio— devuelve
`features: []`, un vacío que se lee como «no hay emergencias» y es en realidad
la consulta mal escrita. Es exactamente el fallo silencioso que este proyecto
persigue, y por eso el código lo declara en `REGION_CODE` con el comentario
puesto, y hay un test que lo fija.

El filtro se manda al servidor, no se aplica acá: son 30 registros de la V
Región frente a más de mil del país. Bajarse el país para descartarlo en Python
sería gastar ancho de banda ajeno para hacer peor el mismo trabajo.

Qué falla y cómo
----------------
* **Consulta válida, cero emergencias.** `success` con cero eventos. Es un
  estado normal: significa que no hay rutas dañadas vigentes en la región.
* **Filas sin coordenada o sin `CORRELATIVO`.** Se descartan contadas, y la
  corrida queda `partial` con el número en el aviso.
* **Cuerpo vacío.** Casi siempre es haber pedido `f=geojson`, que este servidor
  no soporta y responde sin cuerpo en vez de con un error. `parse_features` lo
  dice con esas palabras.
* **Error de ArcGIS dentro de un HTTP 200.** Lo detecta `detect_service_error` y
  la corrida queda `failed` citando el mensaje del servidor.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import request_json
from app.collectors.mop.vialidad_parser import (
    RoadEmergency,
    build_text,
    parse_emergency,
    parse_features,
    severity_rank,
    summarise,
)
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Código de la Región de Valparaíso en el campo `REGION`. **No es el nombre.**
#: Ver el encabezado del módulo: filtrar por «Valparaíso» devuelve cero filas sin
#: error, que es la peor forma posible de equivocarse acá.
REGION_CODE = "05"

#: Confianza de la capa. Cero, y es una declaración: esta fuente informa, no
#: aporta evidencia sobre un siniestro en curso.
MOP_CONFIDENCE = 0.0

#: Campos que se piden explícitamente en vez de `outFields=*`.
#:
#: El motivo no es el ancho de banda sino el ruido: la capa trae 40 columnas,
#: entre ellas `created_user`, `last_edited_date`, dos `GlobalID` duplicados y un
#: `IMAGEN` con nombres de archivo internos del MOP. Todo eso terminaría en
#: `raw_data` y de ahí en el histórico, sin que nadie lo vaya a leer nunca.
OUT_FIELDS: tuple[str, ...] = (
    "CORRELATIVO",
    "ESTADO",
    "TRANSITO",
    "NIVEL_DE_GRAVEDAD",
    "RESTRICCION",
    "NOMBRE_CAMINO",
    "ROL",
    "CAMINO",
    "RESUMEN",
    "DESCRIPCION_DETALLADA",
    "FECHA_EMERGENCIA",
    "FECHA_INGRESO",
    "KM_INICIO_SEGMENTO",
    "KM_FIN_SEGMENTO",
    "ELEMENTO",
    "EVENTO",
    "COMPETENCIA",
    "REGION",
)

#: Tope declarado por el servicio (`maxRecordCount`). No se usa para paginar
#: —este servidor ignora `resultOffset` y `resultRecordCount`, comprobado— sino
#: para **detectar** que nos estamos acercando al techo. Si alguna vez llegan
#: exactamente 1000 filas, es casi seguro que hay más y no las estamos viendo.
MAX_RECORD_COUNT = 1000


class MopVialidadCollector(BaseCollector):
    """Emergencias viales vigentes de la Dirección de Vialidad."""

    name = "mop_vialidad"
    source = EventSource.MOP
    default_interval_seconds = 3600

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.MOP_VIALIDAD_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        url = settings.MOP_VIALIDAD_URL.strip()
        if not url:
            raise CollectorError(
                "MOP_VIALIDAD_URL no está configurada. Debe apuntar a la capa 0 "
                "del MapServer de Emergencias_Vialidad."
            )
        self.url = url.rstrip("/")
        if not self.url.endswith("/query"):
            self.url = f"{self.url}/query"

    def run_params(self) -> dict[str, Any]:
        return {"region": REGION_CODE, "url": self.url}

    def query_params(self) -> dict[str, Any]:
        """Parámetros de la consulta a ArcGIS.

        `outSR=4326` no es decorativo. La capa está almacenada en SIRGAS-Chile
        (`wkid` 5360) y, aunque sus coordenadas ya vienen en grados decimales y
        se parecen mucho a WGS84, no son idénticas: pedir la reproyección
        explícita mueve la latitud en el sexto decimal, y es el servidor quien
        sabe hacer esa transformación. Asumir que 5360 «es lo mismo que» 4326
        funciona hasta que alguien mide.
        """
        return {
            "where": f"REGION='{REGION_CODE}'",
            "outFields": ",".join(OUT_FIELDS),
            "outSR": 4326,
            "returnGeometry": "true",
            "f": "json",
        }

    async def fetch(self) -> Sequence[RoadEmergency]:
        async with httpx.AsyncClient(
            timeout=settings.MOP_VIALIDAD_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            payload = await request_json(client, self.url, self.query_params(), origin=self.name)

        crudas = parse_features(payload, origin=self.name)

        if len(crudas) >= MAX_RECORD_COUNT:
            # Este servicio no pagina, así que no hay forma de pedir «la
            # siguiente página»: lo único honesto es avisar de que la respuesta
            # está probablemente truncada en vez de reportar un número redondo
            # como si fuera el total.
            self.warn(
                f"llegaron {len(crudas)} filas y el tope del servicio es "
                f"{MAX_RECORD_COUNT}: la respuesta puede venir truncada y este "
                f"servicio no admite paginación"
            )

        emergencias = [emergencia for fila in crudas if (emergencia := parse_emergency(fila))]

        descartadas = len(crudas) - len(emergencias)
        if descartadas:
            self.warn(f"{descartadas} emergencias sin coordenada o sin CORRELATIVO")

        if emergencias:
            logger.info(
                "emergencias viales vigentes",
                extra={"collector": self.name, "por_transito": summarise(emergencias)},
            )
        return emergencias

    def normalize(self, records: Sequence[RoadEmergency]) -> list[EventCreate]:
        """Una señal de contexto por emergencia vigente.

        No hay filtro de antigüedad, y es deliberado: en la muestra verificada
        las emergencias arrastran semanas o meses desde `FECHA_EMERGENCIA`, y
        eso no las hace obsoletas —el servicio publica sólo las **vigentes**, así
        que aparecer en la respuesta *es* la señal de que la ruta sigue dañada—.
        Aplicarle acá el descarte por antigüedad que usan Waze o el MTT vaciaría
        la capa entera.

        El corolario es que la desaparición de una fila significa «reparada», el
        mismo modelo que los cortes eléctricos.
        """
        eventos: list[EventCreate] = []
        ahora = datetime.now(UTC)

        for emergencia in records:
            eventos.append(
                EventCreate(
                    # `FECHA_EMERGENCIA` es cuándo empezó, no cuándo se supo. Si
                    # falta, la hora de la corrida es lo único cierto que hay.
                    timestamp=emergencia.ocurrida_en or ahora,
                    source=EventSource.MOP,
                    type=EventType.ROAD_CLOSURE,
                    lat=emergencia.lat,
                    lon=emergencia.lon,
                    text=build_text(emergencia),
                    external_id=f"mop:{emergencia.correlativo}",
                    confidence=MOP_CONFIDENCE,
                    raw_data={
                        **dict(emergencia.raw),
                        "_collector": self.name,
                        "_mop": {
                            "transito": emergencia.transito,
                            "transitable": emergencia.es_transitable,
                            "gravedad": emergencia.gravedad,
                            "restriccion": emergencia.restriccion,
                            "rol": emergencia.rol,
                            "estado": emergencia.estado,
                            # Un solo número para que la UI ordene sin
                            # reimplementar las dos escalas del MOP.
                            "severidad": severity_rank(emergencia),
                        },
                    },
                )
            )

        return eventos


__all__ = ["MOP_CONFIDENCE", "OUT_FIELDS", "REGION_CODE", "MopVialidadCollector"]
