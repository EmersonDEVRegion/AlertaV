<#
Instala o actualiza el proxy chileno de AlertaV en la VM de Oracle, desde Windows.

    .\infra\proxy-cl\desplegar.ps1 -Ip 203.0.113.7 -RenderCidrs "a.b.c.d/nn","e.f.g.h/nn"

1. Sube esta carpeta a la VM (scp).
2. Corre instalar.sh con sudo: tinyproxy, firewall de la VM y prueba interna.
   Al final imprime la linea ESVAL_PROXY_URL para Render.
3. Comprueba desde este PC que el puerto NO responde: tu IP no es de Render.

Usa el ssh/scp que trae Windows (OpenSSH). Reinstalar conserva la clave;
-RotarClave genera otra (hay que actualizar Render).
#>
param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [Parameter(Mandatory = $true)][string[]]$RenderCidrs,
    [string]$Usuario = "ubuntu",
    [string]$Llave = "",
    [int]$Puerto = 8888,
    [switch]$RotarClave
)
$ErrorActionPreference = "Stop"

foreach ($programa in @("ssh", "scp")) {
    if (-not (Get-Command $programa -ErrorAction SilentlyContinue)) {
        throw "Falta $programa. Windows lo trae como 'Cliente OpenSSH' (Configuracion > Aplicaciones > Caracteristicas opcionales)."
    }
}

$cidrs = @($RenderCidrs | ForEach-Object { $_ -split "[,\s]+" } | Where-Object { $_ })
foreach ($cidr in $cidrs) {
    if ($cidr -notmatch '^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$') { throw "'$cidr' no es un rango IPv4 (a.b.c.d/nn)" }
    if ($cidr -eq "0.0.0.0/0") { throw "0.0.0.0/0 abriria el proxy a todo internet" }
}

$opciones = @("-o", "StrictHostKeyChecking=accept-new")
if ($Llave) { $opciones += @("-i", $Llave) }
$destino = "$Usuario@$Ip"

Write-Host "==> Subiendo $PSScriptRoot a ${destino}:proxy-cl"
& ssh @opciones $destino "rm -rf proxy-cl"
if ($LASTEXITCODE -ne 0) { throw "No se pudo entrar por SSH a $destino" }
& scp @opciones -r $PSScriptRoot "${destino}:proxy-cl"
if ($LASTEXITCODE -ne 0) { throw "scp fallo" }

$rotar = if ($RotarClave) { "ROTAR=1 " } else { "" }
$remoto = "sudo ${rotar}PUERTO=$Puerto RENDER_CIDRS='$($cidrs -join ' ')' bash proxy-cl/instalar.sh"
Write-Host "==> $remoto"
& ssh @opciones -t $destino $remoto
if ($LASTEXITCODE -ne 0) { throw "instalar.sh fallo: revisa la salida de arriba" }

Write-Host ""
Write-Host "==> Desde este PC el puerto $Puerto tiene que estar CERRADO (tu IP no es de Render)"
$prueba = Test-NetConnection -ComputerName $Ip -Port $Puerto -WarningAction SilentlyContinue
if ($prueba.TcpTestSucceeded) {
    Write-Warning "El puerto $Puerto responde desde tu PC. Revisa la Security List de Oracle (README, paso 5): solo los rangos de Render."
} else {
    Write-Host "  OK  cerrado para tu IP."
}
Write-Host ""
Write-Host "Siguiente paso: pega la linea ESVAL_PROXY_URL en Render como secreto (README, paso 7)."
