# JARVIS — one-line installer for Windows 10/11.
#   powershell -c "irm https://raw.githubusercontent.com/lfthkenzie-debug/JARVIS/main/install.ps1 | iex"
# or, from inside an existing checkout:  .\install.ps1
#
# Fetches the code, builds a private virtualenv, installs JARVIS core+web,
# trains the intent router. Everything runs on THIS machine, by you, for you.
$ErrorActionPreference = "Stop"

$Repo = if ($env:JARVIS_REPO) { $env:JARVIS_REPO } else { "https://github.com/lfthkenzie-debug/JARVIS.git" }
$Home_ = if ($env:JARVIS_HOME) { $env:JARVIS_HOME } else { Join-Path $env:USERPROFILE "JARVIS" }

Write-Host ""
Write-Host "  +------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |   J.A.R.V.I.S - Windows installer       |" -ForegroundColor Cyan
Write-Host "  +------------------------------------------+" -ForegroundColor Cyan

# -------------------------------------------------------------- prerequisites
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Host "  x Python not found. Install Python 3.10+ from python.org" -ForegroundColor Red
    Write-Host "    and tick 'Add python.exe to PATH', then re-run." -ForegroundColor Red
    exit 1
}
$ver = & $py.Source -c "import sys;print('%d.%d'%sys.version_info[:2])"
$major, $minor = $ver.Split(".")
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 9)) {
    Write-Host "  x JARVIS needs Python >= 3.9 (you have $ver)." -ForegroundColor Red
    exit 1
}
Write-Host "  > Python $ver OK" -ForegroundColor Green

# ------------------------------------------------------------------ fetch code
# When piped via 'irm | iex' the script has no own directory, so we clone/update
# the home checkout. When run as .\install.ps1 from inside a checkout, use it.
$Zip = if ($env:JARVIS_ZIP) { $env:JARVIS_ZIP } else { "" }
$here = if ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { "" }
if ($here -and (Test-Path (Join-Path $here "jarvis"))) {
    Set-Location $here
    Write-Host "  > Using existing checkout at $here" -ForegroundColor Green
} elseif (Test-Path (Join-Path $Home_ "jarvis")) {
    Set-Location $Home_
    Write-Host "  > Updating existing clone at $Home_" -ForegroundColor Green
    if (Get-Command git -ErrorAction SilentlyContinue) { git -C $Home_ pull --ff-only 2>$null }
} elseif ($Zip) {
    # Laptop-mirror mode: unpack a prebuilt bundle so testers get the CURRENT build
    # without waiting for a git push.
    Write-Host "  > Downloading installer bundle from $Zip" -ForegroundColor Green
    $parent = Split-Path -Parent $Home_
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $tmp = Join-Path $env:TEMP ("jarvis_" + [guid]::NewGuid().ToString() + ".zip")
    Invoke-WebRequest -Uri $Zip -OutFile $tmp
    if (Test-Path $Home_) { Remove-Item -Recurse -Force $Home_ }
    Expand-Archive -Path $tmp -DestinationPath $parent -Force
    Remove-Item -Force $tmp
    $rooted = Join-Path $parent "JARVIS"
    if ((Split-Path -Leaf $Home_) -ne "JARVIS" -and (Test-Path $rooted)) { Move-Item $rooted $Home_ }
    Set-Location $Home_
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "  x git not found. Install Git for Windows, or download the ZIP" -ForegroundColor Red
        Write-Host "    and run install.bat instead." -ForegroundColor Red
        exit 1
    }
    Write-Host "  > Cloning $Repo -> $Home_" -ForegroundColor Green
    git clone --depth 1 $Repo $Home_
    Set-Location $Home_
}

# ------------------------------------------------------- virtualenv + install
if (-not (Test-Path ".venv")) {
    Write-Host "  > Creating virtualenv .venv" -ForegroundColor Green
    & $py.Source -m venv .venv
}
Invoke-Expression (Join-Path ".venv\Scripts\activate.ps1")
python -m pip install -q --upgrade pip setuptools wheel

Write-Host "  > Installing JARVIS core + web tiers" -ForegroundColor Green
python -m pip install -r requirements-core.txt -r requirements-web.txt

Write-Host "  > Training JARVIS intent models (first run takes a minute)" -ForegroundColor Green
python -c "from jarvis.main import _boot; _boot()"

Write-Host ""
Write-Host "  JARVIS is installed. Try:" -ForegroundColor Cyan
Write-Host "      .\run.bat --ui          holographic control panel" -ForegroundColor White
Write-Host "      .\run.bat --text        typed conversation" -ForegroundColor White
Write-Host "      .\run.bat               full voice mode (needs a microphone)" -ForegroundColor White
Write-Host "  Windows native voice uses PowerShell synthesis; for the Fish" -ForegroundColor DarkGray
Write-Host "  Audio 'JARVIS' voice put your key in jarvis_data\fish_api_key.txt." -ForegroundColor DarkGray
