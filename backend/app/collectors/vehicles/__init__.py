"""Vehículos robados, recuperados y abandonados publicados por GBV SpA.

Una fuente que NO es de emergencias. Vive en un feed paralelo
(`GET /feed/vehiculos`), fuera del mapa y del motor de correlación: emite
`vehicle_report`, que no está en `CORRELATABLE_EVENT_TYPES`. Ver `gbv_worker`.
"""
