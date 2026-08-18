# context.md

## Proyecto

Nombre provisional: **AlertaV** (alternativas: FOCO, Alerta 5, EmergenciaV, FuegoV).

Objetivo: desarrollar una plataforma ciudadana para visualizar, detectar, correlacionar y reportar emergencias, inicialmente enfocada en **incendios forestales y estructurales de la Región de Valparaíso**, especialmente Valparaíso, Viña del Mar, Concón, Quilpué y Villa Alemana.

La plataforma debería poder evolucionar posteriormente a accidentes, rescates, inundaciones, derrumbes, evacuaciones y otras emergencias.

## Concepto central

No depender de una sola fuente. El sistema debe hacer **fusión/correlación de fuentes** para convertir señales independientes en un único incidente.

Ejemplo:

1. Ciudadano reporta humo.
2. Broadcastify registra una comunicación/despacho de Bomberos.
3. NASA FIRMS detecta una anomalía térmica cercana.
4. CONAF registra un incendio forestal.
5. SENAPRED publica una alerta.

El sistema correlaciona los eventos por tiempo, ubicación, tipo y fuente y genera un incidente con nivel de confianza.

Ejemplo:

```json
{
  "id": "INC-2026-00142",
  "type": "wildfire",
  "status": "active",
  "latitude": -33.025,
  "longitude": -71.52,
  "confidence": 0.96
}
```

## Fuentes investigadas

### Broadcastify

Feed relevante: **25506**, relacionado con fuego y EMS de Valparaíso, con frecuencias de despacho de sistemas digitales y analógicos de la Región de Valparaíso, incluyendo DMR de Viña del Mar y Valparaíso.

https://www.broadcastify.com/listen/feed/25506

Broadcastify dispone de:
- Live Audio Feed Catalog API.
- Broadcastify Calls Developer APIs.

La API de catálogo requiere licencia/aprobación y tiene restricciones para nuevas licencias de aplicaciones móviles. Se debe contactar a Broadcastify antes de usarla en producción.

Broadcastify Calls permite trabajar con llamadas individuales de sistemas de radio convencionales/trunked, incluyendo datos históricos y potencialmente en tiempo real.

https://www.broadcastify.com/calls/dev/

Estrategia recomendada: **no retransmitir audio** sin autorización. Usarlo como fuente de detección, transcribirlo y generar eventos estructurados.

Pipeline:

```text
Broadcastify
    ↓
audio/calls
    ↓
Speech-to-Text
    ↓
clasificación
    ↓
extracción de ubicación/tipo/unidades
    ↓
evento estructurado
```

Palabras/frases útiles:
- incendio
- fuego
- humo
- alarma
- primera/segunda/tercera alarma
- incendio estructural
- incendio forestal
- se despacha
- material mayor
- carro
- unidades
- evacuación

### NASA FIRMS

Fuente con API oficial y documentación clara.

https://firms.modaps.eosdis.nasa.gov/api/

Endpoint de área:

https://firms.modaps.eosdis.nasa.gov/api/area/

Ofrece:
- API
- MAP_KEY gratuito
- CSV/TXT
- SHP
- KML
- WMS
- WFS

Sensores relevantes:
- MODIS
- VIIRS NOAA-20
- VIIRS NOAA-21
- VIIRS Suomi-NPP

Campos de interés:
- latitude
- longitude
- acq_date
- acq_time
- satellite
- instrument
- confidence
- brightness
- frp

FIRMS detecta anomalías térmicas y **no equivale automáticamente a un incendio confirmado**.

Uso recomendado: consultar el área de la Región de Valparaíso, almacenar detecciones y correlacionarlas con otras fuentes.

### CONAF

Sistema de Información Territorial (SIT):

https://sit.conaf.cl/

Utiliza infraestructura MapServer/PostgreSQL/PostGIS y expone información geográfica pública.

Formatos/recursos identificados:
- Shapefile
- KMZ/KML
- información cartográfica
- Excel
- servicios de mapas

Información relacionada:
- situación actual de incendios
- alertas
- incendios forestales
- pronóstico de riesgo
- Botón Rojo

No se identificó una API REST pública y documentada específica de incendios. La siguiente investigación debe determinar las capas y servicios actuales:
- MapServer
- WMS
- WFS
- GeoJSON
- capas de incendios activos
- capas de alertas

