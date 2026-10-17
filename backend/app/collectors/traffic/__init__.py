"""Capa de accidentes viales.

Dos fuentes con perfiles deliberadamente distintos, más la prensa local. Las
dos llegan por el webhook de Apify (X):

===================  ====  =========================  =============================
Fuente               Peso  Qué aporta                 Qué le falta
===================  ====  =========================  =============================
Bomberos (webhook)   1.00  Certeza institucional      Coordenadas (se geocodifican)
@TTIValparaiso (MTT) 0.80  Oficialidad y rapidez      Coordenadas (se geocodifican)
===================  ====  =========================  =============================

Los despachos de Bomberos no son un collector: entran por
`POST /api/v1/apify/webhook`. Este paquete conserva sus funciones libres
(`bomberos_10_4_worker`) y el extractor de calles con Gemini (`gemini`), que
comparten el webhook, el MTT y la prensa.

Todas emiten `type=accident`, que cae en la familia `traffic` y por lo tanto no
puede fundirse con incendios: ver el docstring de
`app/services/correlation/engine.py`.
"""
