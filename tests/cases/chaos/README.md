# Chaos Checks

Controlled failure probes. Start with non-destructive negative tests such as
invalid auth, unauthorized MCP calls, and rejected unsafe SQL. Container restart
or kill tests require before/after evidence and must avoid data loss.
