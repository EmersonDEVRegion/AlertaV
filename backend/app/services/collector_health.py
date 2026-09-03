"""Salud de la recolección, expresada en las familias que dibuja el mapa.

Para qué existe
---------------
Para que un contador en cero deje de ser ambiguo.

Hasta ahora «Accidentes viales · 0» significaba dos cosas opuestas —no ocurrió
ninguno, o no nos está llegando ninguno— y se veían idénticas. El 2026-09-02 esa
ambigüedad costó un accidente concreto: el Actor de Instagram estuvo detenido dos
horas, `collector_runs` lo registró con su diagnóstico redactado cada cinco
minutos, y el mapa mostró el mismo cero de un día tranquilo. La persona se
enteró por Instagram.

Este módulo traduce lo que la base ya sabía a lo que la interfaz necesita.

Por qué agrega por familia y no por collector
---------------------------------------------
Porque el contador es por familia. Nadie mira «Accidentes viales» y se pregunta
por `transporte_informa`: se pregunta si puede confiar en el número. Una familia
la alimentan varias fuentes, así que su salud es la del conjunto — y la regla es
deliberadamente conservadora, ver `_peor`.

Por qué la frescura no basta y hace falta `degraded`
----------------------------------------------------
El collector de Instagram **corre cada cinco minutos sin fallar nunca**: lee el
dataset de la última corrida del Actor, que sigue ahí aunque el Actor lleve días
detenido. Su última corrida siempre es reciente. Cualquier chequeo basado sólo en
«¿cuándo corrió por última vez?» lo habría declarado sano justo durante las dos
horas en que estuvo ciego.

De ahí que `DEGRADED` exista y que este módulo lo mire primero: es el collector
declarando que lo que leyó no describe el presente, y eso ninguna cifra de
recencia lo puede deducir desde afuera.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.collectors.registry import COLLECTORS, collector_class
from app.models.enums import CollectorStatus
from app.models.event import CollectorRun

#: Qué familia del mapa alimenta cada collector, y con qué peso.
#:
#: No se deriva de `EventSource` porque no hay correspondencia: Instagram y la
#: prensa emiten lo que diga el texto —un choque, un incendio, un rescate— así
#: que alimentan tres familias a la vez.
#:
#: **`principal` vs `apoyo` decide si la caída de esa fuente vuelve
#: sospechoso el cero de la familia**, y no es una etiqueta decorativa: la
#: primera versión de este módulo trataba a todas por igual y por eso daba
#: `traffic: ok` el 2026-09-02, con Instagram ciego y Transporte Informa sano.
#: O sea, no habría atrapado el caso para el que se escribió.
#:
#: `transporte_informa` es apoyo porque **no sustituye a nadie en accidentes**:
#: emite sobre todo `road_closure`, que está fuera de `CORRELATABLE_EVENT_TYPES`
#: y no crea incidentes, y su rendimiento real en siniestros es cero. Que el MTT
#: publique con normalidad no significa que un choque se vaya a ver.
#:
#: FIRMS es apoyo por lo mismo en su terreno: emite `thermal_anomaly` con
#: confianza baja y no confirma nada. CONAF sí.
COLLECTOR_ROLES: dict[str, dict[str, str]] = {
    "conaf_incendios": {"fire": "principal"},
    "nasa_firms_area": {"fire": "apoyo"},
    "senapred_alertas": {"otros": "principal"},
    "chilquinta_cortes": {"power": "principal"},
    "cge_cortes": {"power": "principal"},
    "transporte_informa": {"traffic": "apoyo"},
    "instagram_apify": {"fire": "apoyo", "traffic": "principal", "otros": "apoyo"},
    "prensa_local": {"fire": "apoyo", "traffic": "principal", "otros": "apoyo"},
    #: Fuera de `COLLECTORS` —entra por webhook— pero escribe en `collector_runs`
    #: con este nombre y es el pilar de tres familias. Omitirlo dejaría
    #: precisamente la fuente cuya caída más importa fuera del cuadro.
    "bomberos_apify_webhook": {
        "fire": "principal",
        "traffic": "principal",
        "otros": "principal",
    },
    #: La segunda puerta de X. Se agregó tarde y ese olvido es en sí mismo el
    #: argumento del comentario de arriba: la ruta existía, escribía en
    #: `collector_runs` y aun así `/collectors/health` no la miraba, así que su
    #: caída no habría movido el estado de ninguna familia. Una fuente que no
    #: está en esta tabla es una fuente que puede morir en silencio.
    #:
    #: `principal` en `traffic` porque el MTT y la concesionaria son de las
    #: pocas fuentes que reportan un corte de vía antes que nadie; `apoyo` en
    #: las otras dos, donde sólo aporta si la prensa alcanza a publicar.
    "prensa_x_webhook": {
        "fire": "apoyo",
        "traffic": "principal",
        "otros": "apoyo",
    },
}

#: Vista plana, que es lo que la API expone por collector.
COLLECTOR_FAMILIES: dict[str, tuple[str, ...]] = {
    nombre: tuple(roles) for nombre, roles in COLLECTOR_ROLES.items()
}

#: Familias que el mapa cuenta. Espejo de `INCIDENT_LAYERS` en el frontend.
FAMILIES: tuple[str, ...] = ("fire", "traffic", "power", "otros")

#: Cuántas cadencias puede saltarse un collector antes de considerarlo detenido.
#:
#: Tres y no una: una corrida puede perderse por un reinicio del proceso o por
#: la dispersión que el runner introduce a propósito, y declarar una capa ciega
#: por eso sería el mismo canal que grita siempre del que este módulo intenta
#: escapar.
STALE_INTERVALS = 3

#: Piso para fuentes de cadencia larga. El MOP corre cada hora y se actualiza los
#: lunes: tres horas de silencio suyo no son noticia, pero doce sí.
MIN_STALE_SECONDS = 900


@dataclass(frozen=True, slots=True)
class CollectorHealth:
    collector: str
    families: tuple[str, ...]
    #: `ok` | `degraded` | `failing` | `stale` | `never`
    status: str
    last_run_at: datetime | None
    age_seconds: int | None
    expected_interval_seconds: int
    #: El mensaje de la corrida. Es lo que explica la ceguera en palabras.
    detail: str | None


def _intervalo(nombre: str) -> int:
    """Cadencia declarada del collector, o una hora si no está registrado.

    `bomberos_apify_webhook` no está en `COLLECTORS`: no lo dispara el runner,
    lo empuja Apify. Su cadencia es la del Actor y no la conocemos desde acá, así
    que se le da una hora — suficientemente laxa para no gritar por un rato
    tranquilo y suficientemente estricta para notar una integración caída.
    """
    if nombre not in COLLECTORS:
        return 3600
    return collector_class(nombre).poll_interval_seconds()


def _clasificar(run: CollectorRun | None, intervalo: int, *, ahora: datetime) -> str:
    if run is None:
        return "never"

    # El orden importa: `degraded` gana sobre cualquier cuenta de recencia,
    # porque es la fuente diciendo que no ve. Una corrida ciega es reciente por
    # definición —acaba de correr— y sin esta prioridad se leería como sana.
    if run.status == CollectorStatus.DEGRADED.value:
        return "degraded"
    if run.status == CollectorStatus.FAILED.value:
        return "failing"

    referencia = run.finished_at or run.started_at
    if referencia is None:
        return "never"
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=UTC)

    limite = max(intervalo * STALE_INTERVALS, MIN_STALE_SECONDS)
    if (ahora - referencia).total_seconds() > limite:
        return "stale"

    # `partial` cae acá deliberadamente: significa «rechacé filas», que en el
    # USGS ocurre en cada corrida y describe el filtro funcionando. Tratarlo como
    # problema pintaría media interfaz de amarillo permanente.
    return "ok"


#: Estados que significan «esto ANDABA y dejó de andar». Son los únicos que
#: vuelven sospechoso el cero de una familia.
#:
#: `never` queda deliberadamente fuera. Una fuente que nunca corrió es un hueco
#: de configuración, no una regresión, y hoy hay uno permanente:
#: `bomberos_apify_webhook` no entrega hasta que el Actor de X esté andando. Si
#: `never` encendiera el aviso, tres familias quedarían marcadas para siempre y
#: en dos semanas nadie miraría la marca — que es exactamente cómo `partial`
#: dejó de significar algo en este proyecto.
_REGRESIONES = ("degraded", "failing", "stale")

#: De peor a mejor dentro de las regresiones.
_GRAVEDAD = ("degraded", "failing", "stale")


def _estado_de_familia(estados: Sequence[str]) -> str:
    """El estado de una familia a partir del de sus fuentes PRINCIPALES.

    Tres reglas, en orden:

    1. **Si alguna principal se cayó, la familia hereda esa caída.** No hace
       falta que caigan todas: cada principal cubre algo que las otras no —CGE y
       Chilquinta son territorios distintos, Bomberos ve lo que ninguna red
       social ve— así que con una caída ya hay un hueco por el que un hecho real
       puede pasar sin aparecer.

       Esto sería insoportablemente ruidoso si la marca se mostrara siempre. No
       se muestra: el frontend sólo la pinta cuando ADEMÁS el contador está en
       cero. Esa condición es la que hace segura esta regla, porque el aviso
       aparece únicamente cuando hay un cero que interpretar.

    2. **Si ninguna se cayó y al menos una ve, la familia ve.**

    3. **Si ninguna corrió nunca, `never`.** Un despliegue recién parido.
    """
    regresiones = [e for e in estados if e in _REGRESIONES]
    if regresiones:
        return min(regresiones, key=_GRAVEDAD.index)
    if any(e == "ok" for e in estados):
        return "ok"
    return "never"


def build_health(
    ultimas: dict[str, CollectorRun], *, ahora: datetime | None = None
) -> tuple[list[CollectorHealth], dict[str, str]]:
    """Salud por collector y su agregado por familia.

    `ultimas` es `nombre → última corrida`. Se recibe ya resuelto en vez de
    consultarse acá para que toda la regla sea pura y testeable sin base: es
    aritmética de fechas y precedencias, no SQL.
    """
    momento = ahora or datetime.now(UTC)

    salud: list[CollectorHealth] = []
    for nombre, familias in sorted(COLLECTOR_FAMILIES.items()):
        run = ultimas.get(nombre)
        intervalo = _intervalo(nombre)
        estado = _clasificar(run, intervalo, ahora=momento)

        referencia = (run.finished_at or run.started_at) if run else None
        if referencia is not None and referencia.tzinfo is None:
            referencia = referencia.replace(tzinfo=UTC)

        salud.append(
            CollectorHealth(
                collector=nombre,
                families=familias,
                status=estado,
                last_run_at=referencia,
                age_seconds=(
                    int((momento - referencia).total_seconds())
                    if referencia is not None
                    else None
                ),
                expected_interval_seconds=intervalo,
                detail=(run.error or None) if run else None,
            )
        )

    por_familia = {
        familia: _estado_de_familia(
            [
                s.status
                for s in salud
                if COLLECTOR_ROLES.get(s.collector, {}).get(familia) == "principal"
            ]
        )
        for familia in FAMILIES
    }
    return salud, por_familia
