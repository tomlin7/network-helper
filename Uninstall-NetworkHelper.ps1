$ErrorActionPreference='Stop'
$backup=Join-Path $PSScriptRoot 'settings-backup.json'
if(-not(Test-Path -LiteralPath $backup)) { throw 'No settings backup found; nothing changed.' }
$saved=Get-Content -LiteralPath $backup -Raw | ConvertFrom-Json
foreach($entry in $saved.Environment.PSObject.Properties) { [Environment]::SetEnvironmentVariable($entry.Name,$entry.Value,'User') }
$registry='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
foreach($entry in $saved.Internet.PSObject.Properties) {
  if($null -eq $entry.Value) { Remove-ItemProperty -LiteralPath $registry -Name $entry.Name -ErrorAction SilentlyContinue }
  else { Set-ItemProperty -LiteralPath $registry -Name $entry.Name -Value $entry.Value }
}
$shortcut=Join-Path ([Environment]::GetFolderPath('Startup')) 'Network Helper.lnk'
if(Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut }
$listener=Get-NetTCPConnection -LocalPort 18888 -State Listen -ErrorAction SilentlyContinue
if($listener) {
  $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
  $expected=Join-Path $PSScriptRoot 'network_helper.py'
  if($process.CommandLine -and $process.CommandLine.Contains($expected)) { Stop-Process -Id $process.ProcessId }
}
& (Join-Path $PSScriptRoot 'Notify-Settings.ps1')
Write-Output 'Original user settings restored and sign-in startup removed. Restart existing apps to refresh inherited proxy variables. Helper files were preserved.'
