[CmdletBinding()]
param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot
$officialProjectRef = "scfncgaiovztrbgrcvkt"

$insideCheckout = (& git rev-parse --show-toplevel 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or -not $insideCheckout) {
    throw "A build PROD exige um checkout Git valido."
}
if ((Resolve-Path -LiteralPath $insideCheckout).Path -ne $projectRoot) {
    throw "A build PROD foi iniciada fora do checkout oficial."
}
$worktreeStatus = (& git status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $worktreeStatus) {
    throw "A build PROD exige checkout limpo, incluindo arquivos nao rastreados."
}

$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$version = (& $pythonPath -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
$commit = (& git rev-parse --verify "HEAD^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{40}$') {
    throw "Nao foi possivel determinar o commit Git da build."
}
$headCommit = (& git show -s --format=%H HEAD).Trim()
$objectType = (& git cat-file -t $commit).Trim()
if ($LASTEXITCODE -ne 0 -or $headCommit -ne $commit -or $objectType -ne "commit") {
    throw "HEAD nao aponta para um commit Git real."
}

if (-not $SkipTests) {
    & $pythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "A suite falhou; a build PROD foi bloqueada." }
}

& $pythonPath -c "import PyInstaller; print(PyInstaller.__version__)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Instale as dependencias de requirements/windows-build.lock antes da build."
}
& $pythonPath -m pip check | Out-Null
if ($LASTEXITCODE -ne 0) { throw "O ambiente de build possui dependencias inconsistentes." }

$outputRoot = Join-Path $projectRoot "artifacts\windows-build"
$manifestPath = Join-Path $outputRoot "build-manifest.json"
$distRoot = Join-Path $outputRoot "dist"
$workRoot = Join-Path $outputRoot "work"
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

$manifest = [ordered]@{
    schema_version = 1
    version = $version
    build = ("PROD-{0}" -f $commit.Substring(0, 12))
    commit = $commit
    environment = "production"
    channel = "PROD"
    supabase_project_ref = $officialProjectRef
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$env:NEXPOINT_BUILD_MANIFEST = $manifestPath
try {
    & $pythonPath -m PyInstaller --noconfirm --clean `
        --distpath $distRoot --workpath $workRoot `
        "packaging\nexpoint_erp.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller encerrou com erro." }
}
finally {
    Remove-Item Env:\NEXPOINT_BUILD_MANIFEST -ErrorAction SilentlyContinue
}

$bundle = Join-Path $distRoot "NexPointERP"
$executable = Join-Path $bundle "NexPointERP.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Executavel esperado nao foi gerado."
}

$forbiddenNames = @(
    ".env", ".env.local", "erp.sqlite3", "control_center.sqlite3",
    "demo_2_anos.sqlite3", "qa.sqlite3"
)
$unexpected = Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object {
    $forbiddenNames -contains $_.Name -or $_.Extension -in @(".db", ".sqlite", ".sqlite3", ".log")
}
if ($unexpected) {
    throw "A build contem arquivo local proibido."
}

& $pythonPath "scripts\verify_distribution_secrets.py" --distribution $bundle
if ($LASTEXITCODE -ne 0) { throw "A verificacao de secrets bloqueou a build." }

$checksums = Get-ChildItem -LiteralPath $bundle -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($bundle.Length + 1).Replace("\", "/")
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$checksums | Set-Content -LiteralPath (Join-Path $bundle "SHA256SUMS.txt") -Encoding ascii

$archive = Join-Path $outputRoot ("NexPointERP-{0}-PROD-{1}.zip" -f $version, $commit.Substring(0, 12))
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
Compress-Archive -LiteralPath $bundle -DestinationPath $archive -CompressionLevel Optimal
$archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()

[ordered]@{
    executable = $executable
    archive = $archive
    archive_sha256 = $archiveHash
    version = $version
    commit = $commit
    environment = "production"
    channel = "PROD"
} | ConvertTo-Json
