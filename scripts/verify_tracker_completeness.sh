#!/usr/bin/env bash
# XYZ-PILOT-108 — pre/post tracker-edit completeness check.
#
# Run BEFORE and AFTER every XYZ_PILOT_ISSUE_TRACKER.md update so silent
# losses (rebase / merge / writer-discipline gaps) surface immediately
# instead of being discovered weeks later.
#
# Checks:
#   (A) Detail-section IDs are sequential XYZ-PILOT-001..N with no gaps.
#   (B) Every detail-section has a matching <a id="xyz-pilot-NNN"> anchor.
#   (C) Every top-table row references an existing detail section, and
#       every detail section is referenced from the top table.
#   (D) Top-table terminal rows (Resolved OR Wontfix — XYZ-PILOT-337) carry a
#       compact `(Resolved|Wontfix YYYY-MM-DD)` marker matching the Full-register
#       Status line and stay visible for only ONE business day; rows past that
#       window are a hard ERROR (prune them from the Active table — they remain
#       in the Full register). Promoted from a warning during the 2026-06-10
#       hardening after 26 stale rows accrued; extended to Wontfix by XYZ-PILOT-337.
#   (E) Active top-table row styling matches the detail-section semantic
#       status: terminal statuses (Resolved/Wontfix) must strike only ID,
#       Severity, and Area; non-terminal rows must not be struck-through.
#   (F) Active top-table Summary cells stay plain, contain exactly one compact
#       `(Resolved YYYY-MM-DD)` suffix for Resolved rows, and contain no
#       resolution prose. Resolution details belong only in the Full register.
#   (G) Full-register detail sections contain no Markdown strikethrough; only
#       the short-lived Active issues top table may use `~~...~~`.
#   (H) Last refresh line reflects today's UTC date in the expected form.
#   (I) No duplicate `#### ` sub-headings within one Full-register detail
#       section (e.g. two `#### Resolution` blocks). Closes XYZ-PILOT-158 —
#       a concurrent-writer race that produced exactly that pattern slipped
#       past every other check.
#   (J) Active-issues header-section E2E: the preamble is structurally complete
#       (Active-issues header + visibility-rule block + table header row) AND
#       the "*Today is YYYY-MM-DD (Dow) … cut-off date … YYYY-MM-DD (Dow)*"
#       freshness line carries today's date plus the correctly-computed
#       one-business-day visibility cut-off. Closes the 2026-06-10 regression
#       where that line froze at "2026-06-08 (Mon)" and (H) — which only checks
#       the **Last refresh** line — never noticed.
#   (K) Primary-tracker byte-size guard (XYZ-PILOT-336): WARN as the file nears
#       GitHub's ~1 MiB markdown rich-render ceiling, hard ERROR past it — so the
#       Full-register split (scripts/split_tracker_archive.py) runs before the
#       browser view degrades to raw source.
#   (L) Stateful code-fence balance (XYZ-PILOT-336): ERROR on a genuinely unclosed
#       ``` fence (which makes GitHub render the rest of the file as one code
#       block). Stateful so a ``` that is literal content inside a fenced block
#       does not false-positive.
#
# Exit code: 0 = all green. 1 = any check failed (list errors to stderr).
#
# Usage:
#   ./scripts/verify_tracker_completeness.sh           # quiet on success
#   ./scripts/verify_tracker_completeness.sh -v        # verbose
#   ./scripts/verify_tracker_completeness.sh --json    # machine-readable

set -uo pipefail

TRACKER="${TRACKER_PATH:-docs/XYZ_PILOT_ISSUE_TRACKER.md}"
VERBOSE=0
JSON=0
for arg in "$@"; do
  case "$arg" in
    -v|--verbose) VERBOSE=1 ;;
    --json) JSON=1 ;;
    -h|--help)
      sed -n '2,33p' "$0"
      exit 0 ;;
  esac
done

if [ ! -f "$TRACKER" ]; then
  echo "ERROR: tracker file not found at $TRACKER" >&2
  exit 1
