@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  ue5agent - 输入任务后回车，输入 exit 退出
echo  问编辑器相关问题前，请先打开 UE 工程
echo ============================================
uv run ue5agent chat
pause
