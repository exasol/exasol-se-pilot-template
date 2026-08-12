---
name: github-tracker-notification-automation
description: Use when designing or validating automation around docs/HPC_POC_ISSUE_TRACKER.md changes, especially major defect heads-up notifications, duplicate-safe issue parsing, tracker guardrails, and email fan-out through GitHub Actions or AWS SES.
---

# GitHub Tracker Notification Automation

Use this skill for tracker-aware notification workflows.

## Operating Rules

- Never write `docs/HPC_POC_ISSUE_TRACKER.md` from parallel automation unless HPC_MASTER sequencing and tracker guardrails are explicitly implemented.
- For notifications, parse diffs read-only and send only candidate heads-up messages; do not mutate tracker state.
- Notify only on new defect/bug rows with severity `critical` or `major` unless the GitHub Operator Team approves a broader policy.
- Do not notify for generic feature requests by default.
- For email delivery, prefer existing AWS SES with GitHub Actions OIDC over SMTP passwords.
- Redact issue body snippets if they contain credentials, URLs with tokens, or user-specific auth material.
- Deduplicate by `XYZ-PILOT-NNN` and commit SHA so rebases or retries do not spam recipients.

## Review Checklist

- Diff source compares the pushed commit range, not the whole file blindly.
- Detection uses top-table row plus full-register detail when possible.
- Recipients are configured as repository variables or controlled code constants, not secrets unless required.
- Email includes issue ID, severity, area, summary, commit, author, and tracker link.
- Workflow has `contents: read` and `id-token: write` only if SES/OIDC is used.
- Runs are bounded with timeout and concurrency.

## References

- GitHub workflow events: https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows
- GitHub secure-use guidance: https://docs.github.com/en/actions/reference/security/secure-use
- Microsoft Learn GitHub Actions automation path: https://learn.microsoft.com/en-us/training/paths/github-actions/