fi

python3 - "$TRACKER" "$VERBOSE" "$JSON" <<'PY'
import json, re, sys
from datetime import date, timedelta

tracker_path, verbose_s, json_s = sys.argv[1], sys.argv[2], sys.argv[3]
verbose = verbose_s == "1"
emit_json = json_s == "1"

src = open(tracker_path).read()
errors: list[str] = []
warnings: list[str] = []

# Pull all canonical IDs (XYZ-PILOT-NNN with exactly 3 digits)
def ids(pat: str) -> list[int]:
    return sorted({int(m) for m in re.findall(pat, src, re.MULTILINE)})

detail_ids = ids(r"^### XYZ-PILOT-(\d{3})\b")
anchor_ids = ids(r'<a id="xyz-pilot-(\d{3})">')
# Top table: rows look like `| [XYZ-PILOT-NNN](...` or `| ~~[XYZ-PILOT-NNN](...`
top_table_ids = ids(r"\|\s*~?~?\[XYZ-PILOT-(\d{3})\]")

# (A.0) duplicate-detection — XYZ-PILOT-140 follow-up. The original verifier
# collapsed duplicates via the `set()` in `ids()`, hiding the case where
# two writers (e.g. test swarm + Claude Code) both file an `### XYZ-PILOT-NNN`
# detail section concurrently. Use a list (no dedupe) to catch this.
all_detail_hits = [int(m) for m in re.findall(r"^### XYZ-PILOT-(\d{3})\b", src, re.MULTILINE)]
from collections import Counter
dup_details = sorted(n for n, c in Counter(all_detail_hits).items() if c > 1)
if dup_details:
    errors.append(f"(A.0) duplicate detail sections: {['XYZ-PILOT-%03d' % n for n in dup_details]} — each ID must have at most one `### XYZ-PILOT-NNN` heading")
all_top_hits = [int(m) for m in re.findall(r"\|\s*~?~?\[XYZ-PILOT-(\d{3})\]", src)]
dup_top = sorted(n for n, c in Counter(all_top_hits).items() if c > 1)
if dup_top:
    errors.append(f"(A.0) duplicate top-table rows: {['XYZ-PILOT-%03d' % n for n in dup_top]} — each ID must have at most one top-table row")

# (A.1) Full-register strict-descending order — XYZ-PILOT-148.
# After the `## Full register (newest first)` header, every `### XYZ-PILOT-NNN`
# heading must appear in strict DESCENDING numerical order (highest = newest
# at top). Batch-ascending or interleaved blocks corrupt the chronology and
# are how this rule has been violated in the past (XYZ-PILOT-134..139 inserted
# ascending, XYZ-PILOT-140 dropped between 131 and 130).
register_body = ""
hdr_m = re.search(r'^## Full register \(newest first\)\s*\n', src, re.MULTILINE)
if hdr_m:
    register_body = src[hdr_m.end():]
    next_section = re.search(r'^## ', register_body, re.MULTILINE)
    if next_section:
        register_body = register_body[:next_section.start()]
    seq = [int(m.group(1)) for m in re.finditer(r'^### XYZ-PILOT-(\d{3})\b', register_body, re.MULTILINE)]
    if seq:
        violations = []
        for i in range(1, len(seq)):
            if seq[i] >= seq[i - 1]:
                violations.append((seq[i - 1], seq[i]))
        if violations:
            errors.append(
                f"(A.1) Full register not strict-descending — {len(violations)} order violation(s): "
                + ", ".join(f"XYZ-PILOT-{a:03d} → XYZ-PILOT-{b:03d}" for a, b in violations[:5])
                + (" …" if len(violations) > 5 else "")
            )

