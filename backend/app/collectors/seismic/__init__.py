"""Capa sísmica: dos redes con umbrales distintos.

============  ===================  ===============================================
Red           Umbral efectivo      Qué aporta
============  ===================  ===============================================
CSN (Chile)   ~M2.5 en Chile       El catálogo real del país: enjambres,
                                   microsismicidad, precursores.
USGS (global) ~M4.5 en Chile       Contexto mundial y soluciones revisadas
                                   (tensor de momento, PAGER, reportes de
                                   percepción) que el CSN no publica en su tabla.
============  ===================  ===============================================

Se conservan las dos porque miden cosas distintas del mismo fenómeno, y el mismo
sismo puede aparecer dos veces —una por red— con magnitudes que difieren. Eso no
es un duplicado: son dos mediciones independientes, y `seismic_details.provider`
dice cuál es cuál.

Ninguna de las dos entra al motor de correlación: `earthquake` está fuera de
`CORRELATABLE_EVENT_TYPES` a propósito. Ver el docstring de `sismologia_worker`.
"""

from app.collectors.seismic.sismologia_worker import SismologiaCollector

__all__ = ["SismologiaCollector"]
