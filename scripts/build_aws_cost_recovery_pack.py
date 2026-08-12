#!/usr/bin/env python3
"""Build an <PILOT_NAME> AWS cost recovery evidence pack.

The generated CSV/JSON follows the shape of AWS Pricing Calculator exports so
operators can use it as a calculator-entry worksheet. AWS Pricing Calculator is
still a web calculator; this script does not upload or create estimates.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys
import uuid


REPO = Path(__file__).resolve().parents[1]
DEFAULT_BOM = (
    REPO
    / "docs"
    / "60-evidence-inventory"
    / "aws-pricing"
    / "XYZ_PILOT_US_EAST_1_AWS_RESOURCE_BOM_2026-06-19.md"
)
DEFAULT_OUT_DIR = (
    REPO / "docs" / "60-evidence-inventory" / "aws-pricing" / "cost-recovery"
)
DEFAULT_ACCOUNT_ID = "<AWS_ACCOUNT_ID>"
DEFAULT_REGION = "<AWS_REGION>"
DEFAULT_PROFILE = "default"
DEFAULT_GROUP = "<PILOT_NAME>"
DEFAULT_PROJECT_TAG = "XYZPILOT"
DEFAULT_START_DATE = "2026-06-11"

CSV_FIELDS = [
    "id",
    "name",
    "createdAt",
    "expiresAt",
    "rateType",
    "rateTimestamp",
    "status",
    "totalCostHistorical",
    "totalCostEstimated",
    "currency",
    "exportCreatedAt",
    "item/serviceCode",
    "item/usageType",
    "item/operation",
    "item/location",
    "item/usageId",
    "item/usageAccountId",
    "item/accountName",
    "item/group",
    "item/quantity/unit",
    "item/quantity/amount",
    "item/costSummary/cost",
    "item/costSummary/currency",
    "item/costSummary/status",
    "item/historicalUsage",
]

SERVICE_LABELS = {
    "AmazonEC2": "Amazon EC2",
    "AmazonS3": "Amazon S3",
    "AWSLambda": "AWS Lambda",
    "AWSEvents": "Amazon EventBridge",
    "AmazonCognito": "Amazon Cognito",
    "AmazonSES": "Amazon SES",
    "AmazonSNS": "Amazon SNS",
    "AWSSecretsManager": "AWS Secrets Manager",
    "AmazonCloudWatch": "Amazon CloudWatch",
    "AmazonBedrock": "Amazon Bedrock",
}

REGION_USAGE_PREFIX = {
    "<AWS_REGION>": "USE1",
    "<AWS_REGION>": "EUC1",
}


@dataclass(frozen=True)
class CalculatorItem:
    service_code: str
    usage_type: str
    operation: str
    location: str
    group: str
    unit: str
    amount: float
    cost: float = 0.0
    status: str = "requires-calculator-entry"
    historical_usage: str = ""
    note: str = ""

    @property
    def usage_id(self) -> str:
        raw = "|".join(
            [
                self.service_code,
                self.usage_type,
                self.operation,
                self.location,
                self.group,
                self.unit,
            ]
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"xyz-pilot-cost:{raw}"))

    def as_json_item(self, account_id: str) -> dict[str, object]:
        return {
            "serviceCode": self.service_code,
            "usageType": self.usage_type,
            "operation": self.operation,
            "location": self.location,
            "usageId": self.usage_id,
            "usageAccountId": account_id,
            "accountName": "",
            "group": self.group,
            "quantity": {"unit": self.unit, "amount": round_float(self.amount)},
            "costSummary": {
                "cost": round_float(self.cost),
                "currency": "USD",
                "status": self.status,
            },
            "historicalUsage": self.historical_usage,
        }


def round_float(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return round(float(value), 6)


def parse_utc_timestamp(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC).replace(microsecond=0)
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def timestamp_for_path(generated_at: datetime) -> str:
    return generated_at.strftime("%Y-%m-%dT%H%M%SZ")


def iso_z(generated_at: datetime) -> str:
    return generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def date_after_today(generated_at: datetime) -> str:
    return (generated_at + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")


def usage_prefix(region: str) -> str:
    return REGION_USAGE_PREFIX.get(region, region.upper().replace("-", ""))


def read_bom_facts(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")

    def first_int(pattern: str, default: int) -> int:
        match = re.search(pattern, text, re.MULTILINE)
        return int(match.group(1).replace(",", "")) if match else default

    def first_float(pattern: str, default: float) -> float:
        match = re.search(pattern, text, re.MULTILINE)
        return float(match.group(1).replace(",", "")) if match else default

    instance_type = "r6i.xlarge"
    match = re.search(r"\| Instance type \| `([^`]+)` \|", text)
    if match:
        instance_type = match.group(1)

    volumes = re.findall(r"\| `vol-[^`]+` \| [^|]+ \| `gp3` \| ([0-9,]+) GiB \|", text)
    ebs_gib = sum(int(value.replace(",", "")) for value in volumes) or 200

    lambda_count = len(re.findall(r"\| `xyz-pilot-[^`]+` \| `python", text)) or 12

    return {
        "instance_type": instance_type,
        "ebs_gib": ebs_gib,
        "s3_versioned_bytes": first_int(
            r"S3 calculator storage input: `([0-9,]+)` bytes", 1_906_063_670
        ),
        "lambda_count": lambda_count,
        "cognito_users": first_int(r"Cognito \| 1 user pool, ([0-9,]+) users", 11),
        "ses_last_24h": first_int(r"sent last 24h ([0-9,]+)", 34),
        "secrets": first_int(r"Secrets Manager \| ([0-9,]+) secret", 1),
        "cloudwatch_stored_bytes": first_int(
            r"Total stored bytes: `([0-9,]+)`", 198_488
        ),
        "eventbridge_enabled_rules": first_int(
            r"EventBridge rules \| 8 rules, ([0-9,]+) enabled", 6
        ),
        "bedrock_monthly_input_tokens": 0,
        "bedrock_monthly_output_tokens": 0,
        "public_ipv4_count": first_int(r"Elastic/public IPv4 \| ([0-9,]+) associated", 1),
        "s3_bucket_count": first_int(r"S3 buckets \| ([0-9,]+) buckets", 4),
        "sns_topic_count": first_int(r"SNS \| ([0-9,]+) topic", 1),
        "ssm_standard_parameters": first_int(
            r"SSM Parameter Store \| ([0-9,]+) Standard parameters", 12
        ),
        "logs_gib_month": first_float(r"Total stored bytes: `([0-9,]+)`", 198_488)
        / (1024**3),
    }


def build_calculator_items(args: argparse.Namespace, facts: dict[str, object]) -> list[CalculatorItem]:
    prefix = usage_prefix(args.region)
    group = args.group
    ec2_hours = float(args.ec2_hours)
    public_ipv4_hours = float(args.public_ipv4_hours or args.ec2_hours)
    s3_gb_month = float(facts["s3_versioned_bytes"]) / (1024**3)
    ses_monthly_messages = float(args.ses_monthly_messages)

    return [
        CalculatorItem(
            "AmazonEC2",
            f"{prefix}-BoxUsage:{facts['instance_type']}",
            "RunInstances",
            args.region,
            group,
            "Hrs",
            ec2_hours,
            note="EC2 Exasol Personal host hours; default is June 2026 weekday schedule.",
        ),
        CalculatorItem(
            "AmazonEC2",
            f"{prefix}-EBS:VolumeUsage.gp3",
            "",
            args.region,
            group,
            "GB-Mo",
            float(facts["ebs_gib"]),
            note="Two gp3 volumes from the BOM; no gp3 IOPS/throughput above baseline.",
        ),
        CalculatorItem(
            "AmazonEC2",
            f"{prefix}-PublicIPv4:InUseAddress",
            "",
            args.region,
            group,
            "Hrs",
            public_ipv4_hours * float(facts["public_ipv4_count"]),
            note="Associated public IPv4 / Elastic IP hours.",
        ),
        CalculatorItem(
            "AmazonS3",
            f"{prefix}-TimedStorage-ByteHrs",
            "",
            args.region,
            group,
            "GB-Mo",
            s3_gb_month,
            note="Versioned object bytes from the BOM, converted to GiB-month worksheet input.",
        ),
        CalculatorItem(
            "AWSLambda",
            f"{prefix}-Request",
            "",
            args.region,
            group,
            "Requests",
            float(args.lambda_requests),
            note=f"{facts['lambda_count']} Lambda functions; request count must come from CloudWatch.",
        ),
        CalculatorItem(
            "AWSLambda",
            f"{prefix}-Lambda-GB-Second",
            "",
            args.region,
            group,
            "GB-Seconds",
            float(args.lambda_gb_seconds),
            note="Measured GB-seconds should come from CloudWatch Lambda metrics.",
        ),
        CalculatorItem(
            "AWSEvents",
            f"{prefix}-Events",
            "",
            args.region,
            group,
            "Events",
            float(args.eventbridge_events),
            note=f"{facts['eventbridge_enabled_rules']} enabled rules; enter monthly events/invocations.",
        ),
        CalculatorItem(
            "AmazonCognito",
            f"{prefix}-CognitoUserPoolsMAU",
            "",
            args.region,
            group,
            "MAU",
            float(args.cognito_mau or facts["cognito_users"]),
            note="Configured users are a ceiling; calculator may require monthly active users.",
        ),
        CalculatorItem(
            "AmazonSES",
            f"{prefix}-EmailMessages",
            "",
            args.region,
            group,
            "Messages",
            ses_monthly_messages,
            note="Default is last-24h SES count from BOM extrapolated to 30 days.",
        ),
        CalculatorItem(
            "AmazonSNS",
            f"{prefix}-Requests-Tier1",
            "",
            args.region,
            group,
            "Requests",
            float(args.sns_requests),
            note=f"{facts['sns_topic_count']} topic in the BOM; enter monthly publish/delivery volume.",
        ),
        CalculatorItem(
            "AWSSecretsManager",
            f"{prefix}-Secrets",
            "",
            args.region,
            group,
            "Secrets",
            float(facts["secrets"]),
            note="Secret-month count from BOM; API calls remain usage-dependent.",
        ),
        CalculatorItem(
            "AmazonCloudWatch",
            f"{prefix}-DataProcessing-Bytes",
            "",
            args.region,
            group,
            "GB",
            float(args.cloudwatch_ingested_gb),
            note="Enter monthly CloudWatch Logs ingestion; current stored bytes are tiny.",
        ),
        CalculatorItem(
            "AmazonCloudWatch",
            f"{prefix}-TimedStorage-ByteHrs",
            "",
            args.region,
            group,
            "GB-Mo",
            float(facts["logs_gib_month"]),
            note="Stored CloudWatch Logs bytes from BOM, converted to GiB-month worksheet input.",
        ),
        CalculatorItem(
            "AmazonBedrock",
            f"{prefix}-BedrockInputTokens",
            "InvokeModel",
            args.region,
            group,
            "Input tokens",
            float(args.bedrock_input_tokens),
            note="Monthly input tokens for chatbot, daily status, and swarm probes.",
        ),
        CalculatorItem(
            "AmazonBedrock",
            f"{prefix}-BedrockOutputTokens",
            "InvokeModel",
            args.region,
            group,
            "Output tokens",
            float(args.bedrock_output_tokens),
            note="Monthly output tokens for chatbot, daily status, and swarm probes.",
        ),
    ]


def build_export_document(
    args: argparse.Namespace, generated_at: datetime, items: list[CalculatorItem]
) -> dict[str, object]:
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"xyz-pilot-cost-pack:{args.account_id}:{args.region}")),
        "name": args.estimate_name,
        "createdAt": iso_z(generated_at),
        "expiresAt": date_after_today(generated_at),
        "rateType": "BEFORE_DISCOUNTS",
        "rateTimestamp": iso_z(generated_at),
        "status": "WORKSHEET",
        "totalCostHistorical": 0,
        "totalCostEstimated": round_float(sum(item.cost for item in items)),
        "currency": "USD",
        "exportCreatedAt": iso_z(generated_at),
        "items": [item.as_json_item(args.account_id) for item in items],
    }


def write_csv(path: Path, export_doc: dict[str, object]) -> None:
    rows = []
    base = {key: export_doc[key] for key in CSV_FIELDS[:11]}
    for item in export_doc["items"]:
        assert isinstance(item, dict)
        quantity = item["quantity"]
        cost_summary = item["costSummary"]
        assert isinstance(quantity, dict)
        assert isinstance(cost_summary, dict)
        row = {
            **base,
            "item/serviceCode": item["serviceCode"],
            "item/usageType": item["usageType"],
            "item/operation": item["operation"],
            "item/location": item["location"],
            "item/usageId": item["usageId"],
            "item/usageAccountId": item["usageAccountId"],
            "item/accountName": item["accountName"],
            "item/group": item["group"],
            "item/quantity/unit": quantity["unit"],
            "item/quantity/amount": quantity["amount"],
            "item/costSummary/cost": cost_summary["cost"],
            "item/costSummary/currency": cost_summary["currency"],
            "item/costSummary/status": cost_summary["status"],
            "item/historicalUsage": item.get("historicalUsage", ""),
        }
        rows.append(row)

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def cost_explorer_commands(args: argparse.Namespace) -> str:
    region_filter = json.dumps({"Dimensions": {"Key": "REGION", "Values": [args.region]}})
    tag_filter = json.dumps({"Tags": {"Key": "exa:Project", "Values": [args.project_tag]}})
    return f"""#!/usr/bin/env bash
