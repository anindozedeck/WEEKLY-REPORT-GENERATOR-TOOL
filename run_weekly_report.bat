@echo off
cd /d "%~dp0"
py -3 Weekly_Report_Generator.py
exit /b %errorlevel%
