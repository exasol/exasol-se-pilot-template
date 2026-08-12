from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location(
    "find_orphan_candidates",
    SCRIPTS / "find_orphan_candidates.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


TRACKER = """# Tracker

<a id="xyz-pilot-220"></a>

### XYZ-PILOT-220 - Already shipped title
- **Status:** Resolved 2026-06-01
- **Severity:** minor
- **Area:** Test
"""


FULL_TEMPLATE = """# Candidate handoff

## Candidate - File new issue

- **Proposed severity:** `minor`
- **Proposed area:** `Test`
- **Proposed status:** `Open`

### Top-table row

| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | minor | Test | Add a thing. |

### Full-register detail section

```markdown
<a id="xyz-pilot-NNN"></a>

### XYZ-PILOT-NNN - {title}
- **Status:** Open
- **Severity:** minor
- **Area:** Test
- **Surfaced:** Test.
```
"""


MALFORMED_TEMPLATE = """# Candidate handoff

## Candidate - Broken explicit template

- **Proposed severity:** `minor`

### Top-table row

| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | minor | Test | Add a thing. |

### Full-register detail section

```markdown
### Missing anchor and placeholder
```
"""


class FindOrphanCandidatesTests(unittest.TestCase):
    def _repo(self) -> tempfile.TemporaryDirectory[str]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "docs").mkdir()
        (root / "docs" / "XYZ_PILOT_ISSUE_TRACKER.md").write_text(TRACKER, encoding="utf-8")
        return tmp

    def _candidate_file(self, root: Path, run_id: str, source: str, body: str) -> Path:
        candidate_dir = root / "tests" / "logs" / "runs" / run_id / source
        candidate_dir.mkdir(parents=True)
        candidate_file = candidate_dir / "issue-candidates.md"
        candidate_file.write_text(body, encoding="utf-8")
        return candidate_file

    def test_classifies_already_landed_candidate_by_tracker_title(self) -> None:
        with self._repo() as tmp:
            root = Path(tmp)
            self._candidate_file(
                root,
                "2026-06-20T100000Z",
                "CODEX",
                FULL_TEMPLATE.format(title="Already shipped title"),
            )

            findings = mod.scan(root, "2026-06-03T000000Z")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].classification, "already-landed")
        self.assertEqual(findings[0].duplicate_of, "XYZ-PILOT-220")

    def test_classifies_malformed_explicit_template(self) -> None:
        with self._repo() as tmp:
            root = Path(tmp)
            self._candidate_file(root, "2026-06-20T100100Z", "CODEX", MALFORMED_TEMPLATE)

            findings = mod.scan(root, "2026-06-03T000000Z")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].classification, "structurally-malformed")
        self.assertIn("FilingError", findings[0].reason)

    def test_classifies_unmatched_candidate_as_legitimately_pending(self) -> None:
        with self._repo() as tmp:
            root = Path(tmp)
            self._candidate_file(
                root,
                "2026-06-20T100200Z",
                "CODEX",
                FULL_TEMPLATE.format(title="New pending title"),
            )

            findings = mod.scan(root, "2026-06-03T000000Z")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].classification, "legitimately-pending")

    def test_skips_candidates_with_existing_outcome_marker(self) -> None:
        with self._repo() as tmp:
            root = Path(tmp)
            candidate_file = self._candidate_file(
                root,
                "2026-06-20T100300Z",
                "CODEX",
                FULL_TEMPLATE.format(title="Already shipped title"),
            )
            marker_dir = candidate_file.parent / "candidate-outcomes"
            marker_dir.mkdir()
            (marker_dir / "candidate-001.DENIED.DUPLICATE.XYZ-PILOT-220.md").write_text(
                "decided\n",
                encoding="utf-8",
            )

            findings = mod.scan(root, "2026-06-03T000000Z")

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