# (A.2) Active-issues TOP TABLE strict-descending order — XYZ-PILOT-148 follow-up.
# Same rule as (A.1) but for the top one-line-lookup table. Rows are
# `| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | …` (or `| ~~[XYZ-PILOT-NNN]…~~ |` struck).
# Header claims "newest-ID first" — verify that holds.
top_hdr = re.search(r'^## Active issues[^\n]*\n', src, re.MULTILINE)
if top_hdr:
    top_body = src[top_hdr.end():]
    next_section = re.search(r'^## ', top_body, re.MULTILINE)
    if next_section:
        top_body = top_body[:next_section.start()]
    top_seq = [int(m.group(1)) for m in re.finditer(r'^\|\s*~?~?\[XYZ-PILOT-(\d{3})\]', top_body, re.MULTILINE)]
    if top_seq:
        top_violations = []
        for i in range(1, len(top_seq)):
            if top_seq[i] >= top_seq[i - 1]:
                top_violations.append((top_seq[i - 1], top_seq[i]))
        if top_violations:
            errors.append(
                f"(A.2) Top-table not strict-descending — {len(top_violations)} order violation(s): "
                + ", ".join(f"XYZ-PILOT-{a:03d} → XYZ-PILOT-{b:03d}" for a, b in top_violations[:5])
                + (" …" if len(top_violations) > 5 else "")
            )
else:
    top_body = ""

# (A) sequential 001..N with no gaps
if detail_ids:
    expected = list(range(1, max(detail_ids) + 1))
    missing = sorted(set(expected) - set(detail_ids))
    if missing:
        errors.append(f"(A) gap in detail sections — missing: {['XYZ-PILOT-%03d' % n for n in missing]}")

# (B) every detail has anchor
no_anchor = sorted(set(detail_ids) - set(anchor_ids))
if no_anchor:
    errors.append(f"(B) detail sections without <a id> anchor: {['XYZ-PILOT-%03d' % n for n in no_anchor]}")
extra_anchor = sorted(set(anchor_ids) - set(detail_ids))
if extra_anchor:
    warnings.append(f"(B) <a id> anchors without matching detail: {['XYZ-PILOT-%03d' % n for n in extra_anchor]}")

# (C) top-table ↔ detail-section bijection (sort of; allow detail-only for archive)
ttable_no_detail = sorted(set(top_table_ids) - set(detail_ids))
if ttable_no_detail:
    errors.append(f"(C) top-table refs without detail section: {['XYZ-PILOT-%03d' % n for n in ttable_no_detail]}")
detail_no_ttable = sorted(set(detail_ids) - set(top_table_ids))
if detail_no_ttable:
    # detail-only is normal for older Resolved items pruned from the top table by XYZ-PILOT-047
    # — warn only, don't fail.
    if verbose:
        warnings.append(f"(C) detail-only (pruned from top table — normal for old Resolved items): {len(detail_no_ttable)} entries")

detail_status_by_id: dict[int, str] = {}
detail_re = re.compile(
    r'<a id="xyz-pilot-(\d{3})"></a>\s*\n\s*### XYZ-PILOT-\d{3}\b.*?'
    r'\n(?:-\s+)?\*\*Status:\*\*\s*([^\n]+)',
    re.DOTALL,
)
for m in detail_re.finditer(src):
    detail_status_by_id[int(m.group(1))] = m.group(2).strip()

def split_md_row(line: str) -> list[str]:
    """Split a Markdown table row on unescaped pipes, preserving cell text."""
    if not line.startswith("|"):
        return []
    cells: list[str] = []
    cur: list[str] = []
    escaped = False
    # Drop the first and last table delimiter when present.
    body = line[1:-1] if line.endswith("|") else line[1:]
    for ch in body:
        if ch == "\\" and not escaped:
            escaped = True
            cur.append(ch)
            continue
        if ch == "|" and not escaped:
            cells.append("".join(cur).strip())
            cur = []
            escaped = False
            continue
        cur.append(ch)
        escaped = False
    cells.append("".join(cur).strip())
    return cells

# (D) top-table Resolved rows: struck-through with resolution within 2 business days
def biz_days_since(d: date, today: date) -> int:
    days = 0
    cur = d
    while cur < today:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days

