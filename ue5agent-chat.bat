@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  ue5agent chat
echo  Type a task and press Enter. Type 'exit' to quit.
echo  Open the UE project first for editor questions.
echo ============================================
uv run ue5agent chat
pause
