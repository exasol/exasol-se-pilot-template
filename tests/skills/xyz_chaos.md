# xyz_chaos Skill Profile

## Primary Skills

- `runtime-debugger`
- `container-network-telemetry-validator`
- `prometheus-grafana-validator`
- `workshop-runner`

## Supporting Skills

- `aws-ec2-ssm-runtime-validator`
- `aws-eventbridge-scheduler-validator`
- `aws-cloudwatch-observability-validator`
- `prometheus-api`
- `promql`
- `sflow-rt-visualization-debugger`
- `docker-compose-ec2`

## Operating Lens

`xyz_chaos` runs non-destructive negative-path and resilience checks unless a
separate operator explicitly approves mutating chaos. The default mode rejects
invalid auth, unsupported methods, unknown paths, and light concurrent health
load.

The role should look for:

- auth bypass or invalid-token acceptance;
- method mismatch behavior that exposes internals;
- weak concurrent health behavior;
- Grafana/Prometheus boundary regressions;
- network or telemetry paths that look healthy but reject all useful probes.
- EC2, scheduler, and CloudWatch resilience signals that can be checked without
  mutating the live environment.
