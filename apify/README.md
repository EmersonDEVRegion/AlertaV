# Configuración de Apify

**Dos Tasks, un Actor, dos Schedules.** Desde el 2026-09-22 Apify se usa para
una sola cosa: raspar cuentas de X. Las de las centrales de Bomberos y, desde el
2026-10-06, la de tránsito del MTT.

| Task | Schedule | Qué pide | Entrega en |
|---|---|---|---|
| `alertav-bomberos` | `alertav`, cada 30 min | lo publicado en los **últimos 45 min** (`within_time`), hasta 6 por cuenta y 17 en total | `POST /api/v1/apify/webhook` |
| `alertav-canario` | `alertav-canario`, 12:10 cada día | los 2 últimos tuits de cada cuenta (12 en total), **sin ventana** | el mismo webhook |

Las seis cuentas, en los dos:

- Las cinco centrales: `@CGI_CBV` (Valparaíso), `@CBVM132` (Viña del Mar y
  Concón), `@despachoscbla` (Los Andes y Calle Larga), `@CBQuilpue` y
  `@cbquillota`. Entran como `bomberos`, confianza **1.00**.
- `@TTIValparaiso` (TransporteInforma Región de Valparaíso, del MTT). Entra como
  `transporte_informa`, confianza **0.80**, en la corrida `transporte_informa_x`
  (ver abajo).

Actor `xquik/x-tweet-scraper`.

## 6 de octubre de 2026: @TTIValparaiso

La web del MTT (`transporte_informa`) arrastra avisos de semanas; la cuenta de X
es su canal fresco. Agregarla al Task sin más habría sido pagar por tuits que el
webhook bota: una cuenta sin tabla de claves no se ingiere. Ahora:

- **Mismo Task, más cupo.** `maxItems` de 15 a **17** (6 por cuenta) y el
  canario de 10 a **12**. Así un aviso de tránsito no le quita lugar a un
  despacho salvo en una contingencia muy grande.
- **El backend la separa por cuenta** (`APIFY_X_TRANSITO_HANDLES`, por defecto
  `TTIValparaiso`) antes de buscar tabla. Sus tuits pasan por la tubería de la
  web del MTT: `clasificar_transito` (accidente → mapa; desvío, corte o faena →
  capa de cortes; saludos y recomendaciones → fuera), Gemini y Nominatim.
- **Corrida propia** en `collector_runs`: `transporte_informa_x`, sólo cuando la
  entrega trae tuits de la cuenta. Un fallo ahí no toca la corrida de Bomberos.
- **Ceguera:** `ttivalparaiso` está en `APIFY_X_CUENTAS_ESPERADAS`; el canario la
  exige como a las centrales.

Peor caso del mes, con la plataforma incluida:

| Task | Corridas al mes | Tuits por corrida (máx.) | Peor caso |
|---|---|---|---|
| `alertav-bomberos` | 1.488 | 17 | US$ 4,09 |
| `alertav-canario` | 31 | 12 | US$ 0,06 |
| **Total** | | | **US$ 4,15** |

El tope del script subió de US$ 4 a **US$ 4,50**: sigue bajo los US$ 5 del plan
Free, con margen para storage y pruebas.

**Orden de puesta en marcha:** primero el deploy del backend (que ya sepa leer a
@TTIValparaiso), después `configurar.ps1`. Al revés, las entregas de esa media
hora anotan «cuentas sin tabla» y nada más.

`configurar.ps1` crea o actualiza los dos Tasks, los **prueba** con una corrida
por la API, y recién entonces ajusta los Schedules y los webhooks.

## 30 de septiembre de 2026: cinco centrales, ventana de tiempo y canario

Con cinco cuentas a 6 tuits cada media hora el peor caso era US$ 6,70 al mes:
no cabía en Free. La salida fue dejar de pagar por tuits repetidos:

- **Ventana:** el Task principal pide sólo lo publicado en los últimos 45
  minutos (30 de cadencia + 15 de margen). xquik aplica el filtro **antes de
  cobrar** (probado el 30-09: una corrida sin tuits en la ventana cobró 0 tuits
  y US$ 0,00013 de plataforma). Una entrega vacía pasa a ser **calma**.
