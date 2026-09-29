"""Capa de accidentes viales.

Dos fuentes con perfiles deliberadamente distintos, más la prensa local:

===================  ====  =========================  =============================
Fuente               Peso  Qué aporta                 Qué le falta
===================  ====  =========================  =============================
Bomberos (webhook)   1.00  Certeza institucional      Coordenadas (se geocodifican)
Transporte Informa   0.80  Oficialidad y rapidez      Coordenadas (se geocodifican)
===================  ====  =========================  =============================

Los despachos de Bomberos no son un collector: entran por
`POST /api/v1/apify/webhook`. Este paquete conserva sus funciones libres
(`bomberos_10_4_worker`) y el extractor de calles con Gemini (`gemini`), que
comparten el webhook, el MTT y la prensa.

Waze salió del repositorio el 2026-09-23: el convenio de Waze for Cities nunca
se aprobó.

Todas emiten `type=accident`, que cae en la familia `traffic` y por lo tanto no
puede fundirse con incendios: ver el docstring de
`app/services/correlation/engine.py`.
"""

from app.collectors.traffic.transporteinforma_worker import TransporteInformaCollector

__all__ = ["TransporteInformaCollector"]