set -euo pipefail

# Read-only AWS Cost Explorer evidence commands for the <PILOT_NAME> cost claim.
# Cost Explorer is a global billing API; keep --region <AWS_REGION> for AWS CLI endpoint selection.

aws --profile {args.profile} --region <AWS_REGION> ce get-cost-and-usage \\
  --time-period Start={args.start_date},End={args.end_date} \\
  --granularity MONTHLY \\
  --metrics UnblendedCost \\
  --group-by Type=DIMENSION,Key=SERVICE \\
  --output json --no-cli-pager

aws --profile {args.profile} --region <AWS_REGION> ce get-cost-and-usage \\
  --time-period Start={args.start_date},End={args.end_date} \\
  --granularity MONTHLY \\
  --metrics UnblendedCost \\
  --filter '{region_filter}' \\
  --group-by Type=DIMENSION,Key=SERVICE Type=DIMENSION,Key=USAGE_TYPE \\
  --output json --no-cli-pager

aws --profile {args.profile} --region <AWS_REGION> ce get-cost-and-usage \\
  --time-period Start={args.start_date},End={args.end_date} \\
  --granularity MONTHLY \\
  --metrics UnblendedCost \\
  --filter '{tag_filter}' \\
  --group-by Type=DIMENSION,Key=SERVICE \\
  --output json --no-cli-pager

