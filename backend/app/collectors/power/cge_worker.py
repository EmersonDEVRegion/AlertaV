"""CGE — cortes de suministro en las zonas de la V Región que atiende.

CGE cubre la periferia del área de Chilquinta: parte del litoral, el valle del
Aconcagua y sectores rurales. No se solapan mucho, así que las dos juntas son
la cobertura eléctrica de la región y no una redundancia.

Estado: **implementada, fuera del registro**
---------------------------------------------
Se desregistró porque `CGE_API_URL` apuntaba al visor y devolvía el HTML del
portal, y ese error repetido cada cinco minutos ensuciaba el log. La búsqueda de
la ruta XHR que este módulo recomendaba no encontró ninguna, y por una razón:
**CGE no tiene API**. Su visor se dibuja sobre un archivo de Google Earth,
`mapa_cge.kmz`, que la plataforma regenera cada pocos minutos y sirve como
estático.

Un KMZ es un ZIP que contiene un KML, y un KML es XML: los mismos cortes que
otra empresa entregaría como JSON, con dos envoltorios encima. Eso lo hace
perfectamente legible, sólo que por un camino distinto — y es lo que lee este
collector.

El camino está completo: `CGE_API_URL` apunta al archivo y hay tests propios en
`tests/test_cge_kmz.py` —descompresión en memoria, orden lon/lat del KML,
namespaces, HTML doblemente escapado, miles chilenos y el recorte por bounding
box—.

Y aun así **sigue fuera de `COLLECTORS`**, por decisión. Lo que ningún test
puede decir es si el archivo que CGE sirve hoy tiene la forma que esos tests
suponen: están construidos a mano contra la especificación de KML porque el
archivo real no se pudo descargar desde el entorno de verificación. Entra a la
rotación cuando alguien lo compruebe contra el archivo de verdad.

Mientras siga así, la periferia de la región no tiene capa de cortes.
Reactivarlo es descomentar su línea en `app/collectors/registry.py` e invertir
las dos aserciones que allí se nombran; la sección «Qué falla y cómo» de más
abajo describe qué mirar en la primera corrida real.

Qué cambia respecto de Chilquinta, y qué no
--------------------------------------------
Cambia **sólo la adquisición**. Este collector sobrescribe `load_records()`
—descargar bytes, descomprimir en memoria, parsear el XML— y hereda intacto todo
lo que viene después: el filtro de bounding box de la V Región, el conteo de
descartes, la traducción de errores a `CollectorError` y `normalize()` entero.
Un registro que sale de `load_records()` es indistinguible de uno de Chilquinta,
y por eso el resto del pipeline no supo nunca que el formato era otro.

Los ganchos `http_method` y `request_payload` no se usan acá: no hay filtros que
mandar, el archivo viene completo y el recorte lo hace este backend.

Por qué no se guarda el archivo en disco
-----------------------------------------
El KMZ se descomprime en memoria con `io.BytesIO`. No es una optimización: el
contenedor de producción tiene sistema de archivos efímero y se reinicia solo,
así que un fallo a mitad del parseo dejaría un temporal sin limpiar en un
proceso que nadie mira. El archivo pesa decenas de KB — el buffer cuesta menos
que el `try/finally` que haría falta para borrarlo.

Qué falla y cómo
----------------
Se distinguen tres cosas que desde los datos se ven parecidas:

* **El archivo llegó y no hay cortes.** Noche tranquila: `success` con cero
  eventos.
* **El archivo llegó y ningún Placemark trae coordenadas.** No tumba la corrida
  pero deja una degradación (`partial`): es la señal de que el formato cambió
  sin romperse, que es el fallo silencioso que más caro sale.
* **El archivo llegó y no se pudo leer** —no es un ZIP, no trae `.kml`, el XML
  está roto—. `failed`, con el diagnóstico de lo que llegó en el mensaje vía
  `describe_kmz`, para que la traza diga «llegó text/html de 4 KB» y no «no se
  pudo leer el KMZ».
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.collectors.geoservices import request_response
from app.collectors.power.base_worker import BasePowerOutageCollector
from app.collectors.power.kmz_parser import KmzFormatError, describe_kmz, parse_kmz
from app.core.config import settings
from app.core.exceptions import CollectorError
from app.models.enums import EventSource

logger = logging.getLogger(__name__)


class CgeCollector(BasePowerOutageCollector):
    """Cortes publicados por CGE en su KMZ de afectaciones."""

    name = "cge_cortes"
    source = EventSource.CGE
    company = "cge"
    url_setting = "CGE_API_URL"
    default_interval_seconds = 300

    @classmethod
    def poll_interval_seconds(cls) -> int:
        return settings.POWER_POLL_INTERVAL_SECONDS

    def request_headers(self) -> dict[str, str]:
        """Las del proyecto, más el `Accept` que corresponde a un binario.

        Declarar explícitamente que se espera un KMZ evita un modo de fallo
        conocido de estos visores: con un `Accept` genérico, varios CDN
        institucionales responden la página HTML del portal en vez del archivo.
        Ese HTML entraría acá como «no es un ZIP» —un fallo legible, pero
        perfectamente evitable desde la petición.
        """
        cabeceras = super().request_headers()
        cabeceras["Accept"] = (
            "application/vnd.google-earth.kmz,application/zip;q=0.9,*/*;q=0.1"
        )
        return cabeceras

    async def load_records(self) -> Sequence[Any]:
        """Descarga el KMZ, lo abre en memoria y devuelve un registro por corte.

        Se toma la respuesta **cruda** (`.content`) en vez de pasar por
        `request_json`: intentar decodificar como JSON un ZIP binario falla con
        un mensaje que habla de JSON, que es el diagnóstico equivocado y manda a
        buscar el problema donde no está.

        Lo que sí se conserva es `request_response`, el transporte del proyecto:
        reintentos ante 5xx y errores de red —frecuentes y transitorios en
        portales institucionales—, sin reintentos ante 4xx, y toda excepción de
        httpx ya convertida en `CollectorError`. Abrir un `client.get()` a pelo
        para poder leer bytes habría significado perder las tres cosas y dejar
        que una excepción de httpx escapara al orquestador.
        """
        try:
            async with self.http_client() as client:
                respuesta = await request_response(
                    client,
                    self.url,
                    # `{}` y no `None`: activa la guarda de `request_response`
                    # para que httpx no reemplace la query que traiga la URL.
                    {},
                    origin=self.company,
                    headers=self.request_headers(),
                )
                # `.content` — el cuerpo completo ya leído, sin decodificar. En
                # una respuesta no-streaming es lo mismo que `await .aread()`,
                # y no hay razón para hacer streaming de decenas de KB.
                crudo = respuesta.content
        except CollectorError:
            raise
        except Exception as exc:  # frontera con una fuente ajena
            raise CollectorError(
                f"{self.company}: fallo inesperado al descargar el KMZ: "
                f"{type(exc).__name__}: {exc}",
                detail={"url": self.url},
            ) from exc

        if not crudo:
            raise CollectorError(
                f"{self.company}: el KMZ llegó vacío (0 bytes).",
                detail={"url": self.url},
            )

        try:
            registros = parse_kmz(crudo)
        except KmzFormatError as exc:
            # El diagnóstico de lo que llegó va en el mensaje y no sólo en el
            # log: es lo que queda escrito en `collector_runs.error` y lo que
            # alguien va a leer dentro de tres semanas cuando CGE cambie el
            # archivo sin avisar.
            raise CollectorError(
                f"{self.company}: no se pudo leer el KMZ: {exc} [{describe_kmz(crudo)}]",
                detail={"url": self.url, "bytes": len(crudo)},
            ) from exc
        except Exception as exc:  # frontera con un archivo de un tercero
            raise CollectorError(
                f"{self.company}: fallo inesperado al procesar el KMZ: "
                f"{type(exc).__name__}: {exc} [{describe_kmz(crudo)}]",
                detail={"url": self.url, "bytes": len(crudo)},
            ) from exc

        logger.info(
            "KMZ de CGE procesado",
            extra={
                "collector": self.name,
                "bytes_descargados": len(crudo),
                "placemarks_con_coordenadas": len(registros),
            },
        )

        if not registros:
            # Ni un solo Placemark utilizable. Puede ser que no haya cortes —el
            # archivo trae sólo la leyenda— o que el esquema haya cambiado. Se
            # avisa sin tumbar la corrida: distinguirlas requiere el histórico, y
            # un `failed` cada cinco minutos por una noche tranquila sería ruido
            # que enseña a ignorar la traza.
            self.warn(
                "el KMZ no trajo ningún placemark con coordenadas: puede ser que "
                "no haya cortes activos, o que CGE haya cambiado el formato"
            )

        return registros


__all__ = ["CgeCollector"]
