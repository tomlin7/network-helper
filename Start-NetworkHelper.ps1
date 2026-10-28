param([string]$Python=$env:NETWORK_HELPER_PYTHON,[switch]$ResolvePythonOnly)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Net.Http
function Find-Python {
  if($Python) {
    if(-not [IO.Path]::IsPathRooted($Python) -or -not(Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Provide an absolute path to a real Python interpreter.' }
    return $Python
  }
  $candidates=@()
  if($env:APPDATA) {
    $uvRoot=Join-Path $env:APPDATA 'uv/python'
    $candidates+=@(Get-ChildItem -LiteralPath $uvRoot -Directory -ErrorAction SilentlyContinue | Where-Object Name -like 'cpython-*' | Sort-Object Name -Descending | ForEach-Object {Join-Path $_.FullName 'python.exe'})
  }
  $command=Get-Command python.exe -ErrorAction SilentlyContinue
  if($command -and $command.Source -notmatch '[\\/]WindowsApps[\\/]') { $candidates+=$command.Source }
  if($env:LOCALAPPDATA) {
    $root=Join-Path $env:LOCALAPPDATA 'Programs/Python'
    $candidates+=@(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Where-Object Name -like 'Python*' | Sort-Object Name -Descending | ForEach-Object {Join-Path $_.FullName 'python.exe'})
  }
  $found=$candidates | Where-Object {Test-Path -LiteralPath $_ -PathType Leaf} | Select-Object -First 1
  if(-not $found) { throw 'Install Python 3.10+ or set NETWORK_HELPER_PYTHON to its full python.exe path.' }
  return $found
}
if($ResolvePythonOnly) { Write-Output (Find-Python); exit 0 }
function Get-HelperHealth {
  $handler=New-Object System.Net.Http.HttpClientHandler
  $handler.UseProxy=$false
  $client=New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout=[TimeSpan]::FromSeconds(2)
  try { return ($client.GetStringAsync('http://127.0.0.1:18888/health').GetAwaiter().GetResult() | ConvertFrom-Json) }
  catch { return $null }
  finally { $client.Dispose() }
}
$health=Get-HelperHealth
if($health -and $health.service -in @('network-helper-v1','bit-network-helper-v1')) { Write-Output "Network helper already running (PID $($health.pid))."; exit 0 }
$listener=Get-NetTCPConnection -LocalPort 18888 -State Listen -ErrorAction SilentlyContinue
if($listener) { throw 'Port 18888 belongs to another service. It was left untouched.' }
$Python=Find-Python
$helper=Join-Path $PSScriptRoot 'network_helper.py'
$process=Start-Process -FilePath $Python -ArgumentList @('-u',('"'+$helper+'"')) -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -PassThru
for($attempt=0;$attempt -lt 20;$attempt++) {
  Start-Sleep -Milliseconds 200
  $health=Get-HelperHealth
  if($health -and $health.service -in @('network-helper-v1','bit-network-helper-v1') -and $health.pid -eq $process.Id) { Write-Output "Network helper running (PID $($process.Id)); port 18888; 256 MiB hard RAM cap; 32 connections."; exit 0 }
  if($process.HasExited) { throw 'Helper exited. See network-helper.log; no restart loop was started.' }
}
if(-not $process.HasExited) { Stop-Process -Id $process.Id }
throw 'Helper failed readiness and was stopped. See network-helper.log.'
