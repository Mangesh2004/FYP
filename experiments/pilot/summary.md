| condition | method | n | success (95% CI) | rooms | hazard recall (95% CI) | false conf. | median time (success) | recovery after kill |
|---|---|---|---|---|---|---|---|---|
| normal | static | 10 | 0.30 (0.11-0.60) | 1.20 | 0.22 (0.00-0.45) | 0.00 | 120 s | - |
| normal | greedy | 10 | 0.40 (0.17-0.69) | 1.60 | 0.30 (0.10-0.55) | 0.10 | 147 s | - |
| normal | cbba | 10 | 0.40 (0.17-0.69) | 1.60 | 0.35 (0.10-0.65) | 0.10 | 161 s | - |
| normal | central | 10 | 0.30 (0.11-0.60) | 1.20 | 0.25 (0.00-0.50) | 0.00 | 150 s | - |
| loss20 | static | 10 | 0.40 (0.17-0.69) | 1.60 | 0.35 (0.10-0.65) | 0.00 | 115 s | - |
| loss20 | greedy | 10 | 0.30 (0.11-0.60) | 1.40 | 0.32 (0.10-0.58) | 0.10 | 157 s | - |
| loss20 | cbba | 10 | 0.30 (0.11-0.60) | 1.20 | 0.18 (0.00-0.40) | 0.00 | 137 s | - |
| loss20 | central | 10 | 0.30 (0.11-0.60) | 1.20 | 0.13 (0.00-0.37) | 0.00 | 160 s | - |
| commander_cut | static | 10 | 0.30 (0.11-0.60) | 1.20 | 0.25 (0.00-0.50) | 0.00 | 113 s | - |
| commander_cut | greedy | 10 | 0.30 (0.11-0.60) | 1.20 | 0.25 (0.00-0.50) | 0.00 | 152 s | - |
| commander_cut | cbba | 10 | 0.20 (0.06-0.51) | 1.00 | 0.18 (0.00-0.40) | 0.00 | 172 s | - |
| commander_cut | central | 10 | 0.00 (-0.00-0.28) | 0.60 | 0.00 (0.00-0.00) | 0.00 | - | - |
| robot_killed | static | 10 | 0.00 (-0.00-0.28) | 0.40 | 0.15 (0.00-0.35) | 0.00 | - | - |
| robot_killed | greedy | 10 | 0.20 (0.06-0.51) | 0.80 | 0.20 (0.00-0.50) | 0.00 | 221 s | 171 s |
| robot_killed | cbba | 10 | 0.30 (0.11-0.60) | 1.20 | 0.23 (0.00-0.50) | 0.10 | 214 s | 164 s |
| robot_killed | central | 10 | 0.30 (0.11-0.60) | 1.20 | 0.20 (0.00-0.50) | 0.10 | 235 s | 174 s |
| door_blocked | static | 10 | 0.20 (0.06-0.51) | 0.80 | 0.18 (0.00-0.40) | 0.00 | 129 s | - |
| door_blocked | greedy | 10 | 0.20 (0.06-0.51) | 1.20 | 0.18 (0.00-0.40) | 0.00 | 174 s | - |
| door_blocked | cbba | 10 | 0.30 (0.11-0.60) | 1.20 | 0.27 (0.00-0.53) | 0.00 | 200 s | - |
| door_blocked | central | 10 | 0.20 (0.06-0.51) | 1.20 | 0.17 (0.00-0.40) | 0.00 | 157 s | - |

Paired tests (cbba vs other, same seeds). Time: Wilcoxon signed-rank on censored time; success: exact McNemar.

| comparison | pairs | median time diff (ref - other) | p (time) | ref-only successes | other-only successes | p (success) |
|---|---|---|---|---|---|---|
| normal:cbba_vs_static | 10 | 0.0 s | 0.5 | 1 | 0 | 1 |
| normal:cbba_vs_greedy | 10 | 0.0 s | 0.75 | 0 | 0 | 1 |
| normal:cbba_vs_central | 10 | 0.0 s | 0.75 | 1 | 0 | 1 |
| loss20:cbba_vs_static | 10 | 0.0 s | 0.375 | 0 | 1 | 1 |
| loss20:cbba_vs_greedy | 10 | 0.0 s | 0.875 | 0 | 0 | 1 |
| loss20:cbba_vs_central | 10 | 0.0 s | 0.5 | 0 | 0 | 1 |
| commander_cut:cbba_vs_static | 10 | 0.0 s | 0.25 | 0 | 1 | 1 |
| commander_cut:cbba_vs_greedy | 10 | 0.0 s | 0.5 | 0 | 1 | 1 |
| commander_cut:cbba_vs_central | 10 | 0.0 s | 0.25 | 2 | 0 | 0.5 |
| robot_killed:cbba_vs_static | 10 | 0.0 s | 0.25 | 3 | 0 | 0.25 |
| robot_killed:cbba_vs_greedy | 10 | 0.0 s | 0.75 | 1 | 0 | 1 |
| robot_killed:cbba_vs_central | 10 | 0.0 s | 1 | 0 | 0 | 1 |
| door_blocked:cbba_vs_static | 10 | 0.0 s | 0.75 | 1 | 0 | 1 |
| door_blocked:cbba_vs_greedy | 10 | 0.0 s | 0.75 | 1 | 0 | 1 |
| door_blocked:cbba_vs_central | 10 | 0.0 s | 0.75 | 1 | 0 | 1 |
