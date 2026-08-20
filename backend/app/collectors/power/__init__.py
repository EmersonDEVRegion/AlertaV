"""Capa de cortes de suministro eléctrico.

Dos distribuidoras que se reparten la región sin solaparse demasiado:
Chilquinta cubre el Gran Valparaíso, CGE la periferia y el valle del Aconcagua.
Juntas son la cobertura eléctrica de la V Región.

Ambas emiten `type=power_outage`, que cae en la familia `power` y por lo tanto
**no puede fundirse con incendios ni con accidentes**. Esa separación importa
más de lo que parece en esta capa: un incendio derriba tendido y provoca un
corte, así que la coincidencia espaciotemporal entre ambos es lo *esperable* —y
sin la partición por familia el motor los leería como el mismo hecho.

La relación causal entre un incendio y un corte es información valiosa, pero es
una lectura que corresponde a un operador mirando dos capas, no a un motor
fusionando dos puntos.
"""

from app.collectors.power.cge_worker import CgeCollector
from app.collectors.power.chilquinta_worker import ChilquintaCollector

__all__ = ["CgeCollector", "ChilquintaCollector"]
