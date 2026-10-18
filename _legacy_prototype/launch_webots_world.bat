@echo off
title Webots 3D Launcher — 6-Building Industrial Complex (2-UGV Swarm)
echo ============================================================================
echo   Native Webots 3D World Launcher
echo   World File: D:\FYP\swarm_sim\webots\worlds\industrial_disaster_complex.wbt
echo   Controller Python: C:\Users\mange\anaconda3\envs\pytorch\python.exe
echo ============================================================================

if exist "C:\Program Files\Webots\msys64\mingw64\bin\webots.exe" (
    echo Launching Webots 3D Desktop Simulator...
    start "" "C:\Program Files\Webots\msys64\mingw64\bin\webots.exe" "%~dp0swarm_sim\webots\worlds\industrial_disaster_complex.wbt"
    exit /b 0
)

if exist "%LOCALAPPDATA%\Programs\Webots\msys64\mingw64\bin\webots.exe" (
    echo Launching Webots 3D Desktop Simulator...
    start "" "%LOCALAPPDATA%\Programs\Webots\msys64\mingw64\bin\webots.exe" "%~dp0swarm_sim\webots\worlds\industrial_disaster_complex.wbt"
    exit /b 0
)

echo Webots Desktop is not installed yet at the default path.
echo Opening official Cyberbotics Webots R2023b Windows installer download...
start "" "https://github.com/cyberbotics/webots/releases/download/R2023b/webots-R2023b_setup.exe"
echo Once installed, double-click this script again or open:
echo   %~dp0swarm_sim\webots\worlds\industrial_disaster_complex.wbt
pause
