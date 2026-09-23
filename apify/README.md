# Configuración de Apify

**Un Task, un Actor, un Schedule.** Desde el 2026-09-22 Apify se usa para una
sola cosa: raspar las cuentas de X de las centrales de Bomberos.

| Task | Actor | Cuentas | Entrega en | Ingiere como |
|---|---|---|---|---|
| `alertav-bomberos` | Tweet Scraper V2 | `@CBVM132`, `@CGI_CBV` | `POST /api/v1/apify/webhook` | `bomberos`, confianza **1.00** |

`configurar.ps1` crea o actualiza ese Task, su webhook y el Schedule `alertav`.

> **Correr `configurar.ps1` antes del 26-09-2026.** Al 23-09, la cuenta sigue
> con el Schedule `alertav` corriendo los tres Tasks viejos cada 30 minutos, y
> `alertav-bomberos` solo tiene `CGI_CBV`. Si no se corre, cuando se renueve el
> período Instagram vuelve a gastar los US$ 5 en unos ocho días.

## Lo que salió y por qué

| Task retirado | Lo cubre ahora | Cómo reencenderlo |
|---|---|---|
| `alertav-prensa` (X: MTT, Rutas del Pacífico, RNE…) | prensa local por RSS (`prensa_local`) | `APIFY_PRENSA_ENABLED=true` + volver a listarlo en `configurar.ps1` |
| `alertav-instagram` | prensa local por RSS | `APIFY_INSTAGRAM_ENABLED=true` + volver a listarlo en `configurar.ps1` |

El motivo es la cuota. El código de las dos capas sigue en el backend, con sus
tests. Solo están apagadas: no se registran, no cuentan para la salud del mapa
y la puerta `/webhook/prensa` responde `ignored` si le llega algo.
`task-prensa.json` y `task-instagram.json` quedan acá por si hubiera que
volver a encenderlas.

Después de correr `configurar.ps1`, el script **lista** los dos Tasks viejos,
pero no los borra. Hay que borrarlos a mano en el panel de Apify.

## Dos centrales, dos diccionarios

Las dos cuentas publican con la automatización de Viper, pero **son dos
sistemas de claves distintos**:

| | @CGI_CBV (Valparaíso) | @CBVM132 (Viña del Mar y Concón) |
|---|---|---|
| Formato | `72 * PRIMERO DE MAYO / 12 DE OCTUBRE * CLAVE 4-1` | `Clave 5-1 LOS PELLINES / LOS GINKOS U-63, U-82` |
| `Clave 3` | no existe (3-1 / 3-2) | incendio vehicular |
| `Clave 10` | abastecer agua: **no se ingiere** | otros servicios: **se ingiere** |
| `Clave 16` | no existe | servicios internos: no se ingiere |
| Claves que entran | `BOMBEROS_ACCIDENT_KEYS` | `BOMBEROS_CBVM_KEYS` |

El backend lee el autor de cada tuit (`author.userName` o la URL) y usa la
tabla de su Cuerpo (`vocabulary.SistemaClaves`). Un tuit de una cuenta **sin
tabla** no se ingiere, aunque venga en el mismo dataset. Tampoco los retuits.
Las tablas salen de las claves publicadas por Wurtlitzer, contrastadas con
tuits reales de cada cuenta.

La `Clave 14` del CBVM queda a propósito sin decidir. Una versión de la tabla
la da como accidente eléctrico y otra como ejercicio de unidad. Si la central
la usa, el webhook avisa «clave no configurada» y la corrida queda `partial`.

## Cuota: lo que cuesta cada corrida

Hay que leerlo antes de elegir el Cron. Lo que pasó en el período del 26-08 al
25-09 de 2026, según Billing:

| Actor | Cobro | Consumo | Costo |
|---|---|---|---|
| `apify/instagram-scraper` | US$ 0,0027 por resultado | 1.682 resultados | **US$ 4,54** |
| `apidojo/tweet-scraper` | US$ 0,0004 por tuit | 1.120 tuits | US$ 0,45 |

El 3 de septiembre la cuenta llegó a US$ 5,01 de US$ 5 y Apify dejó de iniciar
Actors hasta el 26: Bomberos no entregó nada durante tres semanas. Instagram
fue el 91 % del gasto, y por eso salió.

Con sort `Latest`, cada corrida devuelve los `maxItems` tuits más recientes
**aunque ya se hayan leído**, y los repetidos se vuelven a cobrar. El peor caso
del mes es `corridas × maxItems × US$ 0,0004`:

