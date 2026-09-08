@echo off
cd /d "%~dp0"
start "" http://localhost:8077
python server.py
