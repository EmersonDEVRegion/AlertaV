# Reporte técnico — La regla de muerte súbita no descartaba nada

**Fecha:** 2026-08-20
**Componente:** `IncidentRepository.expire_uncorroborated_citizen` (anti-spam de reportes ciudadanos)
**Severidad:** alta — cualquiera podía dejar un punto falso en el mapa de forma indefinida con un solo POST.

---

## 1. El síntoma

Incidentes sostenidos únicamente por un reporte ciudadano se quedaban en el mapa con
`status = active`, `confidence = 40 %` y más de 18 minutos de antigüedad, cuando la regla los
tenía que descartar a los 5. El motor de correlación corría cada 120 s, sin excepciones y sin
warnings; `_expire` se ejecutaba en todas las pasadas y devolvía `incidents_dismissed = 0`.

Ese `0` es la parte importante: el `UPDATE` se ejecutaba, era sintácticamente correcto y no
encontraba ninguna fila. No había nada que un log pudiera contar.

## 2. Veredicto

**Ninguna de las tres hipótesis, tal como estaban planteadas. Era la segunda —precisión de
flotantes— pero por una causa distinta a la sospechada.**

No era que el motor produjera `0.400000001` por acumulación aritmética en Python. El motor
produce un `0.40` exacto: `score()` cierra con `round(confidence, 4)`. El error lo introduce
**PostgreSQL al guardar**, no Python al calcular.

`alertav.incidents.confidence` está declarada `REAL` en la migración `0002`:

```sql
confidence  REAL  NOT NULL DEFAULT 0.0,
```

`REAL` es float4: 24 bits de mantisa. El motor calcula, escribe y compara en float8. La
conversión de ida y vuelta no es la identidad:

| paso | valor |
|---|---|
| Python escribe (float8) | `0.4` |
| la columna `REAL` guarda (float4) | `0.4000000059604645` |
| la guarda compara (float4 → float8) | `0.4000000059604645 <= 0.4` → **false** |

La guarda `WHERE confidence <= 0.40` **no matcheaba con el mismo número que el motor acababa de
escribir**. El `UPDATE` afectaba 0 filas y devolvía 0. El ORM tampoco daba pistas: el modelo
declara `Float` —que en PostgreSQL es `double precision`— así que leyendo `app/models/incident.py`
la columna parece float8. La verdad estaba sólo en la migración.

El defecto es **simétrico**, y por eso no se limita al anti-spam:

| umbral | guardado en `REAL` | dirección del error |
|---|---|---|
| 0.40 | 0.4000000059604645 | ↑ — rompe `<= 0.40` |
| 0.30 | 0.30000001192092896 | ↑ — rompe `<= 0.30` |
| 0.60 | 0.6000000238418579 | ↑ — rompe `<= 0.60` |
| 0.35 | 0.3499999940395355 | ↓ — rompe `>= 0.35` |
| 0.45 | 0.44999998807907104 | ↓ — rompe `>= 0.45` |
| 0.25 / 0.50 | exactos | sin error |

Es decir: el filtro `min_confidence` de `GET /incidents` **ya estaba escondiendo** los incidentes
que valen exactamente 0.35 o 0.45, y lo mismo en `GET /events` sobre `raw_events.confidence`, que
también es `REAL`. Nadie lo había reportado porque el modo de fallo es "faltan algunos", no "todo
roto".

## 3. Las tres hipótesis, auditadas

### 3.1 Tipos de array — **descartada**

La columna física es `TEXT[]`:

```sql
sources  TEXT[]  NOT NULL DEFAULT '{}'::text[],
```

y el modelo la declara `ARRAY(Text)` del dialecto PostgreSQL, no del genérico. `contained_by()`
compila a `sources <@ $1` con el bind tipado como `text[]`, y el motor escribe el array con
`[source.value for source in scored.sources]` — cadenas planas, `'citizen'`, no miembros del Enum.
Los dos lados coinciden. El SQL compilado, verificado:

```sql
... AND alertav.incidents.sources <@ ARRAY['citizen'] ...
```

Sin casteo explícito y sin mismatch. La guarda estaba bien.

### 3.2 Precisión de flotantes — **culpable**, con la causa desplazada

Ver el punto 2. La sospecha apuntaba a la aritmética de Python; el error real está en el tipo
físico de la columna, un nivel más abajo. Vale la pena marcar la diferencia porque cambia la
solución: si el problema fuera aritmético, bastaría redondear al escribir. Como es de
almacenamiento, redondear al escribir no arregla nada — el `0.40` redondeado se sigue guardando
como `0.4000000059604645`.

### 3.3 Transacción / commit — **descartada**

`CorrelationEngine.run` hace un único `await self.session.commit()` después de `_expire`, dentro
de un `try` con `rollback` en el `except`. El orden es correcto y `_expire` es el último paso
antes del commit. Además, si el commit faltara, tampoco veríamos incidentes creándose ni
confianzas actualizándose, y eso sí funciona.

### 3.4 El resto de las guardas — verificadas de paso

- `status IN ('active','controlled')` — correcto; los incidentes atascados están `active`.
- `first_seen_at < now() - 5 min` — correcto, y `TIMESTAMPTZ` contra `datetime` aware.
- `is_official_confirmed IS false` — correcto.

