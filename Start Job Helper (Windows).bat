@echo off
title Job Helper
cd /d "%~dp0"
echo.
echo  Starting Job Helper... (the first start takes a few minutes)
echo  Keep this black window open while you use Job Helper. Close it when you're done.
echo.
where uv >nul 2>nul
if errorlevel 1 (
  echo  Installing helper tool (one time only)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
uv run --python 3.12 python -m jobbot.firstrun || goto :error
uv run --python 3.12 streamlit run app.py --server.headless false --browser.gatherUsageStats false --theme.base light || goto :error
goto :eof
:error
echo.
echo  Something went wrong. Take a screenshot of this window and send it to whoever gave you Job Helper.
pause
