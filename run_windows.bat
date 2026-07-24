@echo off
REM Quick start for real Windows usage (no API key required)
cd /d %~dp0
if not exist venv (
  python -m venv venv
)
call venv\Scripts\activate.bat
pip install -r requirements.txt
echo.
echo Starting agent in REAL + TEXT mode (rules planner, no API key)...
echo Use --real without --text for mic mode after voice deps work.
python -m voice_agent.cli --real --text --planner auto
