# Instagram vía Apify: por qué polling y no webhook

Decisión de arquitectura del collector `instagram_apify`. Escrita el 2026-08-25.

## El problema

Las cuentas hiperlocales de Instagram (`@alertanoticiasvalparaiso` y similares)
publican accidentes antes que el MTT. Raspar Meta directamente con `httpx` no es
viable: el WAF bloquea desde IP de datacenter al segundo o tercer intento, y no
lo anuncia con un 429 reintentable — devuelve un login wall con HTTP 200. Apify
es esa infraestructura comprada hecha.

La pregunta no es si usar Apify. Es **quién dispara el Actor y quién lee los
datos**, que resultan ser dos decisiones separadas.

## La cuenta que decide todo

Apify cobra **por resultado raspado**, no por petición a su API. El Actor de
Instagram está en torno a 1,5 USD por 1.000 posts en planes de pago (2,3–2,7 en
los bajos), y cobra igual por un post que ya procesamos ayer que por uno nuevo.

| Configuración | Resultados/día | Costo aprox. |
|---|---|---|
| 3 cuentas × 10 posts × 288 corridas (cada 5 min) | 8.640 | ~390 USD/mes |
| 3 cuentas × 5 posts × 96 corridas (cada 15 min) | 1.440 | ~65 USD/mes |

Seis veces el precio por la misma cobertura. **Lo único que mueve la factura es
la cadencia del Actor y su `resultsLimit`.** Ninguna optimización del backend
cambia ese número.

## La decisión

**Se separa quién raspa de quién lee.**

```
  Schedule de Apify            AlertaV (CRON 5 min)
  ─────────────────            ────────────────────
  cada 15 min                  GET /acts/{id}/runs/last?status=SUCCEEDED
    └─> corre el Actor         GET /acts/{id}/runs/last/dataset/items?status=SUCCEEDED
        └─> dataset  ────────>   (gratis, idempotente, sin efectos)
```

* **El Actor lo dispara un Schedule de Apify**, configurado una vez en su panel.
  El gasto queda en un solo lugar, visible, ajustable sin desplegar.
* **Nuestro CRON de 5 minutos sólo lee el dataset de la última corrida
  exitosa.** Leer no consume unidades de cómputo. Poder leer más seguido de lo
  que se raspa es gratis y baja la latencia.

Lo que se descartó explícitamente: llamar a `run-sync-get-dataset-items` desde
el CRON. Bloquea 30–90 s dentro de un worker que comparte event loop con el
motor de correlación, y dispara una corrida pagada cada cinco minutos.

## Por qué no webhook

El webhook es tentador —notifica en el instante en que el Actor termina, sin
peticiones inútiles— y sin embargo no gana:

1. **No ahorra dinero.** El gasto es el scraping. Un webhook cambia cómo nos
   enteramos, no cuánto se raspa.
2. **Abre superficie pública.** Habría que exponer un endpoint de ingesta con
   verificación de firma, protección anti-replay e idempotencia propia. Hoy el
   backend no acepta escrituras de terceros salvo el reporte ciudadano, que está
   detrás de rate limit.
3. **Un webhook perdido es un accidente perdido.** Si el POST llega durante un
   despliegue o un 502, Apify reintenta un puñado de veces y se rinde. El
   polling no tiene ese modo de fallo: la corrida siguiente vuelve a mirar el
   mismo dataset.
4. **Rompe la traza.** Todo lo que entra al sistema deja una fila en
   `collector_runs` con su estado, sus contadores y su error. Un webhook escribe
   fuera de ese camino, y `collector_runs` dejaría de ser la respuesta completa
   a "¿qué pasó en los últimos 30 minutos?".

**Cuándo reconsiderarlo:** si la latencia de 5 minutos resultara ser el cuello
de botella medible frente al MTT. Sería una capa *encima* del polling —adelanta
la lectura, no la reemplaza—, nunca en su lugar.

## El fallo silencioso que esta arquitectura introduce

