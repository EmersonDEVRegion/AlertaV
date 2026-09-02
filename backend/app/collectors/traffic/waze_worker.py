"""Waze CCP — reportes de accidentes de la comunidad de conductores.

Estado: **implementado, fuera del registro, a la espera del feed CCP**
----------------------------------------------------------------------
Este collector está completo y probado, y **no corre**. `WAZE_FEED_URL` es el
endpoint que Waze entrega a los socios del programa *Waze for Cities* (antes
Connected Citizens Program), y la solicitud de AlertaV no fue aprobada. Sin esa
URL el constructor lanza `CollectorError`, así que mientras estuvo registrado
dejó una corrida `failed` y una traza **cada cinco minutos** por una causa ya
conocida y que nadie podía accionar desde el código.

Está fuera de `COLLECTORS` por eso y no por desconfianza en el módulo. La
distinción importa y es la misma que se aplicó a CGE en su momento: una fuente
registrada que falla se deja a la vista porque su fallo es información —cambió
el formato, se movió el archivo, hay algo que mirar—. Acá no hay nada que
mirar; falta una credencial que depende de un trámite. Un error repetido e
inaccionable enseña a ignorar el rojo del log, y esa costumbre es la que después
se traga el error de verdad de otra fuente.

Por qué no se raspa el mapa en vivo
------------------------------------
Existe la ruta obvia: el visor de `waze.com/live-map` consulta un endpoint
interno que devuelve las mismas alertas, y basta con imitar las cabeceras de un
navegador para que deje de responder 403. **Se decidió no hacerlo**, y conviene
dejar escrito por qué, porque este repositorio raspa CSN, Chilquinta, CGE y el
MTT sin problema y la contradicción es sólo aparente.

Esas cuatro fuentes publican sus datos abiertamente para que cualquiera los vea
en su propio visor: no hay control de acceso que sortear, y leer el mismo
archivo que lee su página web es usar un dato público por otro camino. Waze es
el caso contrario. Hubo una solicitud formal, hubo una negativa, y el 403 del
endpoint interno **es esa negativa aplicada técnicamente**. Falsear
`User-Agent` y `Referer` para esquivarlo no es raspar un dato público: es
eludir un control de acceso para obtener lo que el titular ya denegó, contra
sus términos de servicio.

A lo que se suma lo práctico: sería la dependencia más frágil del sistema
—cae cuando Waze ajuste su WAF, probablemente durante una emergencia, que es
cuando su tráfico sube— y la más cara de explicar en la primera conversación
con un municipio o con el GORE. Para una plataforma pública de emergencias que
va a pedir convenios, eso no es un detalle.

Cómo se reactiva
----------------
Nada en este archivo cambia. Fue escrito contra el esquema del feed CCP —el que
se documenta más abajo—, que es exactamente lo que llega con el convenio:

1. Obtener el feed vía Waze for Cities, con patrocinio de un organismo público
   (GORE Valparaíso, SENAPRED o un municipio). El programa está dirigido a
   entidades públicas y una postulación a título particular no prospera.
2. Poner la URL en `WAZE_FEED_URL`.
3. Restaurar en `app/collectors/registry.py` el import y la entrada
   `WazeCollector.name: WazeCollector` bajo «Accidentes viales».

Mientras tanto, la capa de siniestros viales se sostiene sobre Transporte
Informa (MTT, 0.80) y la clave 10-4 de Bomberos (1.00). Lo que falta con Waze
fuera es volumen y coordenada exacta en origen, no certeza: era la fuente menos
verificada de las tres.

Qué es un reporte de Waze dentro de este sistema
------------------------------------------------
Un botón pulsado por alguien que iba manejando. Eso es todo, y conviene tenerlo
presente porque el volumen engaña: Waze entrega cientos de alertas donde CONAF
entrega tres, y esa asimetría puede hacer parecer que es la fuente más rica del
sistema cuando es la menos verificada.

Tres propiedades que lo distinguen de las fuentes institucionales:

* **Georreferenciado en origen.** A diferencia del MTT, el punto lo puso el GPS
  del teléfono. La coordenada es de las mejores que entra al sistema; lo dudoso
  es la interpretación del hecho, no su ubicación.
* **Contemporáneo.** El reporte nace en el segundo en que ocurre. No hay latencia
  administrativa.
* **Sin verificar.** Nadie fue. El campo `reliability` mide cuántos conductores
  confirmaron el ícono, no si hubo un accidente — un atasco por obras acumula
  confirmaciones igual que un choque. Por eso su regla en `confidence.py` colapsa
  la banda a 0.40 fijo, igual que FIRMS y por el mismo motivo.

Esquema del feed (Waze for Cities / CCP, formato verificado 2026-08)::

    alerts[].uuid          identificador estable del reporte
    alerts[].type          ACCIDENT | JAM | ROAD_CLOSED | WEATHERHAZARD | HAZARD | POLICE
    alerts[].subtype       ACCIDENT_MAJOR | ACCIDENT_MINOR | ...
    alerts[].location      {"x": lon, "y": lat}   ← ojo: x es LONGITUD
    alerts[].street        "Ruta 68"
    alerts[].city          "Valparaíso, Valparaíso"
    alerts[].reliability   0–10
    alerts[].confidence    0–10
    alerts[].reportRating  0–6
    alerts[].pubMillis     epoch ms UTC

El detalle de `location` es el que más veces se implementa mal: Waze usa `x`/`y`
en el orden cartográfico (x = longitud, y = latitud), y quien asuma que `x` es la
primera coordenada "como en lat/lon" deposita todos los accidentes de Valparaíso
en el Índico. Se lee explícitamente por nombre y se valida el rango.

Idempotencia
------------
`uuid` es estable mientras la alerta viva, así que `external_id = waze:<uuid>`
hace que releer el feed cada 5 minutos **actualice** la fila en vez de duplicar
el accidente. Cuando Waze la retira, simplemente deja de aparecer.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.collectors.geoservices import as_float, request_json
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource, EventType
from app.schemas.event import EventCreate

logger = logging.getLogger(__name__)

#: Confianza con la que entra cada señal. Coincide con SOURCE_BASE_CONFIDENCE y
#: con el peso pedido para esta capa. `confidence.py` colapsa la banda a 0.40
#: fijo, así que este número no modula nada — se declara igual para que la fila
#: en `raw_events` sea legible sin consultar la política.
WAZE_CONFIDENCE = 0.40

#: Subtipos que describen la gravedad. Sólo se usan para el texto legible: no
#: alteran la confianza, porque quien clasifica "MAJOR" es el mismo conductor sin
#: verificar que reportó el hecho.
_SUBTYPE_LABELS: dict[str, str] = {
    "ACCIDENT_MAJOR": "Accidente grave",
    "ACCIDENT_MINOR": "Accidente menor",
}


@dataclass(frozen=True, slots=True)
class WazeAlert:
    """Una alerta del feed, ya desenvuelta y con los tipos resueltos.

    Existe por la misma razón que `SeismicRecord` en el collector del USGS: que
    `normalize()` sea una función pura sobre datos planos y se pueda testear el
    mapeo con una respuesta real guardada, sin tocar la red.
    """

    uuid: str
    alert_type: str
    subtype: str | None
    lat: float
    lon: float
    street: str | None
    city: str | None
    reliability: int | None
    reported_at: datetime | None
    raw: Mapping[str, Any]


def parse_alert(payload: Any) -> WazeAlert | None:
    """Convierte un elemento de `alerts` en `WazeAlert`. None si es inservible.

    Descarta en silencio y devuelve None en vez de lanzar: un feed comunitario
    trae filas incompletas de forma rutinaria, y perder la corrida entera por una
    alerta sin coordenadas sería cambiar un dato faltante por doscientos.
    """
    if not isinstance(payload, Mapping):
        return None

    uuid = str(payload.get("uuid") or "").strip()
    alert_type = str(payload.get("type") or "").strip().upper()
    if not uuid or not alert_type:
        return None

    location = payload.get("location")
    if not isinstance(location, Mapping):
        return None

    # x = longitud, y = latitud. Ver el docstring del módulo.
    lon = as_float(location.get("x"))
    lat = as_float(location.get("y"))
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    return WazeAlert(
        uuid=uuid,
        alert_type=alert_type,
        subtype=(str(payload.get("subtype")).strip().upper() or None)
        if payload.get("subtype")
        else None,
        lat=lat,
        lon=lon,
        street=(str(payload.get("street")).strip() or None)
        if payload.get("street")
        else None,
        city=(str(payload.get("city")).strip() or None) if payload.get("city") else None,
        reliability=_as_int(payload.get("reliability")),
        reported_at=_from_millis(payload.get("pubMillis")),
        raw=dict(payload),
    )


def _as_int(value: Any) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def _from_millis(value: Any) -> datetime | None:
    """Epoch en milisegundos → datetime UTC.

    Waze publica en milisegundos; interpretarlos como segundos situaría todos los
    reportes en 1970 y el filtro de antigüedad los descartaría a todos sin que
    nadie entienda por qué.
    """
    millis = as_float(value)
    if millis is None or millis <= 0:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000.0, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def parse_commune(city: str | None) -> str | None:
    """Extrae la comuna del campo `city` de Waze.

    Waze publica `"Valparaíso, Valparaíso"` —comuna y región separadas por coma—
    y a veces sólo la comuna. Se toma el primer segmento. Guardar la cadena
    entera haría que el emparejamiento con las alertas de SENAPRED del Paso B
    fallara: ese paso compara nombres de comuna normalizados, y
    "valparaiso, valparaiso" no es ninguno.
    """
    if not city:
        return None
    first = city.split(",")[0].strip()
    return first or None


def build_text(alert: WazeAlert) -> str:
    """Descripción legible. El feed no trae una."""
    head = _SUBTYPE_LABELS.get(alert.subtype or "", "Accidente de tránsito")
    where = " — ".join(part for part in (alert.street, alert.city) if part)
    tail = f" ({alert.reliability}/10 de fiabilidad)" if alert.reliability is not None else ""
    return f"{head}{': ' + where if where else ''}{tail} · reporte de Waze"


class WazeCollector(BaseCollector):
    """Consumidor del feed JSON de Waze CCP."""

    name = "waze_accidentes"
    source = EventSource.WAZE
    default_interval_seconds = 300

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.WAZE_POLL_INTERVAL_SECONDS

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        if not settings.WAZE_FEED_URL.strip():
            # Falla en la construcción, no en silencio. `run_collector` del runner
            # atrapa esto y escribe una fila `failed` en `collector_runs`: un
            # collector mal configurado tiene que ser visible, no invisible.
            #
            # Con el collector fuera de `COLLECTORS` esto ya no se dispara solo.
            # Si aparece, alguien lo volvió a registrar o lo disparó a mano, y el
            # mensaje tiene que decir qué falta y de quién depende — no repetir
            # el nombre de la variable, que ya está en la traza.
            raise CollectorError(
                "WAZE_FEED_URL no está configurada, y no hay URL pública que "
                "poner: el feed CCP lo entrega Waze for Cities al firmar el "
                "convenio, y la solicitud de AlertaV no fue aprobada. Este "
                "collector está fuera de COLLECTORS por eso; si llegaste acá es "
                "que se lo registró de nuevo o se lo disparó a mano. Ver el "
                "encabezado de este módulo."
            )
        self.feed_url = settings.WAZE_FEED_URL.strip()
        self.wanted_types = {
            kind.strip().upper() for kind in settings.WAZE_ALERT_TYPES if kind.strip()
        }

    def run_params(self) -> dict[str, Any]:
        return {
            "types": sorted(self.wanted_types),
            "max_age_minutes": settings.WAZE_MAX_AGE_MINUTES,
        }

    async def fetch(self) -> Sequence[WazeAlert]:
        async with httpx.AsyncClient(
            timeout=settings.WAZE_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            payload = await request_json(
                client, self.feed_url, {}, origin="waze"
            )

        if not isinstance(payload, Mapping):
            raise CollectorError(
                f"waze: se esperaba un objeto JSON con la clave 'alerts', "
                f"llegó {type(payload).__name__}"
            )

        alerts = payload.get("alerts")
        if alerts is None:
            # No es un error: el feed responde sin `alerts` cuando no hay nada
            # activo en la zona. Se avisa igual porque también es lo que se vería
            # si cambiara el esquema.
            self.warn("el feed no trae la clave 'alerts'; se asume que está vacío")
            return []
        if not isinstance(alerts, list):
            raise CollectorError(
                f"waze: 'alerts' debería ser una lista, llegó {type(alerts).__name__}"
            )

        parsed = [parsed_alert for item in alerts if (parsed_alert := parse_alert(item))]
        discarded = len(alerts) - len(parsed)
        if discarded:
            self.warn(f"{discarded} alertas del feed sin uuid, tipo o coordenadas")
        return parsed

    def normalize(self, records: Sequence[WazeAlert]) -> list[EventCreate]:
        """Filtra a accidentes vigentes y los convierte en señales del dominio."""
        now = datetime.now(UTC)
        max_age = settings.WAZE_MAX_AGE_MINUTES * 60
        events: list[EventCreate] = []
        stale = 0

        for alert in records:
            if alert.alert_type not in self.wanted_types:
                continue

            timestamp = alert.reported_at or now
            if alert.reported_at is None:
                self.warn("alertas sin pubMillis; se usó la hora de la corrida")
            elif (now - timestamp).total_seconds() > max_age:
                # Waze mantiene vivas las alertas mientras se las confirme. Una
                # de hace horas ya no describe el tránsito de ahora, y meterla al
                # motor la haría corroborar un accidente que probablemente ya se
                # despejó.
                stale += 1
                continue

            events.append(
                EventCreate(
                    timestamp=timestamp,
                    source=EventSource.WAZE,
                    type=EventType.ACCIDENT,
                    lat=alert.lat,
                    lon=alert.lon,
                    text=build_text(alert),
                    external_id=f"waze:{alert.uuid}",
                    confidence=WAZE_CONFIDENCE,
                    raw_data={
                        **dict(alert.raw),
                        # `EventCreate` no tiene campo `commune`: la comuna se
                        # deja bajo la clave que `extract_commune` ya conoce
                        # (`COMMUNE_ALIASES` en services/correlation/communes.py).
                        # Es el mismo camino que usan CONAF y SENAPRED.
                        "comuna": parse_commune(alert.city),
                        "_collector": self.name,
                        "_waze": {
                            "type": alert.alert_type,
                            "subtype": alert.subtype,
                            "reliability": alert.reliability,
                            "street": alert.street,
                            "city_raw": alert.city,
                        },
                    },
                )
            )

        if stale:
            logger.info(
                "alertas de Waze descartadas por antigüedad",
                extra={"collector": self.name, "stale": stale},
            )
        return events


__all__ = [
    "WAZE_CONFIDENCE",
    "WazeAlert",
    "WazeCollector",
    "build_text",
    "parse_alert",
    "parse_commune",
]
