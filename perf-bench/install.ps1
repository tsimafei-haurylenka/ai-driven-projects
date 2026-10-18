# Install pb.cmd / pb.ps1 shims for perf-bench (Windows).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = if ($args[0]) { $args[0] } else { "$env:LOCALAPPDATA\Programs\perf-bench\bin" }
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Set-Content -Path "$bin\pb.cmd" -Value "@py `"$here\bench.py`" %*"
Set-Content -Path "$bin\pb-summarize.cmd" -Value "@py `"$here\summarize.py`" %*"
Write-Host "installed shims to $bin"
if (-not ($env:Path -split ';' | Where-Object { $_ -eq $bin })) {
  Write-Host "add to PATH:  [Environment]::SetEnvironmentVariable(`"`$env:Path;`$bin`",`"User`")"
}
if (-not (Test-Path "$here\presets\targets.json")) { Copy-Item "$here\presets\targets.example.json" "$here\presets\targets.json" }
Write-Host "edit $here\presets\targets.json and $here\perf-bench.json to point at your servers"