today = date.today()
# Find struck-through Resolved rows in the top table and check the compact
# `(Resolved YYYY-MM-DD)` Summary suffix against the matching Full-register
# status line.
in_top = False
stale_resolved = []
resolved_marker_mismatches = []
for line in src.split("\n"):
    if line.startswith("## Active issues"):
        in_top = True
        continue
    if line.startswith("## ") and in_top and not line.startswith("## Active"):
        in_top = False
        continue
    if in_top and line.startswith("|") and "~~" in line:
        m = re.search(r"\[XYZ-PILOT-(\d{3})\]", line)
        if m:
            status = detail_status_by_id.get(int(m.group(1)), "")
            # XYZ-PILOT-337: terminal = Resolved OR Wontfix; both age out of the top
            # table on the same one-business-day window and are pruned to the Full register.
            d = re.search(r"\b(Resolved|Wontfix)\s+\(?(\d{4}-\d{2}-\d{2})", status, re.IGNORECASE)
            if not d:
                continue
            term = d.group(1).capitalize()
            marker = re.search(r"\((Resolved|Wontfix)\s+(\d{4}-\d{2}-\d{2})\)\s*\|\s*$", line)
            if not marker:
                resolved_marker_mismatches.append(f"XYZ-PILOT-{m.group(1)}: missing compact {term.lower()} marker")
                continue
            if marker.group(2) != d.group(2):
                resolved_marker_mismatches.append(
                    f"XYZ-PILOT-{m.group(1)}: top marker {marker.group(2)} != status {d.group(2)}"
                )
                continue
            try:
                resolved_date = date.fromisoformat(marker.group(2))
            except ValueError:
                continue
            # XYZ-PILOT-NNN: align with daily-status prune threshold. The 2-
            # biz-day visibility rule counts resolution day as day 1 + next
            # biz day as day 2, prunes from day 3 (biz_days_since >= 2 → > 1).
            if biz_days_since(resolved_date, today) > 1:
                stale_resolved.append((f"XYZ-PILOT-{m.group(1)}", marker.group(1), biz_days_since(resolved_date, today)))
if resolved_marker_mismatches:
    errors.append(
        f"(D) top-table resolved marker mismatch — {len(resolved_marker_mismatches)} row(s): "
        + "; ".join(resolved_marker_mismatches[:8])
        + (" …" if len(resolved_marker_mismatches) > 8 else "")
    )
if stale_resolved:
    # XYZ-PILOT-NNN (2026-06-10 hardening): promoted from warning → ERROR. The
    # 2026-06-10 regression accumulated 26 struck-through Resolved rows back to
    # 2026-06-02 because this was a non-blocking warning while the daily-status
    # Lambda's prune pass was broken (XYZ-PILOT-237/-239). As a hard error it now
    # forces the row to be pruned at edit time if the Lambda missed it.
    errors.append(
        f"(D) top-table has {len(stale_resolved)} struck-through Resolved/Wontfix row(s) past the one-business-day visibility window "
        f"(prune them from the Active table — they remain in the Full register): "
        + ", ".join(f"{tid} resolved {rd} ({bd} biz-days)" for tid, rd, bd in stale_resolved[:10])
        + (" …" if len(stale_resolved) > 10 else "")
    )

