# Scenario configurations

`four_room_disaster.json` is the primary validated disaster-response configuration:
four rooms, a shared corridor, four agents, four unassigned tasks and two scripted
events. It contains private simulator truth and must not be given to decisions.

Run `.venv/bin/python -m coopai.scenario_demo` from the source checkout, or pass an
explicit JSON path. See `docs/scenario_loading.md` for schema and boundary rules.