Para backend, WFS/GeoJSON sería preferible a WMS.

### SENAPRED

Fuentes:
- sitio institucional
- Visor Chile Preparado
- alertas oficiales
- información geográfica
- datos abiertos

Visor:

https://www.senapred.cl/visor-chile-preparado/

Portal de datos:

https://datos.gob.cl/

No se identificó una API pública documentada y estable específicamente destinada a alertas en tiempo real.

No depender de endpoints internos del visor sin verificar autorización y estabilidad.

Uso:
- alertas preventivas/rojas
- evacuaciones
- contexto de emergencia
- capas geográficas

### Bomberos de Chile

SIG oficial:

https://sig.bomberos.cl/

Permite consultar:
- Cuerpos de Bomberos
- Compañías
- Grifos

Sirve para construir una base geográfica propia.

No se identificó una API pública documentada de:
- despachos
- emergencias en tiempo real
- unidades enviadas
- comunicaciones operativas

VIPER es un sistema orientado a despacho/gestión de emergencias:

https://viper.cl/

El acceso a datos de despacho probablemente requiera autorización/convenio con Bomberos y/o proveedor.

Diferenciar:
- SIG Bomberos = dónde están compañías/grifos.
- Sistema de despacho = qué emergencia existe y qué unidades fueron enviadas.

## Otras fuentes consideradas

### Reportes ciudadanos

Función:
**Reportar emergencia**

Campos:
- tipo
- descripción
- GPS
- timestamp
- foto/video opcional

No mostrar automáticamente como confirmado.

Ejemplo:
- un reporte → reporte ciudadano
- varios reportes cercanos → aumenta confianza
- coincidencia con Bomberos/CONAF/FIRMS → alta confianza
- confirmación oficial → incidente confirmado

### Cámaras públicas

Posible fuente futura:

```text
cámara
  ↓
captura
  ↓
detección de humo
  ↓
clasificación
  ↓
alerta
```

Verificar siempre disponibilidad y condiciones de uso.

### Meteorología

Datos útiles:
- temperatura
- humedad
- velocidad/dirección del viento

Puede servir para contexto y estimación de propagación.

### OpenStreetMap

Base cartográfica para:
- calles
- edificios
- hospitales
- colegios
- estaciones de servicio
- caminos
- infraestructura

Se puede combinar con PostGIS.

## Motor de correlación

El corazón del proyecto debería ser el backend.

```text
Broadcastify ─┐
CONAF ────────┤
SENAPRED ─────┤
NASA FIRMS ───┤
Ciudadanos ───┤
Cámaras ──────┤
              ↓
        EVENT INGESTOR
              ↓
        NORMALIZACIÓN
              ↓
       PostgreSQL/PostGIS
              ↓
      CORRELATION ENGINE
              ↓
       CONFIDENCE ENGINE
              ↓
           INCIDENTE
              ↓
        REST API/WebSocket
              ↓
          PWA / Android
```

Guardar por evento:
- timestamp
- fuente
- tipo
- latitud
- longitud
- texto
- ID externo
- confidence
- raw_data

Agrupar eventos cercanos en espacio/tiempo.

Ejemplo:

```text
14:32 ciudadano reporta humo
14:34 Broadcastify registra despacho
14:36 FIRMS detecta anomalía térmica
14:40 CONAF registra incendio
```

→ Un único incidente.

## Confianza

Valores iniciales orientativos:

| Fuente | Confianza |
|---|---:|
| Bomberos / fuente institucional | 100% |
| SENAPRED | 100% |
| CONAF | 100% |
| Municipalidad | 90% |
| Medio de comunicación | 70% |
| Reporte ciudadano | 40–60% |
| Detección automática de RRSS | 30–60% |
| NASA FIRMS | señal de corroboración |

Deben calibrarse con datos reales.

## Presentación de incidentes

Ejemplo:

```text
🔥 INCENDIO FORESTAL

Sector: Forestal, Viña del Mar
Detectado: 14:32
Estado: Activo
Bomberos: En respuesta
CONAF: Confirmado
Satélite: Detectado
Reportes ciudadanos: 3

Confianza: 96%
```

Mapa:
- 🔴 incendio confirmado
- 🟠 emergencia en investigación
- 🟡 reporte ciudadano
- 🔵 alerta SENAPRED
- ⚫ incidente controlado

## Historial