# (E) semantic status parity between the Active issues top table and the
# matching Full-register detail section. Terminal rows strike only the first
# three cells (ID, Severity, Area); Summary remains plain for readability.
status_mismatches = []
summary_hygiene = []
if top_body:
    for line in top_body.splitlines():
        if not line.startswith("|"):
            continue
        m = re.search(r"\[XYZ-PILOT-(\d{3})\]", line)
        if not m:
            continue
        n = int(m.group(1))
        status = detail_status_by_id.get(n, "")
        terminal = bool(re.search(r"\b(Resolved|Wontfix)\b", status, re.IGNORECASE))
        cells = split_md_row(line)
        if len(cells) != 4:
            summary_hygiene.append(f"XYZ-PILOT-{n:03d}: malformed Markdown table row has {len(cells)} cells")
            continue
        first_three = cells[:3]
        summary = cells[3]
        status_resolved_date = None
        status_date_m = re.search(r"\b(?:Resolved|Wontfix)\s+\(?(\d{4}-\d{2}-\d{2})", status, re.IGNORECASE)
        if status_date_m:
            status_resolved_date = status_date_m.group(1)
        struck_cells = [cell.startswith("~~") and cell.endswith("~~") for cell in first_three]
        top_struck = all(struck_cells)
        partial_or_wrong = any(struck_cells) and not top_struck
        unexpected_nonterminal_strike = (not terminal) and any(struck_cells)
        if terminal != top_struck or partial_or_wrong or unexpected_nonterminal_strike:
            status_mismatches.append(
                f"XYZ-PILOT-{n:03d}: top={'struck' if top_struck else 'partial-struck' if any(struck_cells) else 'active'}, "
                f"detail_status={status or '<missing>'}"
            )
        if terminal:
            bad = []
            if "~~" in summary:
                bad.append("summary is struck-through")
            marker = re.search(r"\((?:Resolved|Wontfix)\s+(\d{4}-\d{2}-\d{2})\)\s*$", summary)
            summary_without_marker = re.sub(r"\s*\((?:Resolved|Wontfix)\s+\d{4}-\d{2}-\d{2}\)\s*$", "", summary)
            if status_resolved_date:
                if not marker:
                    bad.append("summary missing compact resolved/wontfix marker")
                elif marker.group(1) != status_resolved_date:
                    bad.append(f"summary marker {marker.group(1)} != status {status_resolved_date}")
            elif marker:
                bad.append("non-terminal summary has resolved/wontfix marker")
            if re.search(r"\(\s*(?:fully\s+|partially\s+)?(?:Resolved|Wontfix)\s+\d{4}-\d{2}-\d{2}\s+[—-]", summary, re.IGNORECASE):
                bad.append("summary contains resolution prose")
            if re.search(r"\b(?:Resolved|Wontfix)\s+\d{4}-\d{2}-\d{2}\b", summary_without_marker, re.IGNORECASE):
                bad.append("summary contains extra dated resolution prose")
            if bad:
                summary_hygiene.append(f"XYZ-PILOT-{n:03d}: " + ", ".join(bad))
if status_mismatches:
    errors.append(
        f"(E) top-table semantic status mismatch — {len(status_mismatches)} row(s): "
        + "; ".join(status_mismatches[:8])
        + (" …" if len(status_mismatches) > 8 else "")
    )
if summary_hygiene:
    errors.append(
        f"(F) top-table Summary hygiene violation — {len(summary_hygiene)} row(s): "
        + "; ".join(summary_hygiene[:8])
        + (" …" if len(summary_hygiene) > 8 else "")
    )

# (G) Full-register Markdown strikethrough ban. Resolved rows in the Active
# issues top table stay struck-through for two business days, but the full
# register is an audit log and should remain plain readable prose.
full_register_strikes = []
if register_body:
    register_start_line = src[:hdr_m.end()].count("\n") + 1
    for offset, line in enumerate(register_body.splitlines(), start=0):
        if "~~" in line:
            full_register_strikes.append(f"line {register_start_line + offset}: {line[:140]}")
if full_register_strikes:
    errors.append(
        f"(G) Full register contains Markdown strikethrough — remove `~~` from detail sections "
        f"({len(full_register_strikes)} line(s)): "
        + "; ".join(full_register_strikes[:8])
        + (" …" if len(full_register_strikes) > 8 else "")
    )

# (H) Last refresh line present and parseable
m = re.search(r"\*\*Last refresh:\*\*\s+(\d{4}-\d{2}-\d{2})", src)
if not m:
    warnings.append("(H) `**Last refresh:** YYYY-MM-DD` line not found in expected form")

