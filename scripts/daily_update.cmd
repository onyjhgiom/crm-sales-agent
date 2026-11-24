@echo off
setlocal
cd /d "%~dp0.."
set "OPENAI_API_KEY="
set "LLM_MODE=offline"
set "USE_CHECKPOINTER=false"
python scripts\daily_update.py %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