- **Canario:** como una entrega vacía ya no dice nada, la ceguera la mide un
  Task aparte, una vez al día y sin ventana. El backend lo reconoce por
  `APIFY_X_CANARIO_IDS`, lo registra como `bomberos_apify_canario` y SÓLO ahí
  exige ver cada cuenta de `APIFY_X_CUENTAS_ESPERADAS`. Un lote que trae items
  y todos son relleno sigue siendo `degraded` en cualquiera de los dos.
- **Seguimientos:** «SALE M-34 A CLAVE 15 …» ya no es un despacho nuevo: sus
  unidades se anexan al original (mismo cuerpo, clave y calles, dentro de 90
  min) sin modelo ni Nominatim.

Peor caso del mes, con la plataforma incluida (US$ 0,0002 por corrida):

| Task | Corridas al mes | Tuits por corrida (máx.) | Peor caso |
|---|---|---|---|
| `alertav-bomberos` | 1.488 | 15 | US$ 3,65 |
| `alertav-canario` | 31 | 10 | US$ 0,05 |
| **Total** | | | **US$ 3,70** |

Lo normal es mucho menos: el peor caso supone que cada media hora las centrales
publican 15 cosas. `configurar.ps1` se niega a aplicar una configuración cuyo
peor caso pase de **US$ 4** sin `-AceptarCosto`.

Lo que se pierde: si una central publica más de 6 cosas en 45 minutos, las más
viejas no llegan (en un incendio grande, casi todas son «SALE … A …», que se
anexan igual al original si llegan).

Variables de Render que hay que tener (el script las imprime al final):

```
APIFY_BOMBEROS_ACTOR_IDS = <id de alertav-bomberos>,<id de alertav-canario>
APIFY_X_CANARIO_IDS      = <id de alertav-canario>
APIFY_X_SCHEDULE_MINUTES = 30
```

## Septiembre de 2026: un mes sin despachos con la salud en verde

Del 31-08 al 29-09 no entró **ni un despacho** de Bomberos. El Actor de
entonces, `apidojo/tweet-scraper`, dejó de raspar en el plan Free cuando lo
lanza el Scheduler: cada corrida terminaba en `SUCCEEDED` con diez items
`{"noResults": true}` y cobraba US$ 0,004. Su log lo decía —*«The developer of
this actor doesn't allow the use of Scheduler in the Free Plan»*— pero nadie lo
leía, y el webhook trataba esos items como tuits sin clave: `success`, 0
insertados, salud `ok`.

Lo que cambió:

- **Actor:** `xquik/x-tweet-scraper`, US$ 0,00015 por tuit, sin cargo por
  corrida, con `maxItemsPerTarget` para repartir el cupo entre las dos cuentas.
- **Ceguera visible:** el backend exige ver tuits de las cuentas de
  `APIFY_X_CUENTAS_ESPERADAS` (por defecto las dos) en cada entrega. Sin
  ninguna → `degraded`, y `/collectors/health` lo muestra; falta una →
  `partial`, con la cuenta nombrada. Los items de relleno se cuentan aparte.
- **Fechas:** el formato de X (`Tue Sep 29 18:50:12 +0000 2026`) no se
  entendía y el despacho pasaba como «sin fecha», que el filtro de edad deja
  pasar. Con un Actor que funcionara, la primera entrega habría metido al mapa
  despachos de hace días con la hora de hoy.
- **`-Auditar`** muestra lo que trajeron las últimas corridas
  (`10 items, 10 de relleno: ningun tuit con autor` era el síntoma).

La prueba de `configurar.ps1` usa la API, **no el Scheduler**, que es justo
donde falló el Actor anterior. Después del primer disparo programado hay que
mirar `-Auditar` o `/collectors/health`.

> **Correr `configurar.ps1` antes del 26-09-2026.** Al 23-09, la cuenta sigue
> con el Schedule `alertav` corriendo los tres Tasks viejos cada 30 minutos, y
> `alertav-bomberos` solo tiene `CGI_CBV`. Si no se corre, cuando se renueve el
> período Instagram vuelve a gastar los US$ 5 en unos ocho días.

