@echo off
rem Starts the application so that a phone on the same Wi-Fi can open it.
rem (The usual start only answers on this computer.)
rem Close the window where the application already runs before starting this one.
cd /d "%~dp0"
echo.
echo   The application is starting. On your phone, on the same Wi-Fi, open the
echo   address shown on its "How it is working" page (System, "On your phone").
echo   If Windows asks, allow Python on private networks.
echo.
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
pause