aws --profile {args.profile} --region <AWS_REGION> ce get-tags \\
  --time-period Start={args.start_date},End={args.end_date} \\
  --tag-key exa:Project \\
  --output json --no-cli-pager
"""


def read_cost_explorer_total(path: Path | None) -> tuple[float | None, str]:
    if not path:
        return None, "Not attached. Run the generated Cost Explorer commands for actual billed spend."
    data = json.loads(path.read_text(encoding="utf-8"))
    total = 0.0
    for period in data.get("ResultsByTime", []):
        for group in period.get("Groups", []):
            amount = group.get("Metrics", {}).get("UnblendedCost", {}).get("Amount")
            if amount is not None:
                total += float(amount)
    return total, f"Loaded from `{path.as_posix()}`."


def read_cost_explorer_tags(path: Path | None) -> tuple[list[str], str]:
    if not path:
        return [], "Not attached."
    data = json.loads(path.read_text(encoding="utf-8"))
    tags = [str(tag) for tag in data.get("Tags", [])]
    return tags, f"Loaded from `{path.as_posix()}`."


def total_cell(path: Path | None, label: str) -> str:
    total, note = read_cost_explorer_total(path)
    if total is None:
        return f"Not embedded. {note}"
    return f"${total:,.2f}. {label}: {note}"


def render_markdown(
    args: argparse.Namespace,
    generated_at: datetime,
    facts: dict[str, object],
    items: list[CalculatorItem],
    outputs: dict[str, Path],
) -> str:
    tags, tag_note = read_cost_explorer_tags(args.cost_explorer_tags_json)
    tag_text = ", ".join(f"`{tag}`" if tag else "`<empty>`" for tag in tags) or "Not embedded"

    lines = [
        "# <PILOT_NAME> AWS Cost Recovery Evidence Pack",
        "",
        f"Generated UTC: `{iso_z(generated_at)}`",
        "",
        f"Scope: AWS account `{args.account_id}`, region `{args.region}`, profile `{args.profile}`, project tag `exa:Project={args.project_tag}`.",
        "",
        f"Source BOM: [`{args.bom.name}`]({relative_link(outputs['markdown'], args.bom)})",
        "",
        "## Executive Summary",
        "",
        "| Evidence area | Status |",
        "|---|---|",
        "| AWS Pricing Calculator upload/API | No native BOM upload path is assumed. Use the generated CSV/JSON as calculator-entry worksheets and retain the web calculator export as final estimate evidence. |",
        f"| Calculator worksheet CSV | [`{outputs['csv'].name}`]({relative_link(outputs['markdown'], outputs['csv'])}) |",
        f"| Calculator worksheet JSON | [`{outputs['json'].name}`]({relative_link(outputs['markdown'], outputs['json'])}) |",
        f"| Cost Explorer commands | [`{outputs['commands'].name}`]({relative_link(outputs['markdown'], outputs['commands'])}) |",
        f"| Account-level Cost Explorer service total | {total_cell(args.cost_explorer_service_json, 'All account services in queried period')} |",
        f"| <AWS_REGION> Cost Explorer usage-type total | {total_cell(args.cost_explorer_region_json, 'Region-filtered usage detail')} |",
        f"| `exa:Project=XYZPILOT` Cost Explorer total | {total_cell(args.cost_explorer_project_json, 'Tag-filtered project spend')} |",
        f"| Cost Explorer `exa:Project` tag values | {tag_text}. {tag_note} |",
        "",
        "The worksheet cost fields are intentionally `0` with status `requires-calculator-entry`.",
        "They are planning inputs, not final AWS Pricing Calculator output and not actual billed spend.",
        "",
        "## Calculator Worksheet Items",
        "",
        "| Service | Usage type | Quantity | Unit | Input status | Note |",
        "|---|---|---:|---|---|---|",
    ]

    for item in items:
        service = SERVICE_LABELS.get(item.service_code, item.service_code)
        note = item.note.replace("|", "\\|")
        lines.append(
            f"| {service} | `{item.usage_type}` | {round_float(item.amount)} | {item.unit} | {item.status} | {note} |"
        )

    lines.extend(
        [
            "",
            "## Static BOM Facts Used",
            "",
            "| Fact | Value |",
            "|---|---:|",
            f"| EC2 instance type | `{facts['instance_type']}` |",
            f"| EC2 scheduled hours default | {args.ec2_hours} |",
            f"| EBS gp3 GiB-month | {facts['ebs_gib']} |",
            f"| S3 versioned bytes | {facts['s3_versioned_bytes']} |",
            f"| Lambda functions | {facts['lambda_count']} |",
            f"| Cognito configured users | {facts['cognito_users']} |",
            f"| SES messages last 24h in BOM | {facts['ses_last_24h']} |",
            f"| Secrets Manager secrets | {facts['secrets']} |",
            f"| CloudWatch stored bytes | {facts['cloudwatch_stored_bytes']} |",
            "",
            "## Manual AWS Pricing Calculator Path",
            "",
            "1. Open <https://calculator.aws/#/>.",
            "2. Create a new estimate named `<PILOT_NAME> <AWS_REGION> cost recovery`.",
            "3. Add the services in the worksheet order above.",
            "4. Use the generated CSV/JSON values as calculator-entry inputs.",
            "5. Fill open usage values from CloudWatch, Bedrock logs, SES/SNS metrics, and Cost Explorer.",
            "6. Export the final AWS Pricing Calculator result as CSV, JSON, and PDF and store it beside this pack.",
            "",
            "## Actual Spend Evidence",
            "",
            "Cost Explorer is authoritative for historical billed spend. Prefer the `exa:Project=XYZPILOT` tag-filtered total when the tag is active and complete; use the broader <AWS_REGION> and account-level files as reconciliation context. Use the generated command file for the claim period:",
            "",
            "```bash",
            f"bash {outputs['commands'].as_posix()}",
            "```",
            "",
            "The expected claim period is explicit and reproducible:",
            "",
            f"- Start date inclusive: `{args.start_date}`",
            f"- End date exclusive: `{args.end_date}`",
            "",
            "## Secret Safety",
            "",
            "This pack must not contain SSM SecureString values, Secrets Manager values, Cognito user attributes, access keys, tokens, or signed URLs.",
        ]
    )
    return "\n".join(lines) + "\n"


def relative_link(source: Path, target: Path) -> str:
    return os.path.relpath(target.resolve(), source.resolve().parent).replace(os.sep, "/")


def validate_against_reference(
    generated_csv: Path,
    generated_json: Path,
    reference_csv: Path | None,
    reference_json: Path | None,
) -> list[str]:
    messages: list[str] = []
    if reference_csv:
        with reference_csv.open("r", encoding="utf-8", newline="") as handle:
            expected = next(csv.reader(handle))
        with generated_csv.open("r", encoding="utf-8", newline="") as handle:
            actual = next(csv.reader(handle))
        if actual != expected:
            raise SystemExit(
                f"generated CSV header does not match reference {reference_csv}: {actual!r} != {expected!r}"
            )
        messages.append(f"CSV header matches {reference_csv}")

    if reference_json:
        expected_keys = sorted(json.loads(reference_json.read_text(encoding="utf-8")).keys())
        actual_keys = sorted(json.loads(generated_json.read_text(encoding="utf-8")).keys())
        if actual_keys != expected_keys:
            raise SystemExit(
                f"generated JSON root keys do not match reference {reference_json}: {actual_keys!r} != {expected_keys!r}"
            )
        messages.append(f"JSON root keys match {reference_json}")
    return messages


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bom", type=Path, default=DEFAULT_BOM)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--generated-at", help="UTC timestamp, e.g. 2026-06-22T07:00:00Z")
    parser.add_argument("--estimate-name", default="<PILOT_NAME> <AWS_REGION> cost recovery")
    parser.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--project-tag", default=DEFAULT_PROJECT_TAG)
    parser.add_argument("--group", default=DEFAULT_GROUP)
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--ec2-hours", type=float, default=264)
    parser.add_argument("--public-ipv4-hours", type=float)
    parser.add_argument("--lambda-requests", type=float, default=0)
    parser.add_argument("--lambda-gb-seconds", type=float, default=0)
    parser.add_argument("--eventbridge-events", type=float, default=0)
    parser.add_argument("--cognito-mau", type=float)
    parser.add_argument("--ses-monthly-messages", type=float, default=34 * 30)
    parser.add_argument("--sns-requests", type=float, default=0)
    parser.add_argument("--cloudwatch-ingested-gb", type=float, default=0)
    parser.add_argument("--bedrock-input-tokens", type=float, default=0)
    parser.add_argument("--bedrock-output-tokens", type=float, default=0)
    parser.add_argument(
        "--cost-explorer-json",
        type=Path,
        help="Backward-compatible alias for --cost-explorer-service-json.",
    )
    parser.add_argument("--cost-explorer-service-json", type=Path)
    parser.add_argument("--cost-explorer-region-json", type=Path)
    parser.add_argument("--cost-explorer-project-json", type=Path)
    parser.add_argument("--cost-explorer-tags-json", type=Path)
    parser.add_argument("--reference-csv", type=Path)
    parser.add_argument("--reference-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    args.bom = args.bom.resolve()
    args.out_dir = args.out_dir.resolve()
    if args.cost_explorer_json and not args.cost_explorer_service_json:
        args.cost_explorer_service_json = args.cost_explorer_json

    if not args.bom.exists():
        raise SystemExit(f"BOM not found: {args.bom}")
    for cost_path in (
        args.cost_explorer_service_json,
        args.cost_explorer_region_json,
        args.cost_explorer_project_json,
        args.cost_explorer_tags_json,
    ):
        if cost_path and not cost_path.exists():
            raise SystemExit(f"Cost Explorer evidence JSON not found: {cost_path}")

    generated_at = parse_utc_timestamp(args.generated_at)
    stamp = timestamp_for_path(generated_at)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    facts = read_bom_facts(args.bom)
    items = build_calculator_items(args, facts)
    export_doc = build_export_document(args, generated_at, items)

    outputs = {
        "json": args.out_dir / f"XYZ_PILOT_AWS_PRICING_CALCULATOR_WORKSHEET_{stamp}.json",
        "csv": args.out_dir / f"XYZ_PILOT_AWS_PRICING_CALCULATOR_WORKSHEET_{stamp}.csv",
        "commands": args.out_dir / f"XYZ_PILOT_AWS_COST_EXPLORER_COMMANDS_{stamp}.sh",
        "markdown": args.out_dir / f"XYZ_PILOT_AWS_COST_RECOVERY_PACK_{stamp}.md",
    }

    outputs["json"].write_text(json.dumps(export_doc, indent=2) + "\n", encoding="utf-8")
    write_csv(outputs["csv"], export_doc)
    outputs["commands"].write_text(cost_explorer_commands(args), encoding="utf-8")
    outputs["markdown"].write_text(
        render_markdown(args, generated_at, facts, items, outputs), encoding="utf-8"
    )

    messages = validate_against_reference(
        outputs["csv"], outputs["json"], args.reference_csv, args.reference_json
    )

    print(f"Wrote {outputs['markdown']}")
    print(f"Wrote {outputs['csv']}")
    print(f"Wrote {outputs['json']}")
    print(f"Wrote {outputs['commands']}")
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
