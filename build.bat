@echo off
rem pip install -> pytest -> PyInstaller (onedir, windowed). Output: dist\ApprovalReport\ApprovalReport.exe
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  py -3.11 -m venv .venv 2>nul || uv venv --seed --python 3.11 .venv || goto :error
)
set PY=.venv\Scripts\python.exe

%PY% -m pip install --upgrade PySide6 openpyxl pywin32 pyinstaller pytest || goto :error
%PY% -m pytest -q || goto :error
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --name ApprovalReport main.py || goto :error

echo.
echo Build complete: dist\ApprovalReport\ApprovalReport.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
