#!/usr/bin/env python3
"""Generate the promptfoo rules test cases from the single golden question list.

The Tier A retrieval eval and the Tier B promptfoo answer eval must share ONE
curated question source: ``core/data/eval/rules_golden.json``. This script turns
each golden entry into a promptfoo test case (``question`` + ``expected_rule_ids``
as vars) written to ``evals/cases/rules.yaml``. The assertions themselves live in
the promptfoo config's ``defaultTest`` (structural + cited-rules), so cases carry
only data, never duplicated logic.

Re-run whenever the golden set changes:

    python scripts/build_promptfoo_rules_cases.py

The output file is generated; edit the golden set, not rules.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = _ROOT / "core" / "data" / "eval" / "rules_golden.json"
OUT_PATH = _ROOT / "evals" / "cases" / "rules.yaml"

_HEADER = (
    "# GENERATED FILE — do not edit by hand.\n"
    "# Source: core/data/eval/rules_golden.json (the single curated question list\n"
    "# shared with the Tier A retrieval eval). Regenerate with:\n"
    "#     python scripts/build_promptfoo_rules_cases.py\n"
    "#\n"
    "# Assertions live in defaultTest in promptfooconfig.rules.yaml (structural\n"
    "# pipeline_schema + cited_rules); each case here carries only its question and\n"
    "# the expected LRR rule_ids the grounded answer should cite.\n\n"
)


def build_cases(golden: dict) -> list[dict]:
    cases = []
    for c in golden["cases"]:
        cases.append(
            {
                "description": f"{c['id']}: {c['question']}",
                "vars": {
                    "question": c["question"],
                    "expected_rule_ids": list(c["expected_rule_ids"]),
                    # In-corpus golden questions expect a grounded answer; a case
                    # may set "grounded": false to assert the ungrounded path.
                    "grounded_expected": bool(c.get("grounded", True)),
                },
            }
        )
    return cases


def main() -> int:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = build_cases(golden)
    body = yaml.safe_dump(cases, sort_keys=False, allow_unicode=True, default_flow_style=False)
    OUT_PATH.write_text(_HEADER + body, encoding="utf-8")
    print(f"Wrote {OUT_PATH} — {len(cases)} rules cases from {GOLDEN_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
