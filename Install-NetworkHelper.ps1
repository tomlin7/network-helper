param([switch]$WindowsProxy)
$ErrorActionPreference='Stop'
$registry='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$backup=Join-Path $PSScriptRoot 'settings-backup.json'
$names=@('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')
if(-not(Test-Path -LiteralPath $backup)) {
  $saved=[ordered]@{Environment=@{};Internet=@{}}
  foreach($name in $names) { $saved.Environment[$name]=[Environment]::GetEnvironmentVariable($name,'User') }
  $internet=Get-ItemProperty -LiteralPath $registry
  foreach($name in @('ProxyEnable','ProxyServer','ProxyOverride','AutoConfigURL')) { $saved.Internet[$name]=$internet.$name }
  $saved | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $backup -Encoding UTF8
}
& (Join-Path $PSScriptRoot 'Start-NetworkHelper.ps1')
if($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Helper did not start; settings were left untouched.' }
foreach($name in @('HTTP_PROXY','HTTPS_PROXY')) { [Environment]::SetEnvironmentVariable($name,'http://127.0.0.1:18888','User') }
$noProxy=[Environment]::GetEnvironmentVariable('NO_PROXY','User')
$noProxy=(@($noProxy -split ',')+@('localhost','127.0.0.1','::1') | Where-Object {$_} | Select-Object -Unique) -join ','
[Environment]::SetEnvironmentVariable('NO_PROXY',$noProxy,'User')
if($WindowsProxy) {
  Set-ItemProperty -LiteralPath $registry -Name ProxyEnable -Value 1
  Set-ItemProperty -LiteralPath $registry -Name ProxyServer -Value '127.0.0.1:18888'
  Set-ItemProperty -LiteralPath $registry -Name ProxyOverride -Value 'localhost;127.0.0.1;[::1];<local>'
}
$startup=[Environment]::GetFolderPath('Startup')
$shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup 'Network Helper.lnk'))
$shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $PSScriptRoot 'Start-NetworkHelper.ps1')+'"'
$shortcut.WorkingDirectory=$PSScriptRoot
$shortcut.WindowStyle=7
$shortcut.Description='Starts one bounded loopback proxy; no restart loop.'
$shortcut.Save()
& (Join-Path $PSScriptRoot 'Notify-Settings.ps1')
Write-Output 'Installed sign-in startup and user proxy variables. New apps inherit them; existing apps may need restarting. Original settings are backed up beside this script.'
