@echo off
echo ============================================
echo   Healer — AI Self-Healing Incident Assistant
echo ============================================

:: Load .env if it exists
if exist .env (
    for /f "tokens=1,2 delims==" %%a in (.env) do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set %%a=%%b
    )
)

if "%ANTHROPIC_API_KEY%"=="" (
    echo ERROR: ANTHROPIC_API_KEY is not set.
    echo Copy .env.example to .env and add your key.
    pause
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt --quiet

echo.
echo Starting Healer backend on http://localhost:8000
echo Dashboard: http://localhost:8000
echo API docs:  http://localhost:8000/docs
echo.
echo Make sure the Spring Boot target is running on http://localhost:8080
echo.

cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
