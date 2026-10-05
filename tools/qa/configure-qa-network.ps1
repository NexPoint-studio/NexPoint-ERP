<#
.SYNOPSIS
Restricts the local Supabase QA port range to the NexPoint host-only VM.
.DESCRIPTION
Run explicitly in elevated Windows PowerShell after the Hyper-V QA switch and
VM exist. No routing, NAT, DNS, sharing, or physical adapter settings are changed.
Rerun after adding/removing host adapters; existing rules are never widened.
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$switchName = 'NexPoint QA HostOnly'
$adapterName = 'vEthernet (NexPoint QA HostOnly)'
$vmName = 'NexPoint-Windows-QA'
$hostAddress = '10.254.241.1'
$vmAddress = '10.254.241.2'
$gatewayPort = '56430'
$allQaPorts = '56420-56430'

$switch = Get-VMSwitch -Name $switchName -ErrorAction Stop
if ($switch.SwitchType -ne 'Internal') { throw 'QA switch is not internal.' }
$attached = @(Get-VMNetworkAdapter -All | Where-Object {
    $_.VMName -and $_.SwitchName -eq $switchName
})
if ($attached.Count -ne 1 -or $attached[0].VMName -ne $vmName) {
    throw 'The QA switch must connect only to the named disposable VM.'
}
$adapter = Get-NetAdapter -Name $adapterName -ErrorAction Stop
$profiles = @(Get-NetFirewallProfile -ErrorAction Stop)
if (@($profiles | Where-Object { -not $_.Enabled }).Count) {
    throw 'Every Windows Firewall profile must be enabled before QA networking.'
}
$existingAddresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '169.254.*' })
if ($existingAddresses.Count -gt 1 -or
    ($existingAddresses.Count -eq 1 -and
        ($existingAddresses[0].IPAddress -ne $hostAddress -or $existingAddresses[0].PrefixLength -ne 28))) {
    throw 'The QA adapter has an unexpected IPv4 address; no network changes were made.'
}

function Ensure-QaRule {
    param(
        [string]$Name,
        [string]$Alias,
        [ValidateSet('Allow', 'Block')][string]$Action,
        [string[]]$Ports,
        [string]$RemoteAddress = 'Any',
        [string[]]$LegacyPorts = @()
    )
    $rule = Get-NetFirewallRule -Name $Name -ErrorAction SilentlyContinue
    if ($rule) {
        $portFilter = $rule | Get-NetFirewallPortFilter
        $interfaceFilter = $rule | Get-NetFirewallInterfaceFilter
        $addressFilter = $rule | Get-NetFirewallAddressFilter
        $expectedRemoteForms = @($RemoteAddress)
        if ($RemoteAddress -ne 'Any') {
            $expectedRemoteForms += "$RemoteAddress-$RemoteAddress"
        }
        $currentPorts = @($portFilter.LocalPort) -join ','
        $legacy = $LegacyPorts.Count -gt 0 -and
            $currentPorts -eq ($LegacyPorts -join ',')
        if ($rule.Direction -ne 'Inbound' -or $rule.Action -ne $Action -or
            $rule.Enabled -ne 'True' -or $portFilter.Protocol -ne 'TCP' -or
            ($currentPorts -ne ($Ports -join ',') -and -not $legacy) -or
            (@($interfaceFilter.InterfaceAlias) -join ',') -ne $Alias -or
            (@($addressFilter.RemoteAddress) -join ',') -notin $expectedRemoteForms) {
            throw "Existing QA firewall rule has unexpected scope: $Name"
        }
        if (-not $legacy) { return }
        Remove-NetFirewallRule -Name $Name
    }
    New-NetFirewallRule -Name $Name -DisplayName $Name -Direction Inbound `
        -Action $Action -Enabled True -Profile Any -Protocol TCP `
        -LocalPort $Ports -InterfaceAlias $Alias -RemoteAddress $RemoteAddress |
        Out-Null
}

# Prepare block rules before activating an address reachable by the VM.
$otherAdapters = @(Get-NetAdapter | Where-Object {
    $_.Status -eq 'Up' -and $_.Name -ne $adapterName
})
if (-not $otherAdapters.Count) { throw 'No non-QA adapters found; audit the host first.' }
foreach ($other in $otherAdapters) {
    $ruleName = 'NexPoint-QA-Block-Adapter-' + $other.ifIndex
    Ensure-QaRule -Name $ruleName -Alias $other.Name -Action Block `
        -Ports $allQaPorts -LegacyPorts @('56420-56429')
}
Ensure-QaRule -Name 'NexPoint-QA-Block-Internal' -Alias $adapterName `
    -Action Block -Ports @('56420-56429') `
    -LegacyPorts @('56420', '56422-56429')
Ensure-QaRule -Name 'NexPoint-QA-Allow-Gateway' -Alias $adapterName `
    -Action Allow -Ports $gatewayPort -RemoteAddress $vmAddress `
    -LegacyPorts @('56421')

if (-not $existingAddresses.Count) {
    New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress $hostAddress `
        -PrefixLength 28 -AddressFamily IPv4 | Out-Null
}
$current = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 |
    Where-Object IPAddress -eq $hostAddress
if (-not $current -or $current.PrefixLength -ne 28) {
    throw 'The host-only QA address was not confirmed.'
}
Write-Output 'QA host-only address and scoped firewall rules verified.'
Write-Output 'The guest must use 10.254.241.2/28 with no default gateway or DNS server.'
Write-Output 'Supabase QA remains stopped until TLS, credentials, and listener checks pass.'
