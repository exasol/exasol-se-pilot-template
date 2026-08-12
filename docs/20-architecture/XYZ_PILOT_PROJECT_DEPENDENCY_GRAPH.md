# Project Dependency Graph

This document describes how project documents, runtime modules, GitHub
automation, candidates, and evidence artifacts depend on each other.

The Mermaid graph is intentionally simple so it renders in Streamlit surfaces
and chatbot responses.

```mermaid
flowchart TD
    Tracker["docs/XYZ_PILOT_ISSUE_TRACKER.md<br/>central control register"]
    TOC["docs/20-architecture/XYZ_PILOT_PROJECT_DOCUMENTATION_TOC.md"]
    Graph["docs/20-architecture/XYZ_PILOT_PROJECT_DEPENDENCY_GRAPH.md"]
    Runbooks["docs/30-runbooks/**"]
    Status["docs/00-live-status/**"]
    Evidence["docs/60-evidence-inventory/**"]
    QA["docs/70-generated-qa/**"]

    Generator["generate / generate.sh"]
    Config["config/project.env<br/>config/recipients.yml"]
    Services["services/**<br/>chatbot · MCP · Streamlit · Grafana · operator dashboard"]
    Scheduler["scheduler/**<br/>Lambda and EventBridge automation"]
    Scripts["scripts/**<br/>validation, archive, inventory, deployment helpers"]
    Tests["tests/**<br/>swarm agents and validation scripts"]
    Workflows[".github/workflows/**"]

    AWS["AWS account/region/AZ<br/>Exasol Personal · EC2 · SES · SNS · Cognito · Lambda · EventBridge · S3"]
    GitHub["GitHub repo<br/>Actions · tracker commits · candidates"]
    Cloudflare["Cloudflare tunnel / DNS"]

    Generator --> Config
    Generator --> Tracker
    Generator --> Services
    Generator --> Scheduler
    Generator --> Scripts
    Generator --> Tests
    Generator --> Workflows

    Config --> Services
    Config --> Scheduler
    Config --> AWS
    Config --> Cloudflare

    Services --> AWS
    Services --> Cloudflare
    Services --> GitHub
    Scheduler --> AWS
    Scheduler --> GitHub
    Scripts --> AWS
    Scripts --> GitHub
    Tests --> Services
    Tests --> AWS
    Tests --> GitHub

    Workflows --> Scripts
    Workflows --> Tests
    Workflows --> Tracker
    Tests --> Tracker
    Scheduler --> Tracker

    Tracker --> Status
    Tracker --> Runbooks
    Tracker --> TOC
    TOC --> Graph
    Graph --> TOC
    Evidence --> Tracker
    QA --> Tracker
```

## Update Discipline

Update this graph whenever any of these change:

- Runtime service boundaries.
- GitHub workflow read/write paths.
- Tracker candidate flow.
- AWS service ownership.
- Documentation folder structure.
- Evidence or test-output locations.
