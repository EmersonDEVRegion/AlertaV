# Crea en Apify el Task de X de las centrales de Bomberos, su webhook y el
# Schedule.
#
# DESDE EL 2026-09-22 ES UN SOLO TASK. Los de prensa (X) y de Instagram salieron
# por cuota: el plan gratuito no alcanzaba para tres, y lo que cubrian lo toma
# la prensa local por RSS en el backend. Correr este script deja el Schedule
# 'alertav' con el Task de Bomberos solo, borra el webhook huerfano del Task de
# prensa y LISTA los dos Tasks viejos para que los borres en el panel (no los
# borra: ver "Tasks duplicados" mas abajo).
#
# Es idempotente: busca por nombre antes de crear, y actualiza si ya existe. Se
# puede correr las veces que haga falta.
#
# DESDE EL 2026-09-30: CINCO CENTRALES, VENTANA DE TIEMPO Y CANARIO
# -----------------------------------------------------------------
# Se sumaron @despachoscbla (Los Andes), @CBQuilpue y @cbquillota. Con cinco
# cuentas a 6 tuits cada media hora el peor caso era US$ 6,70 al mes: no cabia
# en Free. Ahora son DOS Tasks:
#
#   alertav-bomberos  cada 30 min, `within_time` 45m: SOLO lo publicado en los
#                     ultimos 45 minutos. xquik filtra antes de cobrar, asi que
#                     una media hora tranquila cuesta solo la plataforma.
#   alertav-canario   una vez al dia, sin ventana, 2 tuits por cuenta. Es lo
#                     que prueba que el Actor VE cada cuenta: con la ventana, una
#                     entrega vacia es calma y ya no sirve para eso. El backend
#                     lo reconoce por APIFY_X_CANARIO_IDS y lo registra aparte.
#
# DESDE EL 2026-09-29 EL ACTOR ES xquik/x-tweet-scraper
# ------------------------------------------------------
# apidojo/tweet-scraper dejo de raspar en el plan Free cuando lo lanza el
# Scheduler: cada corrida escribia diez {"noResults": true}, terminaba en
# SUCCEEDED y cobraba US$ 0,004. Del 31-08 al 29-09 no entro un solo despacho y
# la salud se veia verde. El log del Actor lo decia: "The developer of this
# actor doesn't allow the use of Scheduler in the Free Plan".
#
# Un Task no puede cambiar de Actor. Si el Task existente usa otro Actor, el
# script lo RENOMBRA (alertav-bomberos-<actor>-retirado), crea uno nuevo con el
# nombre de siempre, y el id nuevo hay que copiarlo a APIFY_BOMBEROS_ACTOR_IDS
# en Render (se imprime al final). Antes de tocar el Schedule, el Task nuevo se
# PRUEBA con una corrida por la API: si no trae tuits de las centrales, el
# script se detiene y el Schedule queda como estaba.
#
# EL ORDEN IMPORTA (2026-09-23)
# -----------------------------
# Primero el Task, despues el Schedule y recien despues los webhooks. El
# Schedule es lo que gasta credito, y con la cuenta al tope Apify rechaza crear
# webhooks: si el webhook iba antes y fallaba, el script abortaba y el Schedule
# seguia corriendo los tres Tasks viejos. Un fallo de webhook ahora se avisa y
# no detiene nada.
#
# SOBRE LAS CREDENCIALES
# ----------------------
# `APIFY_TOKEN` y `APIFY_WEBHOOK_SECRET` se leen de backend\.env y NUNCA se
# imprimen ni se escriben en ningún archivo. El token viaja en la cabecera
# `Authorization`, jamás en la query — mismo invariante que el backend: una URL
# con el token dentro termina en los logs del proxy y en el historial de quien
# la copie.
#
#   .\apify\configurar.ps1
#   .\apify\configurar.ps1 -DryRun     # muestra qué haría, sin tocar nada
#   .\apify\configurar.ps1 -Auditar    # lista webhooks, tasks, schedules y consumo
#   .\apify\configurar.ps1 -Cron "0 */2 * * *"   # otra cadencia (ver COSTO)
#   .\apify\configurar.ps1 -SinProbar  # no correr la prueba del Task (no recomendado)

