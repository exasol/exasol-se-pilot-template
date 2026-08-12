---
name: sflow-rt-visualization-debugger
description: Use when asked to validate, debug, or fix sFlow-RT visualizations in this workshop repository, including Vizceral, Topology, Particle, address groups, topology JSON, agents, flows, telemetry, and sFlow-RT REST/API behavior.
---

# sFlow-RT Visualization Debugger

Use this skill to diagnose sFlow-RT based visualization problems in the workshop
stack. It covers Vizceral, Topology, Particle, and related sFlow-RT REST APIs.

## Goals

- Verify sFlow-RT starts, exposes HTTP on `8008`, and listens for sFlow on UDP `6343`.
- Validate the selected visualization app: Vizceral, Topology, or Particle.
- Validate address groups, topology JSON, agents, active flows, and telemetry arrival.
- Distinguish UI availability from real traffic telemetry.
- Keep HTTP/UI and UDP collector exposure safe by default.

## Safety Rules

- Keep sFlow-RT HTTP/REST bound to `127.0.0.1` unless the user explicitly approves a private, firewalled exposure.
- For remote agents, expose only UDP `6343` where possible; do not expose the REST API broadly.
- Do not add AWS security group rules for `8008/tcp` or `6343/udp` without explicit approval.
- Do not install host or packet-capture agents on student machines or Exasol instances without approval.
- Treat telemetry as sensitive because it can reveal hosts, ports, topology, and traffic patterns.

## Public Reference Anchors

- Vizceral image: `sflow/vizceral`, HTTP `8008`, UDP `6343`, groups endpoint under `/app/vizceral/scripts/traffic.js/groups/json`.
- Vizceral requires an `INTERNET` group and is designed around `INTERNET` versus named local groups.
- Topology persists link/node topology and verifies monitored topology coverage.
- Particle is often a better fit for endpoint-to-endpoint particle flow visualization.
- sFlow-RT app status endpoint: `/app/<name>/status`; global status endpoint: `/status`.

## Validation Workflow

1. Validate Compose rendering:
   - `docker compose -f code/kestra/docker-compose-with-grafana.yml config`
   - Confirm HTTP and UDP bind addresses are intentional.

2. Validate sFlow-RT startup:
   - `docker compose logs --tail=200 sflow_rt`
   - `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/status`
   - `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/apps/json`

3. Validate Vizceral:
   - UI: `curl -fsSI http://localhost:${SFLOW_RT_PORT:-8008}/app/vizceral/html/index.html`
   - App status: `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/app/vizceral/status`
   - Groups: `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/app/vizceral/scripts/traffic.js/groups/json`
   - Data: `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/app/vizceral/scripts/traffic.js/data/json`
   - If groups include remote endpoints as non-INTERNET groups, check whether this hides the desired INTERNET/local flow.

4. Validate Topology when used:
   - Publish topology with `PUT /topology/json`.
   - Read back with `GET /topology/json`.
   - Confirm all expected nodes and links are present.

5. Validate telemetry:
   - Agents: `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/agents/json`
   - Flow definitions: `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/flow/json`
   - Active flows: use the app-specific flow names and `activeflows` endpoints.
   - No agents means the UI can render but live traffic animation will remain zero.

## Common Root Causes

- The app is healthy but no sFlow agent is sending telemetry to UDP `6343`.
- A publisher targets `127.0.0.1` from inside a container, where it should use `http://sflow_rt:8008`.
- Vizceral groups are configured as named remote endpoints, but the public Vizceral script expects one side to be `INTERNET`.
- The sFlow-RT store is not writable or not persistent, causing groups/topology to disappear after restart.
- `curl -I` sees a `302` redirect; use the final `/html/index.html` path or `curl -L`.

## Report Format

For every issue, report:

- `Trigger`: URL, command, app, group, topology, or telemetry condition.
- `Root cause`: app behavior, bind address, group model, telemetry absence, or unsafe exposure.
- `Fix`: exact Compose, script, or documentation change.
- `Validation`: HTTP/API command proving the fix.
- `Residual risk`: security or telemetry coverage still not validated.
