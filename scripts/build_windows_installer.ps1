<#
.SYNOPSIS
Packages a previously verified PROD PyInstaller bundle as a per-user installer.
.DESCRIPTION
Requires Inno Setup 6.3 or newer on the build computer only. No Python, compiler,
development tool, database or credential is added to the destination computer.
The bundle manifest determines the version and source commit. The script checks
the complete SHA256SUMS inventory and refuses unknown or modified payload files.

For a non-invasive packaging rehearsal, use a new OutputDirectory under artifacts
and an approved bundle. Compiling does NOT install the ERP, create shortcuts, or
change its real HKCU uninstall registration. -ValidateOnly performs input checks
without compiling. Install/uninstall tests need Windows Sandbox, a disposable VM
or a separate Windows user; /DIR alone does NOT isolate HKCU or Start Menu.
.EXAMPLE
.\scripts\build_windows_installer.ps1 -BundlePath <bundle> -ExpectedVersion 1.1.0
.EXAMPLE
.\scripts\build_windows_installer.ps1 -BundlePath <bundle> -ValidateOnly
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [string]$OutputDirectory,
    [string]$CompilerPath,
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$ExpectedVersion,
    [string]$ExpectedCommit,
    [string]$IconPath,
    [string]$WebViewBootstrap,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$bundle = (Resolve-Path -LiteralPath $BundlePath).Path.TrimEnd('\')
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$executable = Join-Path $bundle "NexPointERP.exe"
$manifestPath = Join-Path $bundle "_internal\build-manifest.json"
$checksumPath = Join-Path $bundle "SHA256SUMS.txt"
foreach ($required in @($executable, $manifestPath, $checksumPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Bundle incompleto: executavel, manifesto e SHA256SUMS.txt sao obrigatorios."
    }
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or $manifest.environment -ne "production" -or
    $manifest.channel -ne "PROD" -or $manifest.supabase_project_ref -ne "scfncgaiovztrbgrcvkt" -or
    $manifest.commit -cnotmatch '^[0-9a-f]{40}$' -or
    $manifest.build -cne ("PROD-" + $manifest.commit.Substring(0, 12))) {
    throw "Manifesto de build PROD invalido."
}
$version = [string]$manifest.version
if ($version -notmatch '^(\d+)\.(\d+)\.(\d+)(?:(?:a|b|rc)(\d+))?$') {
    throw "Versao nao suportada; use major.minor.patch com pre-release opcional a, b ou rc."
}
$revision = if ($Matches[4]) { [int]$Matches[4] } else { 0 }
$versionParts = @([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], $revision)
if (@($versionParts | Where-Object { $_ -gt 65535 }).Count -gt 0) {
    throw "Versao excede os limites do recurso de versao Windows."
}
$windowsVersion = $versionParts -join '.'
if (($ExpectedVersion -and $version -cne $ExpectedVersion) -or
    ($ExpectedCommit -and $manifest.commit -cne $ExpectedCommit)) {
    throw "A versao ou o commit do bundle difere da entrega solicitada."
}
$exeVersion = (Get-Item -LiteralPath $executable).VersionInfo.ProductVersion
if ($exeVersion -cne $version) {
    throw "A versao do executavel difere do manifesto; gere novamente o bundle."
}

$allItems = @(Get-ChildItem -LiteralPath $bundle -Recurse -Force)
if (((Get-Item -LiteralPath $bundle).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    @($allItems | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -gt 0) {
    throw "O bundle nao pode conter links, junctions ou reparse points."
}
$files = @($allItems | Where-Object { -not $_.PSIsContainer })
$forbidden = @($allItems | Where-Object {
    $_.Name -like '.env*' -or $_.Name -in @('Cookies', 'Login Data', 'Local State', 'History', 'Web Data') -or
    $_.Name -match '(?i)\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm|journal))?$' -or
    $_.Extension -in @('.dpapi', '.log', '.pem', '.key', '.pfx', '.p12', '.bak', '.session') -or
    ($_.PSIsContainer -and $_.Name -in @('credentials', 'backups', 'logs', '.git', '.venv', 'EBWebView', 'browser-profile'))
})
if ($forbidden.Count -gt 0) { throw "O bundle contem arquivos locais ou privados proibidos." }

$listedFiles = @{}
foreach ($line in Get-Content -LiteralPath $checksumPath -Encoding UTF8) {
    if ($line -cnotmatch '^([0-9a-f]{64})  (.+)$') { throw "SHA256SUMS.txt invalido." }
    $expectedHash = $Matches[1]
    $relative = $Matches[2]
    if ($relative -match '(^/|\\|:|(^|/)\.\.?(/|$))' -or $relative -ceq 'SHA256SUMS.txt' -or
        $listedFiles.ContainsKey($relative)) { throw "Caminho ou duplicidade invalida em SHA256SUMS.txt." }
    $filePath = Join-Path $bundle $relative
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expectedHash) {
        throw "O bundle nao corresponde ao inventario de hashes aprovado."
    }
    $listedFiles[$relative] = $true
}
if ($listedFiles.Count -ne ($files.Count - 1)) { throw "O bundle contem arquivos nao inventariados." }
foreach ($file in $files) {
    $relative = $file.FullName.Substring($bundle.Length + 1).Replace('\', '/')
    if ($relative -cne 'SHA256SUMS.txt' -and -not $listedFiles.ContainsKey($relative)) {
        throw "O bundle contem arquivos nao inventariados."
    }
}

& $pythonPath (Join-Path $PSScriptRoot "verify_distribution_secrets.py") --distribution $bundle
if ($LASTEXITCODE -ne 0) { throw "O secret scan bloqueou o instalador." }
if ($ValidateOnly) {
    [ordered]@{ validated = $true; version = $version; commit = $manifest.commit; files = $files.Count } | ConvertTo-Json
    return
}

if (-not $CompilerPath) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe')
    )
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $CompilerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $CompilerPath) { throw "Inno Setup ausente no PC de build. Instale o compilador oficial de https://jrsoftware.org/isdl.php." }
$compiler = (Resolve-Path -LiteralPath $CompilerPath).Path
$signature = Get-AuthenticodeSignature -LiteralPath $compiler
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(Pyrsys B\.V\.|Martijn Laan)') {
    throw "O compilador nao possui assinatura Authenticode valida do fornecedor oficial."
}
if (-not $WebViewBootstrap) {
    $WebViewBootstrap = Join-Path $projectRoot 'artifacts\windows-prerequisites\MicrosoftEdgeWebview2Setup.exe'
}
if (-not (Test-Path -LiteralPath $WebViewBootstrap -PathType Leaf)) {
    throw "Forneca -WebViewBootstrap com o bootstrapper Evergreen oficial da Microsoft (https://go.microsoft.com/fwlink/p/?LinkId=2124703)."
}
$webViewInstaller = (Resolve-Path -LiteralPath $WebViewBootstrap).Path
$webViewSignature = Get-AuthenticodeSignature -LiteralPath $webViewInstaller
if ($webViewSignature.Status -ne 'Valid' -or $webViewSignature.SignerCertificate.Subject -notmatch '(^|, )O=Microsoft Corporation(,|$)') {
    throw "O componente WebView2 nao possui assinatura valida da Microsoft."
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot ("artifacts\windows-installer\{0}-{1}" -f $version, $manifest.commit.Substring(0, 12))
}
$output = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
$dataDirectory = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'NexPoint\ERP')).TrimEnd('\')
foreach ($protected in @($bundle, $dataDirectory)) {
    if ($output.Equals($protected, [StringComparison]::OrdinalIgnoreCase) -or
        $output.StartsWith($protected + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "A saida nao pode estar dentro do bundle ou dos dados reais."
    }
}
$installer = Join-Path $output 'NexPointERP-Setup.exe'
if (Test-Path -LiteralPath $installer) { throw "A saida ja possui um instalador. Escolha outro diretorio para preservar a entrega anterior." }
New-Item -ItemType Directory -Force -Path $output | Out-Null
$compilerArgs = @(
    '/Qp', "/DBundleDir=$bundle", "/DAppVersion=$version", "/DWindowsVersion=$windowsVersion",
    "/DInstallerOutputDir=$output", "/DWebViewBootstrap=$webViewInstaller"
)
if ($IconPath) {
    $icon = (Resolve-Path -LiteralPath $IconPath).Path
    if ([IO.Path]::GetExtension($icon) -ine '.ico') { throw "IconPath deve ser um icone .ico oficial." }
    $compilerArgs += "/DAppIcon=$icon"
}
& $compiler @compilerArgs (Join-Path $projectRoot 'packaging\nexpoint_erp.iss')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "O compilador nao gerou o instalador esperado."
}
& $pythonPath (Join-Path $PSScriptRoot "verify_distribution_secrets.py") --distribution $output
if ($LASTEXITCODE -ne 0) { throw "O secret scan da saida bloqueou a entrega." }
$installerHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$report = [ordered]@{
    installer = $installer
    installer_sha256 = $installerHash
    version = $version
    commit = $manifest.commit
    build = $manifest.build
    environment = 'production'
    channel = 'PROD'
    bundle_files = $files.Count
    scope = 'current-user'
    data_preserved = '%LOCALAPPDATA%\NexPoint\ERP'
    compiler_sha256 = (Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash.ToLowerInvariant()
    webview_bootstrap_sha256 = (Get-FileHash -LiteralPath $webViewInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
    signing_status = [string](Get-AuthenticodeSignature -LiteralPath $installer).Status
}
$report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'installer-build.json') -Encoding UTF8
"$installerHash  NexPointERP-Setup.exe" | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
$report | ConvertTo-Json
