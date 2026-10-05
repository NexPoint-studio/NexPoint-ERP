#Requires -RunAsAdministrator
[CmdletBinding()]
param([switch]$Restore)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$caddy = Join-Path $root 'artifacts\qa-tools\caddy-2.11.7\caddy.exe'
$stateFile = Join-Path $root 'artifacts\windows-qa-vm\caddy-firewall-state.json'
$qaInterface = 'vEthernet (NexPoint QA HostOnly)'
$qaRule = Get-NetFirewallRule -Name 'NexPoint-QA-Allow-Gateway' -ErrorAction Stop
$qaPort = $qaRule | Get-NetFirewallPortFilter
$qaAddress = $qaRule | Get-NetFirewallAddressFilter
$qaAdapter = $qaRule | Get-NetFirewallInterfaceFilter
$currentRemote = @($qaAddress.RemoteAddress) -join ','
if ($qaRule.Action -ne 'Allow' -or $qaRule.Enabled -ne 'True' -or
    $qaPort.Protocol -ne 'TCP' -or $qaPort.LocalPort -ne '56430' -or
    $currentRemote -notin @('10.254.241.2', '10.254.241.2-10.254.241.2') -or
    (@($qaAdapter.InterfaceAlias) -join ',') -ne $qaInterface) {
    throw 'The scoped QA gateway firewall rule is missing or unexpected.'
}
$public = Get-NetFirewallProfile -Name Public
if (-not $public.Enabled -or $public.DefaultInboundAction -notin @('Block', 'NotConfigured')) {
    throw 'Public inbound default protection is unavailable.'
}
$listener = @(Get-NetTCPConnection -LocalAddress '10.254.241.1' -LocalPort 56430 -State Listen -ErrorAction Stop)
if ($listener.Count -ne 1 -or (Get-Process -Id $listener[0].OwningProcess).Path -ine $caddy) {
    throw 'The QA Caddy process is not the sole gateway listener.'
}
$blocks = @(Get-NetFirewallRule -PolicyStore PersistentStore -Action Block -Direction Inbound |
    Where-Object {
        $program = ($_ | Get-NetFirewallApplicationFilter).Program
        $port = $_ | Get-NetFirewallPortFilter
        $_.Profile -eq 'Public' -and $program -ieq $caddy -and
        $port.Protocol -eq 'TCP' -and $port.LocalPort -eq 'Any'
    })
if ($blocks.Count -ne 1) { throw 'Expected one exact Public TCP Caddy block rule.' }
$block = $blocks[0]
if ($Restore) {
    if (-not (Test-Path -LiteralPath $stateFile -PathType Leaf)) {
        throw 'No saved QA firewall change exists.'
    }
    $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    if ($saved.rule_name -cne $block.Name -or $saved.program -ine $caddy) {
        throw 'The saved QA firewall rule does not match the current rule.'
    }
    Enable-NetFirewallRule -InputObject $block
    Write-Output 'The original Caddy Public TCP block rule was restored.'
    return
}
if ($block.Enabled -eq 'False') {
    Write-Output 'The exact QA Caddy Public TCP block rule is already disabled.'
    return
}
if ($block.Enabled -ne 'True' -or (Test-Path -LiteralPath $stateFile)) {
    throw 'The Caddy firewall state needs manual review.'
}
[ordered]@{rule_name=$block.Name;program=$caddy;original_enabled=$true} |
    ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8
try {
    Disable-NetFirewallRule -InputObject $block
    Write-Output 'Only the exact Public TCP Caddy block rule was disabled for QA.'
} catch {
    Remove-Item -LiteralPath $stateFile -ErrorAction SilentlyContinue
    throw
}
