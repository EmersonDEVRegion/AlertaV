"""Capa de accidentes viales.

Tres fuentes con perfiles deliberadamente distintos, que es lo que hace que
correlacionarlas valga la pena:

===================  ====  =========================  =========================
Fuente               Peso  Qué aporta                 Qué le falta
===================  ====  =========================  =========================
Bomberos (10-4)      1.00  Certeza institucional      Coordenadas (texto libre)
Transporte Informa   0.80  Oficialidad y rapidez      Coordenadas (se geocodifican)
Waze                 0.40  Punto exacto y volumen     Verificación de nadie
===================  ====  =========================  =========================

La lectura de esa tabla es el diseño entero de la capa: Waze sabe *dónde* pero no
*si*; Bomberos sabe *si* pero no *dónde*. El motor de correlación existe
justamente para juntar esas dos mitades — y sólo puede hacerlo con las señales
que tienen geometría, así que hoy la unión efectiva ocurre entre Waze y el MTT
geocodificado. Los despachos de Bomberos quedan registrados y consultables, a la
espera de un emparejamiento por texto en el Paso B.

Todas emiten `type=accident`, que cae en la familia `traffic` y por lo tanto no
puede fundirse con incendios: ver el docstring de
`app/services/correlation/engine.py`.
"""

from app.collectors.traffic.bomberos_10_4_worker import Bomberos104Collector
from app.collectors.traffic.transporteinforma_worker import TransporteInformaCollector
from app.collectors.traffic.waze_worker import WazeCollector

__all__ = [
    "Bomberos104Collector",
    "TransporteInformaCollector",
    "WazeCollector",
]
