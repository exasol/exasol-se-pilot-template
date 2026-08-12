---
name: container-network-telemetry-validator
description: Use when asked to validate Docker container networking and telemetry capture for this workshop, especially Docker bridge networks, host networking, port bindings, localhost behavior, optional host-sflow, and sFlow collector reachability.
---

# Container Network Telemetry Validator

Use this skill to validate Docker networking and telemetry capture paths for the
workshop services, including Kestra, PostgreSQL, Grafana, Portainer, sFlow-RT,
and optional Host sFlow.

## Goals

- Verify Docker Compose service networking, DNS, ports, and bind addresses.
- Validate localhost versus container-local behavior.
- Validate sFlow UDP collector reachability from approved agents.
- Validate optional `sflow_agent` / `host-sflow` behavior without enabling it by default.
- Identify port conflicts and stale Compose projects that can confuse students.

## Safety Rules

- Do not stop, remove, or recreate unrelated containers or volumes without explicit confirmation.
- Do not enable host networking, packet capture, or Docker socket mounts on student machines without approval.
- Keep administrative UIs and Docker socket backed services bound to localhost by default.
- Do not open AWS or local firewall ports for telemetry without explicit approval.
- Treat Docker socket access as administrative access to the host.

## Validation Workflow

1. Inspect running containers and ports:
   - `docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'`
   - `docker compose ps`
   - Check for multiple Compose projects using the same logical services.

2. Validate Compose network model:
   - `docker compose config`
   - Confirm service names resolve on the Compose network.
   - Confirm host-published ports are bound to `127.0.0.1` unless explicitly approved.

3. Validate host to container access:
   - Grafana: `curl -I http://localhost:${GRAFANA_PORT:-3000}`
   - sFlow-RT HTTP: `curl -fsS http://localhost:${SFLOW_RT_PORT:-8008}/status`
   - Portainer: `curl -k -I https://localhost:${PORTAINER_PORT:-9443}`

4. Validate container to container access:
   - From a service container, use service DNS such as `http://sflow_rt:8008`, not `127.0.0.1`.
   - From Grafana, the PostgreSQL datasource should use `kestra_postgres:5432`.

5. Validate optional Host sFlow:
   - Keep the profile disabled by default.
   - Render config with `docker compose --profile sflow-agent config`.
   - Confirm `network_mode: host`, `COLLECTOR`, `PORT`, `NET`, and read-only Docker socket mount are expected.
   - Check Host sFlow logs only after approval to start the profile.

6. Validate telemetry arrival:
   - sFlow-RT agents endpoint: `/agents/json`.
   - If no agents are present, verify collector bind address and agent target IP.
   - Remember `0.0.0.0` is a bind address, not an agent destination.

## Common Root Causes

- A script targets `localhost` from inside a container instead of the service name.
- A service was started with a custom Compose project, but a validation script uses the default project.
- Port `8080`, `3000`, `8008`, `9443`, or `5432` is already used by another stack.
- Docker Desktop for macOS/Windows does not expose the same host-network behavior as Linux.
- Host sFlow has no visible Docker bridge to sample or cannot reach the collector.

## Report Format

For every issue, report:

- `Trigger`: command, container, port, or network path.
- `Root cause`: bind address, Docker network, service DNS, profile, or platform mismatch.
- `Fix`: exact Compose, environment, or command change.
- `Validation`: command proving connectivity or telemetry.
- `Residual risk`: platform or security constraint still requiring instructor approval.
