# Configuración de Apify

Tres Tasks, dos Actors, un Schedule.

| Task | Actor | Entrega en | Ingiere como |
|---|---|---|---|
| `bomberos` | Tweet Scraper V2 | `POST /api/v1/apify/webhook` | `bomberos`, confianza **1.00** |
| `prensa` | Tweet Scraper V2 | `POST /api/v1/apify/webhook/prensa` | según la cuenta, 0.45–0.80 |
| `instagram` | Instagram Scraper | *(nada: es pull)* | `social_media`, 0.35 |

## Por qué Bomberos y prensa NO comparten Task

`/apify/webhook` está cableado a la central: filtra por claves `10-x` e ingiere
con confianza 1.00, la única banda que por sí sola marca un incidente como
confirmado. Un tuit de prensa por ahí o se descarta entero, o —si coincidiera
con un patrón de clave— entra con el peso de un despacho oficial.

La ruta de prensa tiene además una **lista blanca por cuenta**
(`services/apify_press_service.py`, `HANDLES`). Es lo único que impide que un
término de búsqueda mal puesto en el Task convierta a cualquier vecino en fuente.
Por eso los dos Tasks llevan `searchTerms` vacío: acá se recolecta por cuenta,
no por palabra.

## Run options (los tres Tasks)

```
Timeout             180 s     (0 = sin límite, y una corrida colgada se come el crédito)
Memory              512 MB    (256 MB va lento y una corrida lenta muere por timeout)
Maximum cost/run    $0.10
```

Una corrida que expira **no queda en `SUCCEEDED`**, y el collector de Instagram
lee `runs/last?status=SUCCEEDED`: se queda con el dataset anterior y reporta
«datos rancios». Ése era el bucle del 2026-09-02.

## Webhooks: SÓLO en el Task, nunca en el Actor

Un webhook colgado del Actor dispara también para las corridas que lanza un Task
suyo. Si está en los dos niveles, cada corrida entrega dos veces: no duplica
eventos —`external_id` es determinista— pero duplica llamadas al modelo y hace
competir dos lecturas por el presupuesto de geocodificación.

Cabecera en ambos: `X-AlertaV-Apify-Secret: <APIFY_WEBHOOK_SECRET>`.
Evento: `ACTOR.RUN.SUCCEEDED`. La plantilla de payload por defecto sirve tal cual.

## Schedule

Uno solo, con los tres Tasks dentro. Cron `*/30 * * * *`.

30 minutos y no 5: `APIFY_MAX_RUN_AGE_MINUTES` tolera 45, así que media hora deja
margen para una corrida fallida sin que la capa se declare ciega, y estira el
crédito del plan gratuito.

## Después de la primera entrega

Los dos Tasks salen del mismo Actor y **comparten `actId`**, así que hay que
autorizar el `actorTaskId`. Sale en el log de la primera entrega de cada uno,
campo `ids_actor`:

```
APIFY_BOMBEROS_ACTOR_IDS = <actorTaskId del Task bomberos>
APIFY_PRENSA_ACTOR_IDS   = <actorTaskId del Task prensa>
```

Mientras estén vacías el guard está apagado y cualquier Task puede entregar por
cualquier puerta.

## Pendiente: las claves de @CGI_CBV

`BOMBEROS_ACCIDENT_KEYS` sólo declara la familia 10 y el 12, y la central publica
también otras —se detectó `CLAVE 5-1` en un accidente real—. Las que no están se
**descartan**, y desde el 2026-09-02 quedan avisadas en el log:

```
la central publicó claves que no están en BOMBEROS_ACCIDENT_KEYS
  claves: {"5-1": 1}
```

Antes de agregar una hay que saber qué significa: sin entrada en `CLAVE_MEANINGS`
y `CODE_TYPES` el despacho entra sin tipo, y un despacho de peso 1.00 mal
tipificado es peor que uno perdido.
