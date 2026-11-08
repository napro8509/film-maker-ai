# Set up a local film-maker-ai environment (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File setup_local.ps1
#   $env:TARGET="D:\dir"; powershell -ExecutionPolicy Bypass -File setup_local.ps1
$ErrorActionPreference = "Stop"

$Repo   = if ($env:REPO)   { $env:REPO }   else { "https://github.com/napro8509/film-maker-ai" }
$Branch = if ($env:BRANCH) { $env:BRANCH } else { "claude/relaxed-bohr-q25gvh" }
$Desktop = [Environment]::GetFolderPath("Desktop")
$Target = if ($env:TARGET) { $env:TARGET } else { Join-Path $Desktop "Projects\film-maker-ai" }

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!  $m" -ForegroundColor Yellow }
function Run($exe, [string[]]$a) { & $exe @a; if ($LASTEXITCODE -ne 0) { throw "$exe $($a -join ' ') failed ($LASTEXITCODE)" } }

Step "Kiểm tra công cụ cần có"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Cần cài git: https://git-scm.com/download/win" }
$Py = $null
foreach ($c in @("py -3", "python")) {
  $parts = $c.Split(" ")
  if (Get-Command $parts[0] -ErrorAction SilentlyContinue) {
    & $parts[0] @($parts[1..9] | Where-Object { $_ }) -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>$null
    if ($LASTEXITCODE -eq 0) { $Py = $parts; break }
  }
}
if (-not $Py) { throw "Cần Python 3.10+: https://www.python.org/downloads/" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Warn "Chưa có ffmpeg (cần cho bước ghép phim): winget install Gyan.FFmpeg" }

Step "Lấy code vào $Target"
if (Test-Path (Join-Path $Target ".git")) {
  Run git @("-C", $Target, "fetch", "origin", $Branch)
  Run git @("-C", $Target, "checkout", $Branch)
  Run git @("-C", $Target, "pull", "--ff-only", "origin", $Branch)
} else {
  New-Item -ItemType Directory -Force -Path (Split-Path $Target) | Out-Null
  Run git @("clone", "-b", $Branch, $Repo, $Target)
}
Set-Location $Target

Step "Tạo môi trường Python (.venv) và cài filmmaker"
if (-not (Test-Path ".venv")) { Run $Py[0] (@($Py[1..9] | Where-Object { $_ }) + @("-m", "venv", ".venv")) }
$VPy = Join-Path $Target ".venv\Scripts\python.exe"
Run $VPy @("-m", "pip", "install", "-q", "--upgrade", "pip")
Run $VPy @("-m", "pip", "install", "-q", "-e", ".[ai,dev]")

Step "Cài gflow-cli"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (Get-Command gflow -ErrorAction SilentlyContinue) { uv tool upgrade gflow-cli } else { Run uv @("tool", "install", "gflow-cli") }
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
uv tool update-shell 2>$null | Out-Null
uv tool run --from gflow-cli playwright install chromium
if ($LASTEXITCODE -ne 0) { Warn "Chưa tải được Chromium. Chạy lại sau: uv tool run --from gflow-cli playwright install chromium" }

Step "Chạy test (dùng gflow giả, không tốn credit)"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Run $VPy @("-m", "pytest", "-q") } else { Warn "Bỏ qua test vì chưa có ffmpeg" }

Step "Xong!"
Write-Host @"
Thư mục: $Target

Bước tiếp theo:
  cd "$Target"; .\.venv\Scripts\Activate.ps1
  gflow auth login --browser chrome      # đăng nhập Google (một lần)
  filmmaker check
  filmmaker template test.yaml; filmmaker run test.yaml --dry-run
"@