Con la comparación de confianza arreglada, las cuatro guardas matchean.

## 4. La corrección

### 4.1 Inmediata — comparar con la tolerancia correcta (sin migración, sin downtime)

Nuevo módulo `app/repositories/confidence_filters.py`:

```python
def confidence_at_most(column, value):   return column <= value + CONFIDENCE_EPSILON
def confidence_at_least(column, value):  return column >= value - CONFIDENCE_EPSILON
```

con `CONFIDENCE_EPSILON = 1e-6` en `app/models/enums.py`, junto a los umbrales de la política.
El margen está dos órdenes de magnitud por encima del error de float4 en `[0,1]` (≈6e-8) y tres
por debajo de la resolución que la política tiene de verdad (`score()` redondea a 4 decimales).
No afloja la regla: sólo deja de exigirle a un float4 una exactitud que no tiene. Un incidente
legítimo de 0.41 sigue fuera del descarte.

Las funciones son deliberadamente *sargable* —no envuelven la columna en `ROUND()` ni la castean
a `numeric`—, así que cualquier índice sobre `confidence` sigue sirviendo.

Aplicado en los tres sitios que comparan confianza contra la base:

| archivo | comparación | efecto del bug |
|---|---|---|
| `incident_repository.py` · `expire_uncorroborated_citizen` | `<= max_confidence` | **el bug reportado** |
| `incident_repository.py` · `_apply_filters` | `>= min_confidence` | escondía incidentes de 0.35 / 0.45 |
| `event_repository.py` · `_apply_filters` | `>= min_confidence` | ídem sobre `raw_events` |

SQL resultante:

```sql
UPDATE alertav.incidents SET status='dismissed'
WHERE status IN ('active','controlled')
  AND first_seen_at < '2026-08-20 13:55:00+00:00'
  AND confidence <= 0.400001
  AND is_official_confirmed IS false
  AND sources <@ ARRAY['citizen']
```

### 4.2 De raíz — migración `0006_incident_confidence_float8`

`incidents.confidence` y `incidents.alert_confidence` pasan a `DOUBLE PRECISION`, que es lo que
el ORM ya declaraba y lo que el motor produce. Recrea `v_active_incidents`, porque PostgreSQL no
deja alterar el tipo de una columna de la que depende una vista.

**No toca `raw_events.confidence`**, a propósito: es la tabla que crece y `ALTER TYPE` la
reescribe entera tomando un `ACCESS EXCLUSIVE`. Hacerlo en el mismo despliegue que arregla una
regresión de producción es cambiar un problema por otro. Sus lecturas ya pasan por
`confidence_at_least`, que es correcto con cualquiera de los dos tipos. Queda para una ventana de
mantenimiento.

Los valores ya escritos no se recuperan con el `ALTER` (`0.4000000059604645` sigue siendo
`0.4000000059604645`, ahora en float8). No hace falta recalcularlos: la tolerancia los cubre y el
próximo `_refresh` de cada incidente los reescribe exactos.

Las dos capas conviven a propósito. La migración elimina la causa; la tolerancia es la red que
evita que el problema vuelva si alguien redeclara la columna.

## 5. Tests

Cuatro tests nuevos en `tests/test_antispam.py`, que fijan el fallo **como número y no como
cadena** — comparar contra el SQL literal no habría detectado nada, porque el SQL siempre estuvo
bien escrito:

- `test_la_caducidad_tolera_la_precision_de_la_columna` — reproduce el redondeo de float4 con
  `struct` y exige que el umbral del SQL alcance al valor almacenado.
- `test_la_tolerancia_no_afloja_la_regla` — la otra mitad: 0.41 tiene que sobrevivir al descarte.
- `test_el_umbral_siempre_alcanza_al_valor_que_se_escribio` — parametrizado sobre 0.25 … 0.60,
  cubre los umbrales que float4 redondea hacia arriba y los que redondea hacia abajo.

Resultado: **32 pasan** en `test_antispam.py`; **525 pasan** en la suite completa. Los 7 fallos
restantes (`test_gemini_extraction`, `test_seismic_endpoint`) son previos a este cambio —
verificado corriendo la suite contra `HEAD` limpio en un árbol aparte — y se deben a dependencias
opcionales ausentes en el entorno de verificación.

## 6. Despliegue

1. Desplegar el código (4.1). El anti-spam empieza a funcionar en la pasada siguiente, ~120 s,
   sin migración de por medio.
2. Los incidentes ya atascados se descartan solos en esa misma pasada: la guarda de edad usa
   `first_seen_at`, así que un reporte de hace 18 minutos entra al `UPDATE` de inmediato.
3. Correr `alembic upgrade head` para la `0006` cuando convenga. Es independiente del punto 1 y
   el código es correcto antes y después.

## 7. Pendientes anotados

- `raw_events.confidence` sigue en `REAL` (ver 4.2). Migrar en ventana de mantenimiento; hay que
  recrear `v_events_by_source_day`, que depende de la columna.
- `incident_events.link_confidence` también es `REAL`. Hoy no se compara contra ningún umbral, así
  que no hay bug — pero es la misma trampa esperando a la primera consulta que lo haga.
