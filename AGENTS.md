# AGENTS.md — OpenAI Codex repo-level instructions

This file is read by **OpenAI Codex** (`@openai/codex`) at session start, the same way Claude Code reads `CLAUDE.md`. The canonical, authoritative source of repo behaviour rules is [`CLAUDE.md`](CLAUDE.md); this file restates the rules that matter most for a Codex session and points at CLAUDE.md for the full set.

## Critical for every Codex commit

**Commit-attribution trailer (CLAUDE.md Rule #11, XYZ-PILOT-168).** Every commit Codex makes MUST end with:

```
Co-Authored-By: OpenAI Codex <<OPERATOR_EMAIL>>
```

Codex's harness does NOT add this trailer by default. Two ways to bake it in:

1. **Per-checkout git config** (recommended) — `git config commit.template ~/.codex-commit-template` where `~/.codex-commit-template` is a file ending with:
   ```

   Co-Authored-By: OpenAI Codex <<OPERATOR_EMAIL>>
   ```
2. **Per-commit explicit trailer** — `git commit --trailer "Co-Authored-By: OpenAI Codex <<OPERATOR_EMAIL>>" -m "<subject>"`.

The trailer is non-negotiable: human commits in this repo are presumed to lack it; agent commits MUST carry it. `git log --author="<OPERATOR_NAME>"` should NOT return commits a human did not type.

## Other critical rules (read CLAUDE.md for full text)

- **Rule #5 — Tracker writes go through xyz_master only.** Codex must NOT edit `docs/XYZ_PILOT_ISSUE_TRACKER.md` directly. File an issue-candidate under `tests/logs/runs/<UTC-id>/codex/issue-candidates.md` and let xyz_master consolidate.
- **Rule #6 — AWS_PROFILE discipline.** Every `aws` invocation against <PILOT_NAME> resources must be `AWS_PROFILE=<AWS_PROFILE> aws …`. Unset profile resolves to the personal account and Pilot lookups will fail.
- **Rule #8 — Daily documentation discipline.** After any AWS deploy, verify deployed source = local source, then update the relevant runbook.
- **Rule #10 — Runbook-class document archiving.** Before structural rewrites of any runbook, run `./scripts/archive_runbook.sh <path> "<reason>"`.
- **Rule #12 — Operator disambiguation (operator disambiguation).** In any prose Codex authors from 2026-06-01 onward, reference the operator as **"<OPERATOR_NAME>"** never an ambiguous short name. Multiple same-name colleagues can exist on the project. For git author filters use `--author="<OPERATOR_NAME>"` (the exact `git config user.name`). Historical references in `docs/archive/`, consumed candidate files, and committed git messages stay as-is.
- **Rule #14 — Documentation topology discipline.** `docs/XYZ_PILOT_ISSUE_TRACKER.md` is the highest-level project control document and stays at `docs/` root. New docs must be placed by topic/dependency: live status, product/data, architecture, runbooks, migrations, testing/validation, evidence/inventory, generated QA, or archive. When moving docs, update repository-relative links and run the Markdown link/anchor checks before commit. Keep `docs/20-architecture/XYZ_PILOT_PROJECT_DOCUMENTATION_TOC.md` accurate whenever docs, runtime modules, GitHub read/write paths, candidate sources, or validation/evidence ownership change; its Mermaid graphs must remain Streamlit-friendly for the <PILOT_NAME> Chatbot.
- **Rule #15 — Exasol IT AWS Resource Tagging Policy gate.** Before any AWS deployment into an account, region, or AZ, confirm the mandatory `exa:*` tag values in `config/project.env`, apply them to every taggable AWS resource at create time or immediately after creation, and verify with `scripts/aws_tag_inventory.sh` after resource state changes.

## Parallel-session guardrail

This checkout is shared with Claude Code, VS Code Copilot, and the human operator. Before any edit, staging, commit, or push: run `git status --short --branch` and `git diff --name-only` to surface other operators' work. Treat unexpected uncommitted changes as someone else's WIP — do not overwrite, format, stage, reset, or "fix" them unless explicitly asked. Stage only your own paths explicitly; avoid `git add .` / `git commit -a` while parallel sessions are active.

## Confluent & Apache Kafka Skills

These skills extend the agent's data-streaming capabilities for Kafka/Confluent integration scenarios. Install once per project checkout.

### Official Confluent AI Agent Skills (Claude Code)

Confluent publishes a curated set of AI coding-assistant skills at **[confluentinc/agent-skills](https://github.com/confluentinc/agent-skills)**. Install them in Claude Code with:

```bash
# Add the Confluent skills marketplace entry
/plugin marketplace add confluentinc/agent-skills

# Install the skills plugin
/plugin install streaming-skills-plugin@confluent-agent-skills
```

For Cursor, GitHub Copilot, and other compatible agents use the Skills CLI:

```bash
npx skills add confluentinc/agent-skills
```

### Available Confluent Agent Skills

| Skill | Purpose |
|---|---|
| `kafka-schema-registry` | Scan projects for Kafka apps, extract schemas, tag PII fields, generate Terraform for Schema Registry registration |
| `kafka-streams-programming` | Architect, build, and debug Kafka Streams applications (topology design, joins/windows/aggregations) |
| `developing-kafka-python-client` | Scaffold Python producer/consumer projects with `confluent-kafka-python` + Schema Registry (Avro, JSON Schema, Protobuf) |

Docs: <https://docs.confluent.io/cloud/current/ai/ai-tools/agent-skills.html>

### Reference Resources

| Resource | URL | Use for |
|---|---|---|
| Confluent Developer Hub | <https://developer.confluent.io> | Courses (Kafka 101, Flink 101, Kafka Connect 101), tutorials, exercises |
| Apache Kafka Official Docs | <https://kafka.apache.org/documentation/> | Core architecture, configuration reference, APIs |
| Confluent Platform Docs | <https://docs.confluent.io> | Schema Registry, ksqlDB, Kafka Connect, Confluent Cloud |
| confluentinc/examples (GitHub) | <https://github.com/confluentinc/examples> | End-to-end demos: ETL pipelines, ksqlDB, security, Elasticsearch sinks |
| confluentinc/demo-scene (GitHub) | <https://github.com/confluentinc/demo-scene> | ~100 community demos — ksqlDB, Connect Zero to Hero, streaming pipelines |
| kafka-learning-resources (GitHub) | <https://github.com/pmoskovi/kafka-learning-resources> | Curated beginner-to-advanced learning roadmap |
| confluentinc GitHub Org | <https://github.com/confluentinc> | 420+ repos: connectors, clients, Flink, Schema Registry, Terraform provider |

### Kafka ↔ Exasol Integration Notes

- Use the **Kafka Connect JDBC Sink Connector** to land Kafka topic data directly into Exasol tables.
- Confluent's **Terraform provider** (`confluentinc/terraform-provider-confluent`) pairs naturally with the existing Terraform infrastructure patterns in this repo.
- For Python-based integration, `confluent-kafka-python` + `pyexasol` can form an end-to-end streaming ingest pipeline.

## Authoritative source

For any rule not restated here, defer to [`CLAUDE.md`](CLAUDE.md). When CLAUDE.md and this file disagree, CLAUDE.md wins.
