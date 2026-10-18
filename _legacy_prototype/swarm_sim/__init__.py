"""Decentralized MUM-T / Loyal Wingman Autonomous Swarm Simulation Framework.

Combines:
1. Continuous 3D/2.5D rigid-body rover & drone physics + 360-degree LiDAR raycasting + first-person RGB camera rendering.
2. Real PyTorch CUDA neural networks (`HazardVisionCNN` + `SwarmTacticalPolicyNet`) for visual scene classification and tactical bidding.
3. Real UDP Peer-to-Peer (P2P) socket mesh + Consensus-Based Bundle Algorithm (CBBA) that survives complete Commander/Laptop link severing.
4. A* + Dynamic Window Approach (DWA) local obstacle avoidance through narrow doorways.
5. Live 3D Three.js WebGL Tactical Command Center + Webots (.wbt) & ESP32/Pi hardware node compatibility.
"""

__version__ = "1.0.0"