`runs/last?status=SUCCEEDED` devuelve **la última corrida exitosa, tenga la edad
que tenga**. Si el Schedule se detiene —Instagram cambia algo y el Actor empieza
a fallar, se agota el crédito, alguien lo pausa—, la API sigue sirviendo
alegremente el dataset de anteayer. El collector lo leería, descartaría todo por
`external_id` ya conocido, y reportaría `success` con 0 eventos. Indistinguible
de un día tranquilo.

Es exactamente el modo de fallo que este proyecto persigue en todas partes
(`page_looks_broken` en el MTT, `revisar_feed` en Bomberos, `describe_kmz` en
CGE). Lo cubre `apify_client.run_looks_stale`: se pide el `finishedAt` de la
corrida —una llamada más, gratis— y si supera `APIFY_MAX_RUN_AGE_MINUTES` la
corrida queda `partial` con el motivo escrito en `collector_runs.error`.

El segundo fallo silencioso es distinto y también está cubierto: los Actors de
Instagram **no fallan** cuando un perfil es privado o dejó de existir; empujan
al dataset un item con forma de error y terminan en `SUCCEEDED`. Lo separa
`describe_items`.

## Delta fetching: qué ahorra y qué no

Tres capas, de la más barata a la más cara:

1. `is_fresh` — función pura. Descarta lo anterior a `INSTAGRAM_MAX_AGE_MINUTES`.
2. `classify_event_type` — función pura. Descarta lo que no es una emergencia.
3. `unseen` — **una** consulta a `raw_events` por corrida
   (`ids_by_external_id`). Descarta lo ya procesado.
4. `uq_raw_events_source_external_id` — la red de seguridad en la base.

**Esto no ahorra un peso de Apify**, y conviene tenerlo claro: ahorra tokens de
Gemini y llamadas a Nominatim, que es otro presupuesto y además compartido con
el collector del MTT (el limitador de Nominatim es global al proceso).

Se descartó la cuarta capa evidente —guardar el `finishedAt` de la última
corrida procesada y pedir sólo lo posterior— por frágil: si una corrida muere
después de mover la marca y antes de escribir los eventos, la ventana se pierde
en silencio. El estado ya vive en `raw_events`, que es auditable; una segunda
copia sólo puede desincronizarse.

## Confianza: por qué 0.35 y no 0.70

Que una cuenta se llame "noticias" no la convierte en un medio. No hay
redacción, no hay segunda fuente y no hay corrección: republica lo que le llega
por mensaje directo. Entra como `EventSource.SOCIAL_MEDIA`.

0.35 es el `max_weight` de esa fuente en `confidence.py`. Emitir más alto no
sube nada —el motor recorta igual— y sólo dejaría archivado en `raw_events` un
número que ninguna decisión del sistema respeta.

> Nota aparte: `SOURCE_BASE_CONFIDENCE[SOCIAL_MEDIA]` vale 0.45 y
> `RULES[SOCIAL_MEDIA].max_weight` vale 0.35. La discrepancia es anterior a este
> collector. No se toca desde acá porque arreglarla mueve la confianza de todas
> las fuentes sociales a la vez y merece su propio commit.

## El fuego sin calificar va a `OTHER`

`classify_event_type` es determinista, no un LLM — misma frontera que el worker
del MTT: el modelo extrae calles, nunca decide qué ocurrió.

Un caption que dice "incendio" a secas (que es como lo escribe la mitad de estas
cuentas) se clasifica como `OTHER`, no como `WILDFIRE`. Consecuencia concreta:
cae en la familia `other` y **no puede fusionarse** con los incendios que CONAF o
FIRMS reporten a 500 metros. Pierde corroboración a propósito — preferimos un
punto huérfano en el mapa antes que subirle la confianza a un incendio con
evidencia que no vale lo que aparenta.

Si estas cuentas resultan ser buenas prediciendo incendios, el cambio es una
línea. Haber inflado incendios durante seis meses no se deshace.

## Vigilar en el primer despliegue

1. `collector_runs` de `instagram_apify`. El aviso «datos rancios» significa que
   el Schedule de Apify no está corriendo.
2. La primera factura de Apify. La única palanca es el Schedule, no el `.env`.
3. Cuántos posts quedan sin coordenadas (el `warn` de `normalize`). Si son casi
   todos, el problema está en `clean_caption` o en el prompt del extractor, no
   en Nominatim.
