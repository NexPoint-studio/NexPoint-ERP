<#
.SYNOPSIS
Enables only the Windows Hyper-V feature for a disposable NexPoint QA VM.
.DESCRIPTION
Run explicitly from an elevated Windows PowerShell session. This script does
not reboot, create a VM or virtual switch, or modify firewall/network rules.
It is safe to rerun after an interrupted feature installation or reboot.
#>
#Requires -RunAsAdministrator
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$os = Get-CimInstance -ClassName Win32_OperatingSystem
if ($os.ProductType -ne 1 -or $os.Caption -notmatch 'Windows (10|11) Pro|Windows (10|11) Enterprise') {
    throw 'Hyper-V QA exige Windows 10/11 Pro ou Enterprise neste host.'
}

$cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
if (-not $cpu.VirtualizationFirmwareEnabled -and -not (Get-ComputerInfo).HyperVisorPresent) {
    throw 'A virtualizacao de hardware nao foi confirmada. Habilite-a no firmware antes de continuar.'
}

$feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V
switch ($feature.State.ToString()) {
    'Enabled' {
        Write-Output 'Hyper-V ja esta habilitado. Nenhuma alteracao foi feita.'
        return
    }
    'EnablePending' {
        Write-Output 'Hyper-V aguarda reinicio manual. Nenhuma alteracao foi feita.'
        return
    }
    'Disabled' {
        $result = Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All -NoRestart
        if ($result.RestartNeeded) {
            Write-Output 'Hyper-V habilitado. Reinicio manual necessario antes de criar a VM QA.'
        } else {
            Write-Output 'Hyper-V habilitado. Reinicio nao solicitado pelo Windows; confirme o estado do recurso.'
        }
        return
    }
    default {
        throw "Estado inesperado do recurso Hyper-V: $($feature.State). Nenhuma alteracao adicional foi feita."
    }
}
