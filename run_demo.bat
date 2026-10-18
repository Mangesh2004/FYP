@echo off
rem One live Webots episode with faults firing (blocked door at 15 s, commander radio cut at 30 s,
rem one robot powered off at 110 s, 10%% packet loss), plus the read-only dashboard in the browser.
rem   run_demo.bat              seed 3, CBBA
rem   run_demo.bat 7 greedy     other seed / method (static, greedy, cbba, central, learned)
rem   set FYP_MOVIE=D:\FYP\experiments\demo.mp4 before running to record a video
setlocal
set PY=C:\Users\mange\anaconda3\envs\pytorch\python.exe
set SIM=%~dp0webots_sim
set FYP_SEED=%1
if "%FYP_SEED%"=="" set FYP_SEED=3
set FYP_METHOD=%2
if "%FYP_METHOD%"=="" set FYP_METHOD=cbba
set FYP_CONDITION=demo
set FYP_BATCH=
set FYP_RUN_DIR=
set FYP_FAULT_DOOR=
set FYP_MAX_TIME=300
set FYP_SNAPSHOTS=10,40,120,200
cd /d "%SIM%"
rem Webots can save the running scene into the world on exit, so the demo runs a disposable copy
copy /y "%SIM%\worlds\facility.wbt" "%SIM%\worlds\_demo_facility.wbt" >nul
start "dashboard" /min "%PY%" -m dashboard.server --port 8765
timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8765
"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe" --mode=realtime "%SIM%\worlds\_demo_facility.wbt"
del "%SIM%\worlds\_demo_facility.wbt" >nul 2>&1
endlocal