Guardar todos los incidentes permitirá:
- historial de incendios
- mapas de concentración
- estadísticas por comuna
- estadísticas por sector
- análisis estacional
- zonas de recurrencia

## PWA vs Android

Decisión actual: **comenzar con PWA**.

Razones:
- un solo código para Android/iOS/web
- no requiere Play Store inicialmente
- actualizaciones instantáneas
- ideal para MVP
- mapa web
- geolocalización
- notificaciones push

Android nativo queda como segunda etapa, manteniendo el mismo backend.

## Stack propuesto

### Frontend
- React
- TypeScript
- PWA
- MapLibre GL JS

### Backend
- Python
- FastAPI
- PostgreSQL
- PostGIS
- Redis
- workers Python

### Procesamiento
- Speech-to-Text (por ejemplo Whisper)
- clasificación de comunicaciones
- extracción de entidades
- geocodificación
- correlación espacial/temporal

### Notificaciones
- Web Push inicialmente
- Firebase Cloud Messaging para Android posteriormente

## Arquitectura

```text
                   FUENTES
                      │
       ┌──────────────┼──────────────┐
       │              │              │
       ▼              ▼              ▼
  Broadcastify     NASA FIRMS      CONAF
       │              │              │
       └──────────────┼──────────────┘
                      │
              ┌───────▼───────┐
              │    INGESTOR   │
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │   PostgreSQL  │
              │   + PostGIS   │
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │ Correlation   │
              │ Engine        │
              └───────┬───────┘
                      ▼
              ┌───────────────┐
              │   Incident    │
              │   API         │
              └───────┬───────┘
                      ▼
                PWA / Android
```

## Estrategia de desarrollo

No comenzar por el frontend.

Primero construir un **Fire Data Collector** que recopile durante 7–14 días:
- Broadcastify
- CONAF
- NASA FIRMS
- SENAPRED
- eventualmente reportes ciudadanos

Guardar:

```text
timestamp
source
type
lat
lon
text
external_id
confidence
raw_data
```

Luego analizar cómo coinciden los eventos reales de Valparaíso/Viña del Mar entre las fuentes.

Esto permitirá diseñar el algoritmo de correlación usando datos reales antes de invertir demasiado tiempo en UI.

## MVP recomendado

1. Mapa de Valparaíso/Viña del Mar.
2. Incidentes en tiempo real.
3. NASA FIRMS.
4. CONAF.
5. Broadcastify, sujeto a acceso/condiciones.
6. SENAPRED.
7. Reportes ciudadanos.
8. Historial.
9. Filtros por tipo/estado/comuna.
10. Notificaciones de nuevos incidentes.
11. Ficha de cada incidente.
12. Indicador de confianza y fuentes.

## Consideraciones legales/técnicas

- Un feed público no implica automáticamente permiso para redistribuirlo.
- Confirmar condiciones de Broadcastify antes de incorporar audio/datos en producción.
- No retransmitir audio de radio sin autorización.
- Preferir convertir audio autorizado en eventos estructurados.
- No depender de endpoints internos/no documentados de SENAPRED o CONAF sin verificar condiciones.
- Diferenciar siempre entre detección, reporte e incidente confirmado.
- No presentar una detección satelital como incendio confirmado.
- Registrar la fuente de cada afirmación.

## Nombre

Nombre provisional principal: **AlertaV**

Concepto:
> Plataforma de monitoreo de emergencias de la Región de Valparaíso.

Alternativas:
- FOCO
- Alerta 5
- EmergenciaV
- FuegoV

Antes de adoptar un nombre:
- comprobar dominio .cl
- comprobar .com si interesa
- Google Play
- App Store
- redes sociales
- disponibilidad/marca registrada

## Estado actual

- Producto: plataforma de monitoreo de emergencias.
- Foco inicial: incendios en la Región de Valparaíso.
- Cliente inicial: **PWA**.
- Android nativo: segunda etapa.
- Backend independiente del frontend.
- Fuente satelital prioritaria: **NASA FIRMS**.
- Fuente de comunicaciones prioritaria: **Broadcastify 25506**, sujeto a acceso/condiciones.
- Fuentes oficiales complementarias: **CONAF, SENAPRED y SIG Bomberos**.
- Diferenciador principal: **correlación de múltiples fuentes + reportes ciudadanos + nivel de confianza**.
