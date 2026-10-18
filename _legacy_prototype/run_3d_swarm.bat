@echo off
title MUM-T Loyal Wingman 3D Decentralized Swarm Simulator (PyTorch Conda)
echo ============================================================================
echo   MUM-T / Loyal Wingman 3D Decentralized Swarm Simulator
echo   Using Conda Environment: C:\Users\mange\anaconda3\envs\pytorch
echo   Dashboard URL:           http://127.0.0.1:8765
echo ============================================================================
set PYTHONPATH=%~dp0
start "" http://127.0.0.1:8765
"C:\Users\mange\anaconda3\envs\pytorch\python.exe" -m swarm_sim.server
pause
