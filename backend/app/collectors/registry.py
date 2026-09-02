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
from app.collectors.mop.vialidad_worker import MopVialidadCollector
from app.collectors.news.local_news_worker import LocalNewsCollector
from app.collectors.power.cge_worker import CgeCollector
from app.collectors.power.chilquinta_worker import ChilquintaCollector
from app.collectors.seismic.sismologia_worker import SismologiaCollector
from app.collectors.senapred.collector import SenapredCollector
from app.collectors.social.instagram_apify_worker import InstagramApifyCollector
from app.collectors.traffic.transporteinforma_worker import TransporteInformaCollector
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
    # Los dos emiten `type=accident` y quedan aislados de la familia `fire` por
    # la partición del motor. Ninguno arranca sin su URL configurada: si falta,
    # el constructor lanza y el runner deja una corrida `failed` visible en
    # `collector_runs` en vez de fallar en silencio.
    #
    # WAZE: FUERA DE ROTACIÓN, a la espera del convenio.
    #
    # `WazeCollector` está implementado y con tests (`tests/test_traffic_workers.py`),
    # pero `WAZE_FEED_URL` sólo la entrega Waze for Cities y la solicitud no fue
    # aprobada. Registrado con la variable vacía, su constructor lanzaba
    # `CollectorError` en CADA corrida: una fila `failed` y una traza cada cinco
    # minutos por una causa ya conocida y sin acción posible.
    #
    # Esa es exactamente la diferencia con CGE. Un collector registrado que falla
    # se mantiene a la vista porque el fallo es información —el formato cambió,
    # el archivo se movió, algo hay que mirar—. Acá no hay nada que mirar: falta
    # una credencial que depende de un trámite externo. Un error repetido que
    # nadie puede accionar entrena al equipo a ignorar el rojo del log, y esa
    # costumbre es la que después se traga el error nuevo de otra fuente.
    #
    # Reactivarlo son dos líneas —el import de arriba y la entrada de acá— más
    # `WAZE_FEED_URL` en el entorno. El módulo no necesita ningún cambio: fue
    # escrito contra el esquema del feed CCP, que es el que llega con el convenio.
    # Ver el encabezado de `app/collectors/traffic/waze_worker.py`.
    #
    # BOMBEROS: FUERA DE ROTACIÓN — cambió de puerta, no de estado.
    #
    # Es el tercer módulo de esta sección fuera del CRON y el único que NO está
    # esperando nada: los despachos **entran igual**, por
    # `POST /api/v1/apify/webhook`, y con menos latencia que antes. Lo que se
    # apagó es la forma de traerlos, no la fuente.
    #
    # `Bomberos104Collector` leía la cuenta de la central a través de un puente
    # RSSHub, y ese puente está muerto sin reemplazo: la ruta de Twitter de
    # RSSHub desapareció con la API de X y el espejo de xcancel tampoco
    # responde. Registrarlo hoy sería pedirle a cada corrida que fallara contra
    # un 404 — el mismo ruido inaccionable que sacó a Waze de acá.
    #
    # Ojo con la diferencia de mecánica, que es lo que hay que entender para
    # operar esta capa: los demás collectors **preguntan** cada N minutos; el
    # webhook **espera** a que Apify avise. Un despacho perdido acá no se
    # recupera en la corrida siguiente, porque no hay corrida siguiente. Por eso
    # el endpoint escribe en `collector_runs` igual que un collector: es el
    # único lugar donde se ve si el webhook está llegando.
    #
    # Su decodificación —claves, resumen canónico, construcción del evento— no
    # se movió: vive en las funciones libres de
    # `app/collectors/traffic/bomberos_10_4_worker.py` y la usan las dos puertas.
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
    # -- Infraestructura vial dañada ------------------------------------------
    # Dirección de Vialidad (MOP): socavaciones, derrumbes, puentes con paso
    # restringido. NO son siniestros, y la diferencia no es semántica.
    #
    # Emite `road_closure`, que igual que `weather_observation` está FUERA de
    # `CORRELATABLE_EVENT_TYPES`, y entra con confianza 0.0. No crea incidentes
    # ni mueve la confianza de ninguno. La migración 0009 explica el daño que
    # eso evita: estas emergencias siguen vigentes durante SEMANAS, así que una
    # que aportara peso a la familia `traffic` le regalaría corroboración a cada
    # choque ocurrido en esa cuesta durante todo ese tiempo.
    #
    # Cadencia horaria y no de cinco minutos: el propio servicio declara que se
    # actualiza los lunes ~15:00, y a diario sólo durante eventos de emergencia.
    MopVialidadCollector.name: MopVialidadCollector,
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
    # -- Prensa local ---------------------------------------------------------
    # Sitio del Suceso y Pura Noticia, raspados de forma nativa y sin
    # intermediario: son portales abiertos, uno con RSS estándar y el otro con
    # HTML público. Cero costo por corrida, a diferencia de la capa de Instagram.
    #
    # Emite `MEDIA` (0.60), una banda entera por encima de `SOCIAL_MEDIA` (0.35),
    # y la diferencia no es de simpatía: estos dos tienen firma, editor y
    # rectificaciones. Lo que NO tienen es a alguien en el lugar, así que no son
    # `confirming` y una sola nota no lleva un incidente a certeza.
    #
    # Los dos portales entran como señales INDEPENDIENTES entre sí —el
    # `external_id` lleva el slug del medio— para que dos coberturas del mismo
    # choque se corroboren en el motor en vez de colapsar en una sola fila.
    #
    # Es la fuente más lenta de la capa de siniestros y eso está asumido: llega
    # después que el MTT y después que las cuentas hiperlocales. Su valor no es
    # la velocidad, es que alguien la escribió después de llamar por teléfono.
    #
    # Vigilar en la primera corrida: `collector_runs` de `prensa_local`. Un
    # portal caído deja la corrida `partial` con el nombre del medio en el aviso;
    # sólo si caen los dos queda `failed`.
    LocalNewsCollector.name: LocalNewsCollector,
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