## Lo que salió y por qué

| Task retirado | Lo cubre ahora |
|---|---|
| `alertav-prensa` (X: MTT, Rutas del Pacífico, RNE…) | prensa local por RSS (`prensa_local`) |
| `alertav-instagram` | prensa local por RSS |

El motivo fue la cuota. Desde el 2026-09-23 el código de las dos capas ya no
está en el backend (collector de Instagram, `apify_press_service`, la puerta
`/webhook/prensa` y los `task-*.json`): si hiciera falta volver, está en el
historial de git. `configurar.ps1` **lista** los dos Tasks viejos si siguen en
la cuenta, pero no los borra: hay que borrarlos a mano en el panel de Apify.

## Cinco centrales, tres sistemas de claves

Además de las dos de abajo:

| | @despachoscbla (Los Andes) | @CBQuilpue y @cbquillota |
|---|---|---|
| Formato | `10-0-1 (LLAMADO ESTRUCTURAL), MEMBRILLAR /CHACABUCO, QB-2,RB-3,BT-5` | `Clave 5-1 AYMARAS / LOS CARRERA M-43, M-11` (como Viña) |
| Sistema | **nacional**: las emergencias son la familia 10; de 0 a 9 es radio. El `0` de `10-0-x` NO se colapsa | la familia 1–6 de la costa |
| Tabla | Wurtlitzer, completa | **provisional**: no hay tabla publicada |
| No se ingiere | radio, 10-9, 10-10, 10-11, 10-12, 10-15 y algunos subtipos | 17-x de Quilpué (servicios internos), 15 de Quillota |
| Claves que entran | `BOMBEROS_CBLA_KEYS` | `BOMBEROS_QUILPUE_KEYS`, `BOMBEROS_QUILLOTA_KEYS` |

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

Con `queryType: Latest`, cada corrida devuelve los `maxItems` tuits más
recientes **aunque ya se hayan leído**, y los repetidos se vuelven a cobrar. Es
a propósito: que el Actor traiga siempre los últimos tuits de cada central es
lo que permite distinguir «no publicó» de «no se ve». Con xquik el peor caso
del mes es `corridas × maxItems × US$ 0,00015`:

| Cron | maxItems | Peor caso al mes | ¿Cabe en Free (US$ 5)? |
|---|---|---|---|
| `*/15 * * * *` | 12 | US$ 5,36 | No |
| `*/30 * * * *` (**el de ahora**) | 12 | US$ 2,68 | Sí |
| `0 * * * *` | 12 | US$ 1,34 | Sí |

`configurar.ps1` hace esta cuenta antes de tocar nada y **se niega a seguir**
si el peor caso pasa de US$ 4 (con la plataforma incluida), salvo que se le pase `-AceptarCosto`. Esa
opción es para cuando la cuenta tenga un plan de pago.

Lo que se pierde con 6 tuits por central cada media hora: si una central
publica más de 6 cosas en 30 minutos, las más viejas de esa media hora no
llegan. `maxItemsPerTarget` reparte el cupo, así que la Clave 16 de Viña ya no
puede dejar fuera a Valparaíso.

Si se cambia el Cron, hay que cambiar `APIFY_X_SCHEDULE_MINUTES` en Render al
mismo valor. El script lo imprime al final. Si no coinciden, `/collectors/health`
marca el webhook como detenido entre dos entregas normales. Y
`APIFY_WEBHOOK_MAX_AGE_MINUTES` (180) tiene que seguir siendo mayor que la
cadencia.

## Run options

```
Timeout             180 s      (0 = sin límite, y una corrida colgada se come el crédito)
Memory              512 MB     (256 MB va lento y una corrida lenta muere por timeout)
Maximum cost/run    US$ 0,01   (opción maxTotalChargeUsd; -MaxCostoPorCorrida en el script;
                               una corrida normal cuesta ~US$ 0,002)
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
(campo `ids_actor`) y al final de `configurar.ps1`. **Al cambiar de Actor el
Task es otro y su id también**: hay que reemplazarlo en Render, o el webhook
ignora las entregas del Task nuevo.

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
