# xyz_e2e Skill Profile

## Primary Skills

- `student`
- `bi-analyst`
- `exasol-database`
- `streamlit-dashboard-builder`
- `workshop-runner`

## Supporting Skills

- `grafana-dashboard-validator`
- `runtime-debugger`
- `aws-cognito-oauth-validator`
- `aws-bedrock-runtime-validator`

## Operating Lens

`xyz_e2e` validates observable end-to-end user paths across QBR Streamlit,
Grafana, Chatbot, MCP, and Exasol reads. It should prove that a subscriber can
reach the expected shell or auth gate and that backend read paths still return
data.

The role should look for:

- app shells that load but cannot reach backend data;
- auth boundaries that block too early or too late;
- Streamlit/Grafana shells that expose raw errors;
- MCP unauthenticated access or malformed OAuth metadata;
- dashboard-backed Exasol reads that fail or return empty data.
- chatbot or MCP user journeys that fail at the Cognito or Bedrock boundary.
