# DISPATCH: Survey Explorer 2 - WSL & Substrate Environment

You are `teamwork_preview_explorer_survey_2`.
Working directory: C:\Users\antho\.windsurf\cappo-backend\.agents\teamwork_preview_explorer_survey_2
Project root: C:\Users\antho\.windsurf\cappo-backend
Original Request: C:\Users\antho\.windsurf\cappo-backend\.agents\ORIGINAL_REQUEST.md

Task:
Investigate the WSL and Linux substrate environment:
1. Probe WSL via `wsl -e ...` or WSL commands: Linux distro, kernel version, python3 version, pip, gcc / clang / wabt / wasm-tools if available.
2. Verify cgroup v2 status inside WSL: check mount point (e.g. `/sys/fs/cgroup`), whether unified hierarchy (v2) is enabled (`cgroup2fs`), available controllers (`cgroup.controllers`, `cgroup.subtree_control`, specifically `memory` and `cpu`).
3. Check permissions / root or sudo requirements for creating child cgroups in WSL (e.g., `/sys/fs/cgroup/vre0` or user systemd slices/delegation).
4. Verify if `wasmtime` python package or CLI can be run in WSL, and what installation is needed (pip install wasmtime).
5. Document exact commands, prerequisites, and operational steps needed for the supervisor to run in WSL.
6. Write your complete findings to `C:\Users\antho\.windsurf\cappo-backend\.agents\teamwork_preview_explorer_survey_2\analysis.md` and `handoff.md`.
7. Send a message to parent when done.
