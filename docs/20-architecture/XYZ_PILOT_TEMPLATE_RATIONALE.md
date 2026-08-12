# Template Rationale

The template exists to make future Exasol SE pilots repeatable without copying
customer-specific state from earlier projects.

## Why This Shape

- `docs/XYZ_PILOT_ISSUE_TRACKER.md` is the control register because pilot work
  crosses code, data, AWS, access management, business validation, and
  documentation.
- `services/` isolates deployable runtime components from scheduler automation
  and repository governance.
- `scheduler/` keeps Lambda and EventBridge automation together.
- `tests/agents/` and `tests/scripts/` keep E2E swarm validation reproducible.
- `.agents/skills/` keeps agent operating instructions versioned with the
  project.
- `.github/workflows/` keeps validation close to the code but must default to
  safe permissions and non-deploying behavior.

## Deployment Principle

Generation creates a deployable repository. Deployment is a separate explicit
operator action because it can create cost-bearing AWS resources, external
URLs, user invitations, and outbound notifications.

## Recipient Principle

Operational recipients and business users are distinct:

- OPS-only users receive infrastructure status, defect, deploy, and start/stop
  notifications.
- Business users receive pilot access, UAT, and business-facing updates.

Generated projects must keep those cohorts separate in config and automation.