# (I) XYZ-PILOT-158: detect duplicate `#### ` sub-headings within one
# Full-register detail section. The 2026-05-29T093225Z concurrent-writer
# race produced two `#### Resolution` blocks inside XYZ-PILOT-113's detail
# body and slipped past every check A..H — `(A.0)` only catches duplicate
# `### XYZ-PILOT-NNN` headings, not sub-headings within one section.
# Walk anchor → next-anchor windows in the Full register and group `#### `
# heading text after lowercase + strip; flag any group > 1.
def _normalize_subheading(text: str) -> str:
    return re.sub(r"[\s\.\:\—\-]+$", "", text.strip().lower())

dup_subheadings = []
if register_body:
    anchor_matches = list(re.finditer(r'<a id="xyz-pilot-(\d{3})"></a>', register_body))
    for i, am in enumerate(anchor_matches):
        ticket_id = int(am.group(1))
        start = am.end()
        end = anchor_matches[i + 1].start() if i + 1 < len(anchor_matches) else len(register_body)
        block = register_body[start:end]
        sub_headings = re.findall(r'^####\s+(.+?)\s*$', block, re.MULTILINE)
        normalised = [_normalize_subheading(h) for h in sub_headings if h.strip()]
        from collections import Counter as _Cnt
        for heading_norm, count in _Cnt(normalised).items():
            if count > 1:
                # Recover the most-readable original wording for the message.
                originals = [h.strip() for h in sub_headings if _normalize_subheading(h) == heading_norm]
                dup_subheadings.append(
                    f"XYZ-PILOT-{ticket_id:03d}: `#### {originals[0]}` appears {count}×"
                )
if dup_subheadings:
    errors.append(
        f"(I) duplicate sub-heading inside a detail section — {len(dup_subheadings)} occurrence(s): "
        + "; ".join(dup_subheadings[:8])
        + (" …" if len(dup_subheadings) > 8 else "")
    )

# (J) XYZ-PILOT-NNN (2026-06-10 hardening): Active-issues header-section E2E. The
# preamble must be structurally complete AND the "*Today is …*" freshness line
# must carry today's date + the correctly-computed business-day visibility
# cut-off. The 2026-06-10 regression (header frozen at "2026-06-08 (Mon)" while
# the real date was 2026-06-10 (Wed), 26 resolved rows unpruned) slipped past
# every A..I check because (H) only validates the **Last refresh** line — never
# this second, separately-maintained date line.
def _biz_cutoff(today_: date) -> date:
    # Earliest resolved-date still inside the one-business-day visibility window
    # (mirrors the (D) prune threshold: visible iff biz_days_since <= 1).
    c = today_
    while biz_days_since(c - timedelta(days=1), today_) <= 1:
        c -= timedelta(days=1)
    return c

_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
header_problems = []
if "## Active issues" not in src:
    header_problems.append("missing '## Active issues' header")
if "Visibility rule" not in src:
    header_problems.append("missing visibility-rule block")
if not re.search(r"^\|\s*ID\s*\|\s*Severity\s*\|\s*Area\s*\|\s*Summary\s*\|", src, re.MULTILINE):
    header_problems.append("missing Active-issues table header row")
todayline = re.search(
    r"\*Today is (\d{4}-\d{2}-\d{2})\s*\((\w{3})\)[^\n]*?cut-?off date[^\n]*?(\d{4}-\d{2}-\d{2})\s*\((\w{3})\)",
    src,
)
if re.search(r"\*Today is <TRACKER_TODAY>;.*<TRACKER_CUTOFF>", src):
    # Unrendered template seed: the date is stamped by
    # scripts/render_template_placeholders.py at generate time, so there is no
    # correct value to assert here. Structural checks above still apply.
    warnings.append("(J) freshness line is an unrendered template placeholder — stamped at generate time")
elif not todayline:
    header_problems.append(
        "missing/malformed '*Today is YYYY-MM-DD (Dow) … cut-off date … YYYY-MM-DD (Dow)*' freshness line"
    )
