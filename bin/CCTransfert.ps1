# Lanceur Windows (PowerShell) : exécute CCTransfert depuis ce dépôt, sans installation.
$root = Split-Path -Parent $PSScriptRoot
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$root;$env:PYTHONPATH" } else { $env:PYTHONPATH = $root }
$env:PYTHONUTF8 = "1"
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m cctransfert @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m cctransfert @args
} else {
    Write-Error "CCTransfert : Python 3.9+ introuvable (installez-le depuis python.org)."
    exit 1
}
exit $LASTEXITCODE
