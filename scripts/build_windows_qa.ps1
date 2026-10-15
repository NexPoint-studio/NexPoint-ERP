[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('A', 'B')][string]$Build,
    [string]$Python = '.\.venv\Scripts\python.exe',
    [string]$ActivationProfilePath,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ActivationProfilePath) { throw 'Informe o perfil publico de ativacao do laboratorio QA.' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $root
$status = @(git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $status.Count -ne 0) { throw 'Build QA exige checkout limpo.' }
$commit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Commit QA invalido.' }
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
if (-not $SkipTests) {
    & $pythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Suite QA falhou.' }
}
& $pythonPath -m pip check | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Dependencias do build inconsistentes.' }
& $pythonPath -c 'import PyInstaller'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller ausente.' }

$version = if ($Build -eq 'A') { '1.1.0' } else { '1.1.2' }
$base = Join-Path $root ('artifacts\windows-qa-build\' + $Build + '-' + $commit.Substring(0, 12))
if (Test-Path -LiteralPath $base) { throw 'Build QA existente. Preserve a evidencia anterior.' }
$stage = Join-Path $base 'source'
$dist = Join-Path $base 'dist'
$work = Join-Path $base 'work'
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$allowed = @('app/', 'control_center/', 'templates/', 'static/', 'packaging/', 'run_desktop.py', 'run_updater.py')
$files = @(git ls-files --cached --full-name)
if ($LASTEXITCODE -ne 0) { throw 'Inventario Git indisponivel.' }
foreach ($relative in $files) {
    $normal = $relative.Replace('\', '/')
    $selected = $false
    foreach ($prefix in $allowed) {
        if ($prefix.EndsWith('/') -and $normal.StartsWith($prefix, [StringComparison]::Ordinal)) { $selected = $true; break }
        if ($normal -ceq $prefix) { $selected = $true; break }
    }
    if (-not $selected) { continue }
    if ($normal -match '(^|/)(?:\.env[^/]*|__pycache__|\.pytest_cache)(/|$)' -or
        $normal -match '(?i)\.(?:sqlite3?|db|dpapi|key|pem|pfx|p12|log|bak)$') {
        throw 'Arquivo proibido no inventario de build QA.'
    }
    $destination = Join-Path $stage $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $root $relative) -Destination $destination
}
$target = Join-Path $stage 'app\core\_build_target.py'
if (-not (Test-Path -LiteralPath $target)) { throw 'Perfil de destino nao esta versionado.' }
@'
"""Pinned destination for a disposable, host-only QA package."""
ENVIRONMENT = "qa"
CHANNEL = "QA"
PROJECT_REF = "nexpointqaisolatelab"
ORIGIN = "https://qa.nexpoint.test:56430"
UPDATE_CHANNEL = "internal"
PRODUCT_DIRECTORY = "ERP-QA"
'@ | Set-Content -LiteralPath $target -Encoding UTF8

$versionInfo = Join-Path $stage 'packaging\windows_version_info.txt'
$versionText = Get-Content -LiteralPath $versionInfo -Raw
$versionText = $versionText -replace '1, 1, \d+, 0', ('1, 1, ' + $(if ($Build -eq 'A') { '0' } else { '2' }) + ', 0')
$versionText = ($versionText -replace '1\.1\.\d+', $version).Replace('NexPoint ERP', 'NexPoint ERP QA')
Set-Content -LiteralPath $versionInfo -Value $versionText -Encoding UTF8
$manifestPath = Join-Path $base 'build-manifest.json'
$manifest = [ordered]@{
    schema_version = 1; version = $version; build = ('QA-' + $Build + '-' + $commit.Substring(0, 12))
    commit = $commit; environment = 'qa'; channel = 'QA'
    supabase_project_ref = 'nexpointqaisolatelab'
    cloud_origin = 'https://qa.nexpoint.test:56430'; update_channel = 'internal'
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$env:NEXPOINT_BUILD_MANIFEST = $manifestPath
try {
    & $pythonPath -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work (Join-Path $stage 'packaging\nexpoint_erp.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller ERP QA falhou.' }
    & $pythonPath -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work (Join-Path $stage 'packaging\nexpoint_updater.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller Updater QA falhou.' }
}
finally { Remove-Item Env:\NEXPOINT_BUILD_MANIFEST -ErrorAction SilentlyContinue }
$bundle = Join-Path $dist 'NexPointERP'
$exe = Join-Path $bundle 'NexPointERP.exe'
$helper = Join-Path $dist 'NexPointUpdater.exe'
if (-not (Test-Path -LiteralPath $exe) -or -not (Test-Path -LiteralPath $helper)) { throw 'Executaveis QA incompletos.' }
Copy-Item -LiteralPath $helper -Destination (Join-Path $bundle 'NexPointUpdater.exe')
& $helper --verify-runtime
if ($LASTEXITCODE -ne 0) { throw 'Updater QA nao iniciou.' }
if ($ActivationProfilePath) {
    $profile = (Resolve-Path -LiteralPath $ActivationProfilePath).Path
    & $pythonPath -c 'import sys; from pathlib import Path; from app.services.installation_activation import ActivationProfile; p=ActivationProfile.load(Path(sys.argv[1])); assert (p.tenant_key,p.installation_key)==tuple(sys.argv[2:4])' $profile 'qa-nexpoint-lab' 'qa-win-clean-04'
    if ($LASTEXITCODE -ne 0) { throw 'Perfil publico QA invalido.' }
    Copy-Item -LiteralPath $profile -Destination (Join-Path $bundle 'activation-profile.json')
}
& $pythonPath (Join-Path $root 'scripts\verify_distribution_secrets.py') --distribution $bundle
if ($LASTEXITCODE -ne 0) { throw 'Secret scan do bundle QA falhou.' }
& $pythonPath (Join-Path $root 'tools\qa\verify_qa_distribution.py') $bundle
if ($LASTEXITCODE -ne 0) { throw 'Boundary QA do bundle falhou.' }
$checksums = Get-ChildItem -LiteralPath $bundle -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($bundle.Length + 1).Replace('\', '/')
    (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant() + '  ' + $relative
}
$checksums | Set-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS.txt') -Encoding ascii
[ordered]@{ bundle = $bundle; version = $version; build = $manifest.build; commit = $commit; environment = 'qa'; channel = 'QA' } | ConvertTo-Json
