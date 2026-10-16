@echo off
rem Lanceur Windows (cmd.exe) : exécute CCTransfert depuis ce dépôt, sans installation.
setlocal
set "CCT_ROOT=%~dp0.."
if defined PYTHONPATH (set "PYTHONPATH=%CCT_ROOT%;%PYTHONPATH%") else (set "PYTHONPATH=%CCT_ROOT%")
set PYTHONUTF8=1
where py >nul 2>nul
if %ERRORLEVEL%==0 goto use_py
where python >nul 2>nul
if %ERRORLEVEL%==0 goto use_python
echo CCTransfert : Python 3.9+ introuvable (installez-le depuis python.org). 1>&2
exit /b 1
:use_py
py -3 -m cctransfert %*
exit /b %ERRORLEVEL%
:use_python
python -m cctransfert %*
exit /b %ERRORLEVEL%