else:
    t_date, t_dow, c_date, c_dow = todayline.groups()
    if t_date != today.isoformat():
        header_problems.append(f"'Today is' date {t_date} != actual today {today.isoformat()}")
    elif t_dow != _DOW[today.weekday()]:
        header_problems.append(f"'Today is' weekday ({t_dow}) wrong for {today.isoformat()} (expect {_DOW[today.weekday()]})")
    exp_cut = _biz_cutoff(today)
    if c_date != exp_cut.isoformat():
        header_problems.append(f"cut-off date {c_date} != expected {exp_cut.isoformat()} (one business day back from {today.isoformat()})")
    elif c_dow != _DOW[exp_cut.weekday()]:
        header_problems.append(f"cut-off weekday ({c_dow}) wrong for {exp_cut.isoformat()} (expect {_DOW[exp_cut.weekday()]})")
if header_problems:
    errors.append("(J) Active-issues header-section E2E — " + "; ".join(header_problems))

# (K) XYZ-PILOT-336: primary-tracker byte-size guard. GitHub stops rich-rendering
# markdown above ~1 MiB (shows raw source). Warn as the file approaches that ceiling
# and hard-error once past it, so the Full-register split (XYZ-PILOT-336,
# scripts/split_tracker_archive.py) is run before browser rendering breaks.
_size_bytes = len(src.encode("utf-8"))
if _size_bytes >= 1_048_576:
    errors.append(
        f"(K) primary tracker is {_size_bytes/1048576:.2f} MiB (>= 1 MiB) — GitHub renders it as raw source. "
        f"Run scripts/split_tracker_archive.py --apply to archive Resolved>30d sections (XYZ-PILOT-336)."
    )
elif _size_bytes >= 950_000:
    warnings.append(
        f"(K) primary tracker is {_size_bytes/1048576:.2f} MiB — approaching GitHub's rich-render ceiling; "
        f"run scripts/split_tracker_archive.py (XYZ-PILOT-336) soon."
    )

# (L) XYZ-PILOT-336: stateful code-fence balance. An unclosed ``` fence makes GitHub
# render everything after it as one code block (looks like plain text). A naive count
# is fooled by ``` that appear as literal content INSIDE a fenced block, so track state.
_in_fence = False
_fence_char = None
_fence_open_line = None
for _i, _l in enumerate(src.split("\n"), 1):
    _fm = re.match(r"^\s*(`{3,}|~{3,})(.*)$", _l)
    if not _fm:
        continue
    _ticks, _info = _fm.group(1), _fm.group(2).strip()
    if not _in_fence:
        _in_fence, _fence_char, _fence_open_line = True, _ticks[0], _i
    elif _ticks[0] == _fence_char and _info == "":
        _in_fence, _fence_char, _fence_open_line = False, None, None
if _in_fence:
    errors.append(
        f"(L) unclosed code fence — opened at line {_fence_open_line} and never closed; "
        f"GitHub renders everything after it as a code block. Balance the ``` fences."
    )

# Output
result = {
    "tracker": tracker_path,
    "today_utc": today.isoformat(),
    "stats": {
        "detail_sections": len(detail_ids),
        "anchors": len(anchor_ids),
        "top_table_rows": len(top_table_ids),
        "max_id": max(detail_ids) if detail_ids else 0,
    },
    "errors": errors,
    "warnings": warnings,
    "ok": len(errors) == 0,
}

if emit_json:
    print(json.dumps(result, indent=2))
else:
    if result["ok"]:
        print(f"✓ tracker completeness OK — {result['stats']['detail_sections']} details, "
              f"max id XYZ-PILOT-{result['stats']['max_id']:03d}, "
              f"{result['stats']['top_table_rows']} top-table rows")
        if verbose and warnings:
            for w in warnings:
                print(f"  ⚠ {w}")
    else:
        print(f"✗ tracker completeness FAILED — {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        if warnings:
            for w in warnings:
                print(f"  ⚠ {w}", file=sys.stderr)

sys.exit(0 if result["ok"] else 1)
PY