| Cron | maxItems | Peor caso al mes | ¿Cabe en Free (US$ 5)? |
|---|---|---|---|
| `*/30 * * * *` | 25 | US$ 14,88 | No |
| `0 * * * *` (**el de ahora**) | 15 | US$ 4,46 | Sí, con margen |
| `0 */2 * * *` | 25 | US$ 3,72 | Sí |

`configurar.ps1` hace esta cuenta antes de tocar nada y **se niega a seguir**
si el peor caso pasa de US$ 4,50, salvo que se le pase `-AceptarCosto`. Esa
opción es para cuando la cuenta tenga un plan de pago.

Lo que se pierde con 15 tuits por hora: si entre las dos centrales publican más
de 15 despachos en una hora, los más viejos de esa hora no llegan. Si el log del
webhook muestra una y otra vez corridas de exactamente 15 ítems, esa es la señal
de que hace falta más cupo, ya sea con un plan de pago o con más frecuencia.

Si se cambia el Cron, hay que cambiar `APIFY_X_SCHEDULE_MINUTES` en Render al
mismo valor. El script lo imprime al final. Si no coinciden, `/collectors/health`
marca el webhook como detenido entre dos entregas normales. Y
`APIFY_WEBHOOK_MAX_AGE_MINUTES` (180) tiene que seguir siendo mayor que la
cadencia.

## Run options

```
Timeout             180 s      (0 = sin límite, y una corrida colgada se come el crédito)
Memory              512 MB     (256 MB va lento y una corrida lenta muere por timeout)
Maximum cost/run    US$ 0,01   (opción maxTotalChargeUsd; -MaxCostoPorCorrida en el script)
```

Hasta el 2026-09-23 el Task decía "Maximum cost per run: Unlimited": este
README prometía un tope que el script no fijaba.

## Orden del script

Primero el Task, después el Schedule y al final los webhooks. El Schedule es lo
que gasta crédito, y con la cuenta al tope Apify rechaza crear webhooks. Antes
el webhook iba primero: si fallaba, el script abortaba y el Schedule seguía
corriendo los tres Tasks viejos. Ahora un fallo de webhook, o de actualización
del Task, queda como aviso al final y no detiene el resto.

## Webhooks: SÓLO en el Task, nunca en el Actor

Un webhook colgado del Actor dispara también para las corridas que lanza un Task
suyo. Si está en los dos niveles, cada corrida entrega dos veces: no duplica
eventos —`external_id` es determinista— pero duplica llamadas al modelo y hace
competir dos lecturas por el presupuesto de geocodificación.

Cabecera: `X-AlertaV-Apify-Secret: <APIFY_WEBHOOK_SECRET>`.
Evento: `ACTOR.RUN.SUCCEEDED`. La plantilla de payload por defecto sirve tal cual.

## Después de la primera entrega

Autorizar el `actorTaskId` del Task, que sale en el log de la primera entrega
(campo `ids_actor`) y al final de `configurar.ps1`:

```
APIFY_BOMBEROS_ACTOR_IDS = <actorTaskId del Task alertav-bomberos>
```

Mientras esté vacía el guard está apagado y cualquier corrida puede entregar.

## Las claves de @CGI_CBV

Resuelto el 2026-09-02. Las tablas del código describían **otro sistema de
claves**: `10-x` como incendios y rescates, `12` como «llamado a servicio
especial». En el CBV real, `10` es abastecer agua y `12` es **Academia de
Cuerpo** — una capacitación que podía entrar al mapa con confianza 1.00 — y los
accidentes son `5-x` (rescate vehicular) y `9-x` (túnel), que no existían en
ninguna tabla.

Hoy `CLAVE_MEANINGS`, `CODE_TYPES` y `BOMBEROS_ACCIDENT_KEYS` siguen la tabla
oficial del Cuerpo, y `NON_INCIDENT_CODES` declara lo que se despacha y no es
una emergencia del mapa (7, 8, 10, 11, 12, 13, 14).

El prompt de Gemini se construye **desde** la tabla de cada Cuerpo en tiempo de
ejecución, así que no hay un glosario escrito a mano que pueda quedar atrás.

Si una central empieza a despachar una clave que no está, queda avisada en el
log y en `collector_runs`:

```
la central publicó claves que no están en BOMBEROS_ACCIDENT_KEYS
  claves: {"7-9": 1, "CBVM Clave 14": 2}
```

Antes de agregarla hay que saber qué significa: sin entrada en la tabla del
Cuerpo el despacho entra sin tipo, y uno de peso 1.00 mal tipificado es peor
que uno perdido. Hay tests que lo impiden en los dos sentidos, para los dos
Cuerpos.
