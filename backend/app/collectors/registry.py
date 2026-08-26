"""Registro de collectors disponibles.

Añadir una fuente = implementar `BaseCollector` y registrarla acá. Nada más
cambia: el runner, la traza y el endpoint de disparo manual la recogen solos.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import BaseCollector
from app.collectors.conaf.collector import ConafCollector
from app.collectors.firms.collector import FirmsCollector
from app.collectors.power.cge_worker import CgeCollector
from app.collectors.power.chilquinta_worker import ChilquintaCollector
from app.collectors.seismic.sismologia_worker import SismologiaCollector
from app.collectors.senapred.collector import SenapredCollector
from app.collectors.social.instagram_apify_worker import InstagramApifyCollector
from app.collectors.traffic.bomberos_10_4_worker import Bomberos104Collector
from app.collectors.traffic.transporteinforma_worker import TransporteInformaCollector
from app.collectors.traffic.waze_worker import WazeCollector
from app.collectors.usgs.collector import UsgsCollector
from app.collectors.weather.openmeteo_worker import OpenMeteoCollector

CollectorFactory = Callable[[AsyncSession], BaseCollector]

COLLECTORS: dict[str, type[BaseCollector]] = {
    # -- Incendios y emergencias ---------------------------------------------
    FirmsCollector.name: FirmsCollector,
    ConafCollector.name: ConafCollector,
    SenapredCollector.name: SenapredCollector,
    # -- Sismos ---------------------------------------------------------------
    # Dos redes con umbrales distintos, no una redundante: el USGS ignora en la
    # práctica casi todo lo chileno bajo M4.5 y el CSN publica desde M2.5.
    # Ninguna entra al motor de correlación. Ver app/collectors/seismic/.
    UsgsCollector.name: UsgsCollector,
    SismologiaCollector.name: SismologiaCollector,
    # -- Cortes de suministro eléctrico ---------------------------------------
    # Familia `power`, aislada de incendios y accidentes. Un incendio derriba
    # tendido y provoca un corte: la coincidencia entre ambos es lo esperable, y
    # sin la partición el motor los leería como el mismo hecho.
    #
    # CGE: DE VUELTA EN ROTACIÓN.
    #
    # Se desregistró cuando su URL devolvía el HTML del visor: fallaba
    # correctamente, pero fallar correctamente cada cinco minutos llena
    # `collector_runs` y el log con un error ya conocido, y un log donde todo el
    # mundo aprende a ignorar lo rojo deja de servir para el error nuevo.
    #
    # Ese motivo concreto ya no aplica. La razón del HTML resultó ser que CGE
    # **no tiene API**: publica un KMZ estático (`mapa_cge.kmz`) que regenera
    # cada pocos minutos. `CGE_API_URL` apunta a ese archivo, `cge_worker` lo
    # descomprime en memoria y el camino tiene tests propios en
    # `tests/test_cge_kmz.py` —orden lon/lat del KML, namespaces, HTML doblemente
    # escapado, miles chilenos y el recorte por bounding box—.
    #
    # Lo que NO está verificado es que el archivo que CGE sirve hoy tenga la
    # forma que esos tests suponen: están construidos a mano contra la
    # especificación de KML porque el archivo real no se pudo descargar desde el
    # entorno de verificación. El primer despliegue es donde esto se puede
    # equivocar, y está preparado para ese caso — si el formato no encaja, la
    # corrida queda `failed` con el diagnóstico de lo que llegó (`describe_kmz`)
    # en vez de reportar cero cortes con estado `success`. Una fuente registrada
    # que falla a la vista se arregla; una apagada se olvida.
    #
    # Vigilar en la primera corrida: `collector_runs` de `cge_cortes`. El mensaje
    # dice si llegó HTML, si el ZIP no traía `.kml` o si el XML no se pudo
    # parsear. Si sale mal, desregistrar vuelve a ser una línea; mientras tanto
    # la periferia de la V Región —valle del Aconcagua, litoral y sectores
    # rurales— recupera la capa de cortes que sin CGE no tenía.
    ChilquintaCollector.name: ChilquintaCollector,
    CgeCollector.name: CgeCollector,
    # -- Accidentes viales ----------------------------------------------------
    # Los tres emiten `type=accident` y quedan aislados de la familia `fire` por
    # la partición del motor. Ninguno arranca sin su URL configurada: si falta,
    # el constructor lanza y el runner deja una corrida `failed` visible en
    # `collector_runs` en vez de fallar en silencio.
    WazeCollector.name: WazeCollector,
    Bomberos104Collector.name: Bomberos104Collector,
    TransporteInformaCollector.name: TransporteInformaCollector,
    # -- Meteorología ---------------------------------------------------------
    # La única capa que habla del futuro: emite `weather_observation`, que está
    # FUERA de `CORRELATABLE_EVENT_TYPES` y pesa 0 en `confidence.py`. No crea
    # incidentes ni mueve la confianza de ninguno; existe para superponerse a los
    # cortes de ruta y a los cortes eléctricos, que es donde una tarde de 8 mm/h
    # cambia la lectura de los avisos que llegan.
    #
    # Deliberadamente NO emite `flood`: ese tipo sí correlaciona y mapea a
    # `IncidentType.FLOOD`, así que un pronóstico produciría incidentes de
    # inundación en comunas donde no se ha inundado nada. Mismo criterio que
    # `thermal_anomaly` con los incendios. El flag de riesgo va en el payload.
    OpenMeteoCollector.name: OpenMeteoCollector,
    # -- Redes sociales -------------------------------------------------------
    # Cuentas hiperlocales de Instagram, leídas a través de Apify porque el WAF
    # de Meta bloquea cualquier intento directo. Emite `SOCIAL_MEDIA`, la banda
    # más baja del catálogo: es la fuente más rápida del sistema y la única
    # donde nadie verificó nada.
    #
    # Es también el único collector cuya fuente de datos **no la disparamos
    # nosotros**: el Actor corre según su propio Schedule en el panel de Apify y
    # acá sólo se lee el dataset resultante, que es gratis. Si el Schedule se
    # detiene, este collector no falla — avisa (`datos rancios`) y queda
    # `partial`. Ver `app/collectors/social/apify_client.py`.
    InstagramApifyCollector.name: InstagramApifyCollector,
    # Próximos hitos:
    #   BroadcastifyCollector.name: BroadcastifyCollector,  # STT → evento
}


def collector_class(name: str) -> type[BaseCollector]:
    """Clase de un collector sin instanciarla.

    El runner la necesita para conocer la cadencia y para poder registrar una
    corrida fallida cuando la propia construcción del collector revienta (por
    ejemplo, por configuración ausente).
    """
    try:
        return COLLECTORS[name]
    except KeyError as exc:
        raise KeyError(
            f"collector desconocido '{name}'. Disponibles: {sorted(COLLECTORS)}"
        ) from exc


def get_collector(name: str, session: AsyncSession) -> BaseCollector:
    try:
        return COLLECTORS[name](session)
    except KeyError as exc:
        raise KeyError(
            f"collector desconocido '{name}'. Disponibles: {sorted(COLLECTORS)}"
        ) from exc


def available_collectors() -> list[str]:
    return sorted(COLLECTORS)
