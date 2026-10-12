@echo off
rem pip install -> pytest -> PyInstaller (single exe). Output: dist\ApprovalReport.exe
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv 2>nul || uv venv --seed --python 3.12 .venv || goto :error
)
set PY=.venv\Scripts\python.exe

%PY% -m pip install -r requirements.txt || goto :error
%PY% -m pytest -q || goto :error
%PY% -m PyInstaller --noconfirm --clean build.spec || goto :error

echo.
echo Build complete: dist\ApprovalReport.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