param(
    [switch]$DryRun,
    [switch]$Auditar,
    # Permite una cadencia cuyo peor caso supera el plan gratuito (ver COSTO).
    [switch]$AceptarCosto,
    # Salta la corrida de prueba del Task antes de tocar el Schedule.
    [switch]$SinProbar,
    [string]$ApiBase = "https://api.apify.com/v2",
    [string]$BackendUrl = "https://alertav-api.onrender.com",
    # Cada 30 minutos: con xquik (US$ 0,00015 por tuit, sin cargo por corrida)
    # cabe en Free, ver COSTO. Si se cambia, APIFY_X_SCHEDULE_MINUTES en Render
    # tiene que cambiar con el (el script lo imprime al final).
    [string]$Cron = "*/30 * * * *",
    # El canario: una vez al dia (hora de Chile). Ver arriba.
    [string]$CronCanario = "10 12 * * *",
    # Tope de gasto de UNA corrida (opcion maxTotalChargeUsd del Task). Con
    # maxItems = 17 y US$ 0,00015 por tuit, una corrida llena cuesta US$ 0,0028.
    [double]$MaxCostoPorCorrida = 0.01
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot

# --- Credenciales ------------------------------------------------------------

function Read-EnvValue([string]$archivo, [string]$clave) {
    if (-not (Test-Path $archivo)) { throw "No existe $archivo" }
    foreach ($linea in Get-Content $archivo) {
        if ($linea -match "^\s*$clave\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

# Primero el entorno del proceso, despues backend\.env.
#
# Ese orden importa en este proyecto: las credenciales de produccion viven en
# Render, no en el disco, y el .env local puede estar meses atrasado — lo estaba
# la primera vez que se corrio esto. Con el entorno primero, se puede pasar la
# credencial para una sola invocacion sin escribirla en ningun archivo.
function Get-Credencial([string]$clave) {
    $delEntorno = [Environment]::GetEnvironmentVariable($clave)
    if ($delEntorno) { return $delEntorno.Trim() }
    return Read-EnvValue (Join-Path $raiz "backend\.env") $clave
}

$token = Get-Credencial "APIFY_TOKEN"
$secreto = Get-Credencial "APIFY_WEBHOOK_SECRET"

if (-not $token) {
    throw @"
APIFY_TOKEN no esta ni en el entorno ni en backend\.env.

Sacalo de https://console.apify.com/settings/integrations y pasalo asi, en la
MISMA linea que la llamada para que no quede en tu historial de sesion:

    `$env:APIFY_TOKEN='apify_api_...'; `$env:APIFY_WEBHOOK_SECRET='...'; .\apify\configurar.ps1

O ponelos en backend\.env, que esta en .gitignore.
"@
}
Write-Host "Token cargado ($($token.Length) caracteres)." -ForegroundColor DarkGray

# Sin secreto NO se crean webhooks. Antes esto era un aviso amarillo y se
# seguía adelante; el resultado fue dos integraciones que no podian funcionar
# ni una sola vez.
#
# Si el backend tiene APIFY_WEBHOOK_SECRET puesto —y en produccion lo tiene— la
# ruta responde 401 a toda entrega sin cabecera. Apify reintenta once veces y
# despues DESHABILITA la integracion. O sea que crear el webhook sin secreto no
# deja el sistema "abierto pero andando": lo deja roto, y encima el diagnostico
# llega por correo horas mas tarde.
#
# El 2026-09-03 costo exactamente eso: dos correos de Apify diciendo
# "Endpoint responded with HTTP status code 401", uno por Task.
if (-not $secreto) {
    throw @"
APIFY_WEBHOOK_SECRET no esta ni en el entorno ni en backend\.env.

Sin el, los webhooks se crearian sin cabecera de autenticacion y el backend
responderia 401 a cada entrega, hasta que Apify deshabilite la integracion.

Pasalo junto al token, en la misma linea:

    `$env:APIFY_TOKEN='apify_api_...'; `$env:APIFY_WEBHOOK_SECRET='...'; .\apify\configurar.ps1

Tiene que ser EL MISMO valor que la variable APIFY_WEBHOOK_SECRET de Render.
"@
}

$headers = @{ Authorization = "Bearer $token" }

function Api([string]$metodo, [string]$ruta, $cuerpo = $null) {
    $pedido = @{ Method = $metodo; Uri = "$ApiBase$ruta"; Headers = $headers }
    if ($cuerpo) {
        $json = $cuerpo | ConvertTo-Json -Depth 12 -Compress
        $pedido.ContentType = "application/json; charset=utf-8"
        $pedido.Body = [System.Text.Encoding]::UTF8.GetBytes($json)
    }
    try {
        return Invoke-RestMethod @pedido
    }
    catch {
        # Apify explica el rechazo en el cuerpo: {"error":{"type":..,"message":..}}.
        # Sin esto solo queda "(403) Prohibido", que no distingue una cuenta al
        # tope de un token sin permiso (2026-09-23).
        $original = $_
        $detalle = $original.ErrorDetails.Message
        if (-not $detalle) { throw }
        try {
            $e = ($detalle | ConvertFrom-Json).error
            if ($e) { $detalle = "$($e.type): $($e.message)" }
        }
        catch { }
        throw "$metodo $ruta -> $($original.Exception.Message) $detalle"
    }
}

# --- Consumo de la cuenta -----------------------------------------------------
#
# El 2026-09-03 la cuenta llego a US$ 5,01 de US$ 5 y Apify dejo de iniciar
# Actors hasta el periodo siguiente. Nadie lo vio hasta el correo: el mapa solo
# mostro que Bomberos dejaba de entregar. Se imprime siempre, al principio.
#
# Lo que se gasto ese periodo (Billing, 2026-09-23):
#   apify/instagram-scraper   1.682 resultados x US$ 0,0027 = US$ 4,54
#   apidojo/tweet-scraper     1.120 tuits      x US$ 0,0004 = US$ 0,45
# Instagram fue el 91 %. Por eso salio.

function Show-Consumo {
    try {
        $lim = (Api GET "/users/me/limits").data
        $usado = [double]$lim.current.monthlyUsageUsd
        $tope = [double]$lim.limits.maxMonthlyUsageUsd
        $fin = $lim.monthlyUsageCycle.endAt
        Write-Host ("Consumo del periodo: US$ {0:N2} de US$ {1:N2} (el periodo termina {2})" -f $usado, $tope, $fin) -ForegroundColor DarkGray
        if ($tope -gt 0 -and $usado -ge $tope) {
            Write-Host "  CUENTA AL TOPE: Apify no inicia corridas hasta el proximo periodo." -ForegroundColor Yellow
            Write-Host "  El Schedule se aplica igual, para que al renovarse corra SOLO Bomberos." -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host "  (no se pudo leer el consumo: $($_.Exception.Message))" -ForegroundColor DarkGray
    }
}

# --- COSTO: cuanto cuesta la cadencia elegida -----------------------------------
#
# El Actor de X (xquik/x-tweet-scraper, desde el 2026-09-29) cobra
# US$ 0,00015 por tuit devuelto, sin cargo por corrida. Con queryType=Latest
# cada corrida devuelve los `maxItems` tuits mas recientes, SEAN NUEVOS O NO:
# los repetidos se vuelven a cobrar. Y conviene que sea asi: que el Actor traiga
# siempre los ultimos tuits de cada central es lo que le permite al backend
# distinguir "la central no publico" de "el Actor no ve" (APIFY_X_CUENTAS_ESPERADAS).
# El peor caso del mes es
#
#     corridas al mes x maxItems x US$ 0,00015
#
#   cada 30 min, 12 tuits:  1.488 x 12 x 0,00015 = US$ 2,68   (el de ahora)
#   cada 15 min, 12 tuits:  2.976 x 12 x 0,00015 = US$ 5,36   (no cabe en Free)
#   cada 1 h,    12 tuits:    744 x 12 x 0,00015 = US$ 1,34
#
# (Con apidojo, a US$ 0,0004, cada 30 min con 25 tuits eran US$ 14,88.)
#
# El plan Free tiene US$ 5 al mes y ademas cobra storage y transferencia (unos
# centavos). Si el peor caso pasa de US$ 4 el script se niega a seguir sin
# -AceptarCosto, que es para cuando la cuenta tenga un plan de pago.
#
# Lo que se pierde con 6 tuits por central cada 30 minutos: si una central
# publica mas de 6 cosas en media hora, las mas viejas de esa media hora no
# llegan. maxItemsPerTarget reparte el cupo, asi que la Clave 16 de Vina ya no
# puede dejar fuera a Valparaiso.

$PrecioPorTuit = 0.00015
# Lo que cobra la plataforma por corrida (memoria y CPU de unos segundos), aparte
# de los tuits. Medido el 2026-09-30: entre US$ 0,00012 y 0,00018. Se redondea
# para arriba. Con 1.488 corridas al mes son unos US$ 0,30: ya no es despreciable.
$PlataformaPorCorrida = 0.0002
# US$ 4,50 y no 5: el margen cubre storage, transferencia y las pruebas. Era
# US$ 4 hasta el 2026-10-06; subio para sumar @TTIValparaiso (maxItems 15 -> 17,
# peor caso US$ 4,15 con el canario) sin pasar de los US$ 5 del plan Free.
$TopePlanFree = 4.5

# Corridas al mes de un cron simple: "M * * * *", "*/N * * * *", "0 */N * * *",
# listas con comas. Devuelve $null si el cron es de otra forma (dia, mes o dia de
# la semana restringidos): en ese caso no se estima y se avisa.
function Get-CorridasPorDia([string]$cron) {
    $p = $cron.Trim() -split '\s+'
    if ($p.Count -ne 5) { return $null }
    if ($p[2] -ne '*' -or $p[3] -ne '*' -or $p[4] -ne '*') { return $null }

    $porHora = $null
    if ($p[0] -match '^\*/(\d+)$') { $porHora = [math]::Ceiling(60 / [int]$Matches[1]) }
    elseif ($p[0] -match '^\d+(,\d+)*$') { $porHora = ($p[0] -split ',').Count }
    if ($null -eq $porHora) { return $null }

    $horas = $null
    if ($p[1] -eq '*') { $horas = 24 }
    elseif ($p[1] -match '^\*/(\d+)$') { $horas = [math]::Ceiling(24 / [int]$Matches[1]) }
    elseif ($p[1] -match '^\d+(,\d+)*$') { $horas = ($p[1] -split ',').Count }
    if ($null -eq $horas) { return $null }

    return $porHora * $horas
}

# Resume un lote de items de un Actor de X: cuantos, cuantos de relleno y cuantos
# tuits de cada cuenta. Mismos alias que el backend (apify_webhook_service).
function Get-CuentaDeTuit($i) {
    foreach ($contenedor in @("author", "user")) {
        $a = $i.$contenedor
        if ($a) {
            foreach ($k in @("userName", "username", "screen_name", "screenName")) {
                if ($a.$k) { return "$($a.$k)".TrimStart("@").ToLower() }
            }
        }
    }
    foreach ($k in @("userName", "username", "authorUsername", "handle")) {
        if ($i.$k) { return "$($i.$k)".TrimStart("@").ToLower() }
    }
    foreach ($k in @("url", "twitterUrl", "tweetUrl", "tweet_url")) {
        if ("$($i.$k)" -match '^https://(x|twitter)\.com/([^/]+)/status/') { return $Matches[2].ToLower() }
    }
    return $null
}

function Resumir-Tuits($items) {
    $items = @($items)
    $relleno = @($items | Where-Object { $_.noResults -or $_.demo -or (-not $_.text -and -not $_.id -and -not $_.url) }).Count
    $porCuenta = @{}
    foreach ($i in $items) {
        $c = Get-CuentaDeTuit $i
        if ($c) { $porCuenta[$c] = 1 + [int]$porCuenta[$c] }
    }
    $cuentas = ($porCuenta.GetEnumerator() | Sort-Object Name | ForEach-Object { "@$($_.Name) x$($_.Value)" }) -join ", "
    if (-not $cuentas) { $cuentas = "ningun tuit con autor" }
    return "$($items.Count) items, $relleno de relleno: $cuentas"
}

# Las cuentas que el CANARIO tiene que traer. Mismo valor por defecto que
# APIFY_X_CUENTAS_ESPERADAS en el backend: las cinco centrales y, desde el
# 2026-10-06, @TTIValparaiso (transito del MTT).
$CuentasEsperadas = @("cgi_cbv", "cbvm132", "despachoscbla", "cbquilpue", "cbquillota", "ttivalparaiso")

Show-Consumo

# --- Auditoria: solo lee, no cambia nada ------------------------------------
#
# Existe porque despues de limpiar seguian llegando correos de 401 nombrando un
# Task viejo, y sin ver el estado completo de la cuenta cualquier explicacion
# era conjetura. Muestra las TRES cosas que pueden disparar una entrega:
# webhooks, tasks y schedules. El script gestiona un solo schedule —`alertav`—
# asi que cualquier otro que exista sigue corriendo lo que tenga dentro, y eso
# no se ve mirando webhooks.

if ($Auditar) {
    $anfitrion = ([uri]$BackendUrl).Host

    Write-Host "`n=== WEBHOOKS que apuntan a $anfitrion ===" -ForegroundColor Cyan
    $webhooks = (Api GET "/webhooks?limit=1000").data.items |
                Where-Object { $_.requestUrl -and ([uri]$_.requestUrl).Host -eq $anfitrion }
    if (-not $webhooks) { Write-Host "  (ninguno)" }
    foreach ($w in $webhooks) {
        $de = if ($w.condition.actorTaskId) { "task $($w.condition.actorTaskId)" }
              elseif ($w.condition.actorId) { "ACTOR $($w.condition.actorId)" }
              else { "sin condicion" }
        # `headersTemplate` puede traer el secreto: se dice si LO HAY, nunca cual.
        $cab = if ($w.headersTemplate -and $w.headersTemplate -match "Secret") { "con cabecera" }
               else { "SIN CABECERA -> 401 seguro" }
        Write-Host "  [$($w.id)] $de"
        Write-Host "      $($w.requestUrl)  ($cab)"
    }

    Write-Host "`n=== TASKS con nombre de AlertaV ===" -ForegroundColor Cyan
    $tareas = (Api GET "/actor-tasks?limit=1000").data.items |
              Where-Object { $_.name -match "(?i)alerta" }
    foreach ($t in $tareas) { Write-Host "  [$($t.id)] $($t.name)" }

    Write-Host "`n=== SCHEDULES ===" -ForegroundColor Cyan
    $nombres = @{}; foreach ($t in $tareas) { $nombres[$t.id] = $t.name }
    foreach ($s in (Api GET "/schedules?limit=1000").data.items) {
        $estado = if ($s.isEnabled) { "activo" } else { "pausado" }
        Write-Host "  '$($s.name)' ($($s.cronExpression)) $estado"
        foreach ($a in $s.actions) {
            $quien = if ($nombres.ContainsKey($a.actorTaskId)) { $nombres[$a.actorTaskId] }
                     else { $a.actorTaskId }
            Write-Host "      -> $quien"
        }
    }

    # Lo que trajeron las ultimas corridas del Task. Es lo que habria delatado
    # el problema de septiembre de 2026 en un minuto: SUCCEEDED, 10 items,
    # todos {"noResults": true}.
    foreach ($nombre in @("alertav-bomberos", "alertav-canario")) {
        $t = $tareas | Where-Object { $_.name -eq $nombre } | Select-Object -First 1
        if (-not $t) { continue }
        Write-Host "`n=== ULTIMAS CORRIDAS de $nombre ===" -ForegroundColor Cyan
        $corridas = (Api GET "/actor-tasks/$($t.id)/runs?desc=1&limit=5").data.items
        foreach ($c in $corridas) {
            $muestra = Api GET "/datasets/$($c.defaultDatasetId)/items?clean=true&limit=50"
            # El costo total de la corrida (tuits + plataforma), para no tener
            # que ir a la consola a mirarlo.
            $costo = ""
            try {
                $detalle = (Api GET "/actor-runs/$($c.id)").data
                if ($null -ne $detalle.usageTotalUsd) {
                    $tuits = [double]$detalle.chargedEventCounts."apify-default-dataset-item" * $PrecioPorTuit
                    $costo = " (US$ {0:N4} = tuits {1:N4} + plataforma {2:N4})" -f ($tuits + [double]$detalle.usageTotalUsd), $tuits, [double]$detalle.usageTotalUsd
                }
            }
            catch { }
            Write-Host ("  {0} {1}  {2}{3}" -f $c.startedAt, $c.status, (Resumir-Tuits $muestra), $costo)
        }
    }
    Write-Host ""
    return
}


# --- Definicion del Task ----------------------------------------------------
#
# Uno solo: las centrales y @TTIValparaiso en la misma corrida. El backend
# separa cada tuit por su autor: los de una central los lee con el diccionario
# de claves de su Cuerpo; los de @TTIValparaiso (APIFY_X_TRANSITO_HANDLES), con
# la tuberia del MTT, como transporte_informa.
#
# Los Tasks retirados, por si hubiera que volver a encenderlos (junto con
# APIFY_PRENSA_ENABLED / APIFY_INSTAGRAM_ENABLED en Render):
#
#   @{ nombre = "alertav-prensa";    actor = "apidojo~tweet-scraper";
#      archivo = "task-prensa.json";    webhook = "/api/v1/apify/webhook/prensa" }
#   @{ nombre = "alertav-instagram"; actor = "apify~instagram-scraper";
#      archivo = "task-instagram.json"; webhook = $null }

$tasks = @(
    @{ nombre = "alertav-bomberos"; actor = "xquik~x-tweet-scraper";
       archivo = "task-bomberos.json"; webhook = "/api/v1/apify/webhook";
       schedule = "alertav"; cron = $Cron; canario = $false }
    @{ nombre = "alertav-canario"; actor = "xquik~x-tweet-scraper";
       archivo = "task-bomberos-canario.json"; webhook = "/api/v1/apify/webhook";
       schedule = "alertav-canario"; cron = $CronCanario; canario = $true }
)

# Timeout 180s y no 0 (=sin limite): una corrida colgada se come el credito.
# Memory 512MB y no 256: con menos va lento, y lento choca con el timeout — y
# una corrida que expira NO queda en SUCCEEDED, que es lo que el collector lee.
# maxTotalChargeUsd: el tope de gasto por corrida que el README prometia y el
# Task no tenia ("Maximum cost per run: Unlimited" en el panel, 2026-09-23).
$runOptions = @{
    build             = "latest"
    timeoutSecs       = 180
    memoryMbytes      = 512
    maxTotalChargeUsd = $MaxCostoPorCorrida
}

# --- Estimacion de costo, antes de tocar nada ---------------------------------

# Peor caso: cada corrida trae su `maxItems` completo. Con la ventana de tiempo
# eso solo pasa si una central publica mas de 6 cosas en 45 minutos; lo normal
# es mucho menos (se ve con -Auditar).
$peorCaso = 0.0
$cadenciaMin = $null
$estimable = $true
foreach ($t in $tasks) {
    $e = Get-Content (Join-Path $PSScriptRoot $t.archivo) -Raw -Encoding UTF8 | ConvertFrom-Json
    $porDia = Get-CorridasPorDia $t.cron
    if ($null -eq $porDia) {
        Write-Host "No se puede estimar el costo del cron '$($t.cron)' de $($t.nombre) (forma no simple). Revisalo a mano." -ForegroundColor Yellow
        $estimable = $false
        continue
    }
    $mes = $porDia * 31 * ([int]$e.maxItems * $PrecioPorTuit + $PlataformaPorCorrida)
    $peorCaso += $mes
    if (-not $t.canario) { $cadenciaMin = [math]::Ceiling(1440 / $porDia) }
    Write-Host ("{0} '{1}': {2} corridas al dia, hasta {3} tuits -> peor caso US$ {4:N2} al mes" -f $t.nombre, $t.cron, $porDia, $e.maxItems, $mes)
}
if ($estimable) {
    Write-Host ("Peor caso total: US$ {0:N2} al mes (tope del script: US$ {1:N2})" -f $peorCaso, $TopePlanFree)
    if ($peorCaso -gt $TopePlanFree -and -not $AceptarCosto) {
        throw @"
El peor caso (US$ $([math]::Round($peorCaso, 2)) al mes) no cabe en el plan gratuito de Apify.

Con la cuenta en Free, al pasar los US$ 5 Apify detiene TODOS los Actors hasta
el periodo siguiente, y Bomberos deja de entregar por dias. Opciones:
  - una cadencia mas espaciada:  -Cron "0 */2 * * *"
  - menos tuits por corrida: bajar maxItems (y maxItemsPerTarget) en task-bomberos.json
  - una ventana mas corta: within_time en task-bomberos.json
  - si la cuenta ya tiene plan de pago, repetir con -AceptarCosto
"@
    }
}

$resultado = @()
$avisos = @()

# --- Lo que hay que copiar a Render -----------------------------------------
#
# El guard tiene que autorizar el actorTaskId. Estos ids NO son secretos:
# identifican un Task, no autorizan nada por si solos. Se imprimen tambien con
# -DryRun: actualizar un Task no le cambia el id, asi que el que tiene hoy es el
# definitivo. Solo un Task que todavia no existe se queda sin id hasta crearlo.
function Show-VariablesRender {
    Write-Host "`n--- Variables para Render ---" -ForegroundColor Yellow
    # Los dos Tasks entregan por el mismo webhook: los dos van en la lista de
    # autorizados, y el canario ademas en APIFY_X_CANARIO_IDS.
    $ids = @($script:resultado | ForEach-Object { $_.TaskId })
    $canarios = @($script:resultado | Where-Object { $_.Canario } | ForEach-Object { $_.TaskId })
    Write-Host "APIFY_BOMBEROS_ACTOR_IDS = $($ids -join ',')"
    if ($canarios) { Write-Host "APIFY_X_CANARIO_IDS      = $($canarios -join ',')" }
    if ($script:cadenciaMin) { Write-Host "APIFY_X_SCHEDULE_MINUTES = $($script:cadenciaMin)" }
    Write-Host "APIFY_X_CUENTAS_ESPERADAS = $(($CuentasEsperadas | ForEach-Object { $_.ToUpper() }) -join ',')   (es el valor por defecto: no hace falta ponerla)"
}

# --- 1. El Task ---------------------------------------------------------------

foreach ($t in $tasks) {
    $entrada = Get-Content (Join-Path $PSScriptRoot $t.archivo) -Raw -Encoding UTF8 | ConvertFrom-Json

    # ¿Existe ya? Se busca por nombre para poder re-ejecutar sin duplicar.
    $existentes = (Api GET "/actor-tasks?limit=1000").data.items
    $previo = $existentes | Where-Object { $_.name -eq $t.nombre } | Select-Object -First 1

    # Un Task no cambia de Actor: si el que existe usa otro, se retira con otro
    # nombre y se crea uno nuevo. Renombrar y no borrar, por la misma razon que
    # el punto 5 de abajo: el input viejo puede valer la pena mirarlo.
    $actorDeseado = (Api GET "/acts/$($t.actor)").data
    if ($previo -and $previo.actId -ne $actorDeseado.id) {
        $viejo = (Api GET "/acts/$($previo.actId)").data
        $retiro = "$($t.nombre)-$($viejo.name)-retirado"
        if ($DryRun) {
            Write-Host "RENOMBRARIA $($t.nombre) -> $retiro (usa $($viejo.username)/$($viejo.name)) y CREARIA uno nuevo sobre $($t.actor)"
            $resultado += [pscustomobject]@{
                Task = $t.nombre; TaskId = "(nuevo: se asigna al crearlo)"; Webhook = $t.webhook
                Canario = $t.canario; Schedule = $t.schedule; Cron = $t.cron
            }
            continue
        }
        Api PUT "/actor-tasks/$($previo.id)" @{ name = $retiro } | Out-Null
        Write-Host "Task viejo renombrado: $retiro (usaba $($viejo.username)/$($viejo.name))" -ForegroundColor Yellow
        $previo = $null
    }

    if ($DryRun) {
        $accion = if ($previo) { "ACTUALIZARIA" } else { "CREARIA" }
        Write-Host "$accion task $($t.nombre) sobre $($t.actor)"
        $idHoy = if ($previo) { $previo.id } else { "(se asigna al crearlo: correr sin -DryRun)" }
        $resultado += [pscustomobject]@{
            Task = $t.nombre; TaskId = $idHoy; Webhook = $t.webhook
            Canario = $t.canario; Schedule = $t.schedule; Cron = $t.cron
        }
        continue
    }

    if ($previo) {
        # Si Apify rechaza la actualizacion (cuenta al tope, por ejemplo), el
        # Task viejo sigue existiendo y el Schedule se ajusta igual con su id:
        # que deje de correr lo retirado importa mas que actualizar el input.
        try {
            $task = Api PUT "/actor-tasks/$($previo.id)" @{
                name = $t.nombre; options = $runOptions; input = $entrada
            }
            $taskId = $task.data.id
            Write-Host "Task actualizado: $($t.nombre)" -ForegroundColor Cyan
        }
        catch {
            $taskId = $previo.id
            $avisos += "task $($t.nombre): $($_.Exception.Message)"
            Write-Host "  AVISO: no se pudo actualizar el Task $($t.nombre); el Schedule se ajusta igual." -ForegroundColor Yellow
        }
    }
    else {
        $task = Api POST "/actor-tasks" @{
            actId = $actorDeseado.id; name = $t.nombre
            options = $runOptions; input = $entrada
        }
        $taskId = $task.data.id
        Write-Host "Task creado: $($t.nombre)" -ForegroundColor Green
    }

    $resultado += [pscustomobject]@{
        Task = $t.nombre; TaskId = $taskId; Webhook = $t.webhook
        Canario = $t.canario; Schedule = $t.schedule; Cron = $t.cron
    }
}

if ($DryRun) {
    foreach ($t in $tasks) { Write-Host "Dejaria el Schedule '$($t.schedule)' con $($t.nombre) ($($t.cron))" }
    Show-VariablesRender
    Write-Host "`n(DryRun: no se cambio nada. Repetir sin -DryRun para aplicar.)" -ForegroundColor DarkGray
    return
}

# --- 1b. Prueba del Task, antes de tocar el Schedule ---------------------------
#
# Una corrida por la API, esperando el resultado. Si no trae ni un tuit de las
# centrales, el script se detiene: el Schedule queda como estaba y nada nuevo
# empieza a gastar. Cuesta lo que una corrida normal (unos US$ 0,002).
#
# OJO: esto prueba la API, no el Scheduler. El Actor anterior corria bien por
# la API y se negaba solo con el Scheduler. Despues del primer disparo
# programado, mirar -Auditar: si las corridas traen relleno, el backend ya lo
# marca como `degraded` en /collectors/health.

if (-not $SinProbar) {
    foreach ($r in $resultado) {
        Write-Host "`nProbando $($r.Task) (una corrida por la API, hasta 3 minutos)..." -ForegroundColor Cyan
        $items = Api POST "/actor-tasks/$($r.TaskId)/run-sync-get-dataset-items?timeout=170&clean=true"
        $resumen = Resumir-Tuits $items
        Write-Host "  $resumen"
        $relleno = @(@($items) | Where-Object { $_.noResults -or $_.demo }).Count
        if (-not $r.Canario) {
            # Con ventana de tiempo, cero tuits es posible y no dice nada: lo que
            # SI delata a un Actor que no ve es el relleno. La visibilidad de
            # cada cuenta la prueba el canario.
            if ($relleno -gt 0) {
                throw "La prueba de $($r.Task) trajo $relleno items de relleno: el Actor no esta entregando. El Schedule NO se toco."
            }
            continue
        }
        $vistas = @(@($items) | ForEach-Object { Get-CuentaDeTuit $_ } | Where-Object { $_ } | Sort-Object -Unique)
        $faltan = @($CuentasEsperadas | Where-Object { $_ -notin $vistas })
        if ($faltan.Count -eq $CuentasEsperadas.Count) {
            throw @"
La prueba de $($r.Task) no trajo ningun tuit de las centrales ($resumen).

El Schedule NO se toco. Revisa el log de esa corrida en el panel de Apify
(Actors -> Tasks -> $($r.Task) -> Runs). Si el Actor dice que el plan Free no
le alcanza, hay que elegir otro Actor en `$tasks.
"@
        }
        if ($faltan) {
            $avisos += "la prueba de $($r.Task) no trajo tuits de: $($faltan -join ', ')"
            Write-Host "  AVISO: falta @$($faltan -join ', @')" -ForegroundColor Yellow
        }
    }
}

# --- 2. El Schedule, antes que los webhooks -----------------------------------
#
# Es lo que gasta credito, y por eso va primero: ver "EL ORDEN IMPORTA" arriba.
#
# Cada corrida del Actor de X se cobra. Si se cambia el Cron, hay que cambiar
# tambien APIFY_X_SCHEDULE_MINUTES en Render (se imprime al final): con eso la
# salud sabe cada cuanto esperar una entrega y no marca en falso las tres
# familias. Y APIFY_WEBHOOK_MAX_AGE_MINUTES (180) tiene que seguir siendo MAYOR
# que la cadencia, o un despacho publicado justo despues de una corrida llega
# viejo a la siguiente y se descarta.

# Un Schedule por Task: tienen cadencias distintas (cada 30 min y una vez al dia).
$schedulesHoy = (Api GET "/schedules?limit=1000").data.items
foreach ($r in $resultado) {
    $previo = $schedulesHoy | Where-Object { $_.name -eq $r.Schedule } | Select-Object -First 1
    $cuerpoSchedule = @{
        name           = $r.Schedule
        cronExpression = $r.Cron
        isEnabled      = $true
        isExclusive    = $true
        timezone       = "America/Santiago"
        actions        = @(@{ type = "RUN_ACTOR_TASK"; actorTaskId = $r.TaskId })
    }
    if ($previo) {
        Api PUT "/schedules/$($previo.id)" $cuerpoSchedule | Out-Null
        Write-Host "Schedule '$($r.Schedule)' actualizado ($($r.Cron)): solo $($r.Task)" -ForegroundColor Cyan
    } else {
        Api POST "/schedules" $cuerpoSchedule | Out-Null
        Write-Host "Schedule '$($r.Schedule)' creado ($($r.Cron)): $($r.Task)" -ForegroundColor Green
    }
}

# --- 3. Webhooks, SOLO sobre el Task ------------------------------------------
#
# Nunca sobre el Actor: un webhook colgado del Actor dispara tambien para las
# corridas de sus Tasks, y con los dos puestos cada corrida entrega dos veces.
#
# Un fallo aca NO detiene el script: el Task y el Schedule ya quedaron bien, y
# con la cuenta al tope Apify responde que los webhooks no estan habilitados.
# Se avisa y se sigue. La unica excepcion es el 401 de la sonda, que significa
# que el secreto no coincide con el de Render y el webhook no va a servir.

foreach ($r in $resultado) {
    if (-not $r.Webhook) {
        Write-Host "  $($r.Task): sin webhook (pull)" -ForegroundColor DarkGray
        continue
    }

    $url = "$BackendUrl$($r.Webhook)"
    $plantilla = (@{ "X-AlertaV-Apify-Secret" = $secreto } | ConvertTo-Json -Compress)
    $cuerpo = @{
        eventTypes      = @("ACTOR.RUN.SUCCEEDED")
        condition       = @{ actorTaskId = $r.TaskId }
        requestUrl      = $url
        headersTemplate = $plantilla
        isAdHoc         = $false
    }

    try {
        $wh = (Api GET "/webhooks?limit=1000").data.items |
              Where-Object { $_.condition.actorTaskId -eq $r.TaskId } |
              Select-Object -First 1
        if ($wh) {
            Api PUT "/webhooks/$($wh.id)" $cuerpo | Out-Null
            Write-Host "  webhook actualizado -> $($r.Webhook)" -ForegroundColor Cyan
        } else {
            Api POST "/webhooks" $cuerpo | Out-Null
            Write-Host "  webhook creado      -> $($r.Webhook)" -ForegroundColor Green
        }
    }
    catch {
        $avisos += "webhook de $($r.Task): $($_.Exception.Message)"
        Write-Host "  AVISO: no se pudo crear/actualizar el webhook de $($r.Task)." -ForegroundColor Yellow
        Write-Host "  Si la cuenta esta al tope, repetir el script cuando se renueve el periodo." -ForegroundColor Yellow
        continue
    }

    # --- Comprobar la cabecera contra el backend REAL ------------------------
    #
    # El script termina diciendo "listo" aunque haya dejado una cabecera que el
    # backend rechaza. Sin esta comprobacion, el unico aviso llega por correo de
    # Apify horas despues —"Endpoint responded with HTTP status code 401"— y
    # para entonces la integracion puede estar deshabilitada.
    #
    # Se manda un cuerpo sin `defaultDatasetId` a proposito: el backend lo
    # responde `200 ignored` sin leer ningun dataset ni escribir en la base. Lo
    # unico que se esta probando es la puerta.
    #
    # 90 s y no 45: en el plan gratuito de Render el servicio se duerme y la
    # primera peticion tarda en despertarlo.
    try {
        $sonda = Invoke-WebRequest -Method POST -Uri $url `
            -Headers @{ "X-AlertaV-Apify-Secret" = $secreto } `
            -Body '{"eventType":"CONFIGURACION","resource":{}}' `
            -ContentType "application/json" -UseBasicParsing -TimeoutSec 90
        Write-Host "  verificado          -> HTTP $($sonda.StatusCode)" -ForegroundColor DarkGray
    } catch {
        $codigo = $_.Exception.Response.StatusCode.value__
        if ($codigo -eq 401) {
            throw @"
El backend rechazo la cabecera con 401 en $url

El webhook quedo creado pero NO va a funcionar: el valor de
APIFY_WEBHOOK_SECRET que se uso aca no coincide con el de Render.
Corregilo y volve a correr este script: es idempotente.
"@
        }
        $avisos += "sonda de $($r.Task): HTTP $codigo"
        Write-Host "  AVISO: la sonda devolvio HTTP $codigo (revisar)" -ForegroundColor Yellow
    }
}

# --- 4. Limpieza de webhooks huerfanos -----------------------------------------
#
# Toda configuracion manual anterior sigue viva en el panel. Un webhook viejo
# apuntando a la misma URL entrega igual, con la cabecera que tuviera entonces
# —o sin ninguna— y el backend responde 401 a cada intento. Apify reintenta
# once veces y despues deshabilita esa integracion.
#
# El sintoma son correos de Apify por Tasks que uno cree tener bien
# configurados, y corridas fantasma en `collector_runs`. Ya habia pasado antes
# en este proyecto: el endpoint documenta que baja el log del 401 sin credencial
# a INFO precisamente porque estos huerfanos inundaban Render.
#
# El criterio de borrado es estricto y esta puesto para no pasarse:
#
#   1. Solo webhooks cuya `requestUrl` apunte a ESTE backend. Cualquier otra
#      integracion de la cuenta —Slack, otro proyecto, lo que sea— no se toca.
#   2. Solo los que NO cuelgan de uno de los Tasks que este script gestiona.
#
# Lo segundo cubre tambien los webhooks colgados del ACTOR en vez del Task, que
# son los que provocan la entrega doble: un webhook de Actor dispara ademas para
# las corridas de sus Tasks.

$gestionados = @($resultado | ForEach-Object { $_.TaskId })
$anfitrion = ([uri]$BackendUrl).Host

$limpiezaOk = $false
try {
    $huerfanos = (Api GET "/webhooks?limit=1000").data.items | Where-Object {
        $_.requestUrl -and
        ([uri]$_.requestUrl).Host -eq $anfitrion -and
        $_.condition.actorTaskId -notin $gestionados
    }

    if ($huerfanos) {
        Write-Host "`nWebhooks huerfanos apuntando a $anfitrion :" -ForegroundColor Yellow
        foreach ($h in $huerfanos) {
            $de = if ($h.condition.actorTaskId) { "task $($h.condition.actorTaskId)" }
                  elseif ($h.condition.actorId) { "ACTOR $($h.condition.actorId)" }
                  else { "sin condicion" }
            Write-Host "  borrando: $de -> $($h.requestUrl)"
            Api DELETE "/webhooks/$($h.id)" | Out-Null
        }
        Write-Host "  $($huerfanos.Count) eliminados." -ForegroundColor Green
    }
    else {
        Write-Host "`nSin webhooks huerfanos." -ForegroundColor DarkGray
    }
    $limpiezaOk = $true
}
catch {
    $avisos += "limpieza de webhooks: $($_.Exception.Message)"
    Write-Host "`nAVISO: no se pudieron revisar los webhooks huerfanos." -ForegroundColor Yellow
}

# --- 5. Tasks duplicados: se reportan, NO se borran ----------------------------
#
# El script empareja por nombre exacto, asi que una configuracion manual previa
# con otro nombre —"Alertav Prensa" contra `alertav-prensa`— no se reutiliza: se
# crea un Task nuevo al lado. Los dos quedan vivos y, si el viejo esta en algun
# Schedule, sigue corriendo y gastando credito para no entregar nada (su webhook
# acaba de borrarse arriba).
#
# Se reportan y no se borran a proposito. Un Task puede tener un input afinado a
# mano que valga la pena mirar antes de tirarlo, y borrar cosas de la cuenta de
# alguien sin preguntar es de las pocas acciones que no se deshacen.

$otros = (Api GET "/actor-tasks?limit=1000").data.items | Where-Object {
    $_.name -match "(?i)alerta" -and $_.id -notin $gestionados
}

if ($otros) {
    Write-Host "`nTasks con nombre de AlertaV que este script NO gestiona:" -ForegroundColor Yellow
    foreach ($o in $otros) { Write-Host "  $($o.name)  [$($o.id)]" }
    if ($limpiezaOk) {
        Write-Host "  Sus webhooks ya se eliminaron y el Schedule 'alertav' ya no los corre." -ForegroundColor DarkGray
    }
    else {
        Write-Host "  El Schedule 'alertav' ya no los corre, pero sus webhooks NO se pudieron borrar (ver avisos)." -ForegroundColor Yellow
    }
    Write-Host "  alertav-prensa y alertav-instagram estan RETIRADOS desde 2026-09-22:" -ForegroundColor DarkGray
    Write-Host "  borralos en el panel. Si hay otro Schedule que los corra, sigue" -ForegroundColor DarkGray
    Write-Host "  gastando credito: revisalo con -Auditar." -ForegroundColor DarkGray
}

# --- Lo que hay que copiar a Render (ver Show-VariablesRender) ---------------

Show-VariablesRender

if ($avisos) {
    Write-Host "`nTerminado con avisos:" -ForegroundColor Yellow
    foreach ($a in $avisos) { Write-Host "  - $a" -ForegroundColor Yellow }
}
else {
    Write-Host "`nListo." -ForegroundColor Green
}
