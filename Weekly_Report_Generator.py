"""Create and email a consolidated report from the CSV files in the input folder."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("weekly_report_generator")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


class ReportError(Exception):
    """An expected, user-actionable report error."""


class ConfigurationError(ReportError):
    """Required settings are missing or invalid."""


class AIUnavailable(ReportError):
    """The AI trend comment could not be generated."""


def load_settings() -> None:
    """Load simple KEY=value settings without overriding Windows environment values."""
    settings_file = BASE_DIR / ".env"
    if not settings_file.exists():
        return
    try:
        lines = settings_file.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"Could not read {settings_file.name}: {exc}") from exc

    for line in lines:
        setting = line.strip()
        if not setting or setting.startswith("#") or "=" not in setting:
            continue
        key, value = setting.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def configure_logging() -> None:
    log_dir = BASE_DIR / os.getenv("LOG_FOLDER", "logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=log_dir / "weekly_report_generator.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )
    except OSError as exc:
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        LOG.warning("Could not create the log file; logging to the screen: %s", exc)


def parse_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in {"true", "yes", "1"}:
        return True
    if value in {"false", "no", "0"}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


def parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().replace(",", "")
    cleaned = re.sub(r"^[A-Za-z]{2,4}\s*", "", cleaned)
    if not cleaned:
        return None
    try:
        number = Decimal(cleaned)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def is_identifier_column(column: str) -> bool:
    name = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
    return (
        name in {"id", "phone", "telephone", "mobile", "postcode", "postal_code"}
        or name.endswith(("_id", "_code", "_phone", "_postcode", "_postal_code"))
    )


def load_csv_files(input_dir: Path) -> tuple[list[Path], list[str], list[dict[str, str]], list[str]]:
    try:
        files = sorted(
            (path for path in input_dir.glob("*.csv") if path.is_file()),
            key=lambda path: path.name.lower(),
        )
    except OSError as exc:
        raise ReportError(f"Could not list CSV files in {input_dir}: {exc}") from exc
    if not files:
        raise ReportError(f"No CSV files were found in {input_dir}.")

    headers: list[str] = []
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    readable_files: list[Path] = []

    for path in files:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                if not reader.fieldnames or not any(name and name.strip() for name in reader.fieldnames):
                    warnings.append(f"{path.name}: skipped because it has no column headings.")
                    continue
                file_headers = [name.strip() for name in reader.fieldnames if name and name.strip()]
                for header in file_headers:
                    if header not in headers:
                        headers.append(header)

                file_rows: list[dict[str, str]] = []
                malformed_rows = 0
                for row in reader:
                    if None in row:
                        malformed_rows += 1
                        continue
                    file_rows.append(
                        {
                            key.strip(): (value or "").strip()
                            for key, value in row.items()
                            if key and key.strip()
                        }
                    )
                rows.extend(file_rows)
                readable_files.append(path)
                if malformed_rows:
                    warnings.append(
                        f"{path.name}: skipped {malformed_rows} row(s) with extra values."
                    )
                if not file_rows:
                    warnings.append(f"{path.name}: contained no data rows.")
        except (OSError, UnicodeError, csv.Error) as exc:
            warnings.append(f"{path.name}: could not be read and was left in place ({exc}).")
            LOG.error("Could not read %s: %s", path.name, exc)

    if not readable_files or not rows:
        raise ReportError("No readable CSV data rows were found; source files were left in place.")
    return readable_files, headers, rows, warnings


def calculate_totals(headers: list[str], rows: list[dict[str, str]]) -> dict[str, Decimal]:
    configured = os.getenv("TOTAL_COLUMNS", "").strip()
    if configured:
        columns = [column.strip() for column in configured.split(",") if column.strip()]
        missing = [column for column in columns if column not in headers]
        if missing:
            raise ConfigurationError(
                "TOTAL_COLUMNS names were not found in the CSV headings: " + ", ".join(missing)
            )
    else:
        columns = [column for column in headers if not is_identifier_column(column)]

    totals: dict[str, Decimal] = {}
    for column in columns:
        values = [row.get(column, "") for row in rows if row.get(column, "").strip()]
        parsed_values = [parse_decimal(value) for value in values]
        if values and all(value is not None for value in parsed_values):
            totals[column] = sum(
                (value for value in parsed_values if value is not None),
                start=Decimal("0"),
            )
    return totals


def format_total(value: Decimal) -> str:
    return format(value.normalize(), "f")


def make_merged_csv(headers: list[str], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def generate_trend_comment(totals: dict[str, Decimal], row_count: int) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise AIUnavailable("ANTHROPIC_API_KEY is not set.")

    metrics = {name: format_total(value) for name, value in list(totals.items())[:20]}
    payload = {
        "model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        "max_tokens": 120,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write one cautious, plain-language sentence about the pattern in these "
                    "weekly aggregate totals. Do not claim a week-over-week change because "
                    "only one week's totals are provided. If there is not enough information "
                    "to identify a pattern, say so. Do not invent causes or figures. "
                    f"Rows: {row_count}. Totals: {json.dumps(metrics, ensure_ascii=True)}"
                ),
            }
        ],
    }
    request = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "anthropic-version": ANTHROPIC_API_VERSION,
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result: Any = json.loads(response.read().decode("utf-8"))
        content = result["content"]
        comment = " ".join(
            item["text"].strip()
            for item in content
            if item.get("type") == "text" and item.get("text", "").strip()
        )
        if not comment:
            raise AIUnavailable("The AI service returned an empty comment.")
        return comment
    except urllib.error.HTTPError as exc:
        raise AIUnavailable(f"AI service returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AIUnavailable(f"Could not reach the AI service: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise AIUnavailable(f"Could not read the AI service response: {exc}") from exc


def build_message(
    totals: dict[str, Decimal],
    rows: list[dict[str, str]],
    source_files: list[Path],
    merged_csv: bytes,
    warnings: list[str],
    trend_comment: str,
) -> EmailMessage:
    report_date = date.today().isoformat()
    report_lines = [
        "Weekly consolidated report",
        f"Report date: {report_date}",
        f"CSV files included: {len(source_files)}",
        f"Data rows included: {len(rows)}",
        "",
        "Totals:",
    ]
    if totals:
        report_lines.extend(f"- {name}: {format_total(value)}" for name, value in totals.items())
    else:
        report_lines.append("- No numeric columns were available to total.")
    report_lines.extend(["", f"AI trend comment: {trend_comment}"])
    if warnings:
        report_lines.extend(["", "Processing notes:"])
        report_lines.extend(f"- {warning}" for warning in warnings)
    report_lines.extend(
        [
            "",
            "The consolidated CSV is attached. The AI comment uses aggregate totals only.",
        ]
    )

    recipient = os.getenv("REPORT_RECIPIENT", "").strip()
    sender = os.getenv("REPORT_SENDER", os.getenv("SMTP_USER", "")).strip()
    if not recipient or not sender:
        raise ConfigurationError("Set REPORT_RECIPIENT and REPORT_SENDER (or SMTP_USER) in .env.")

    message = EmailMessage()
    message["Subject"] = f"Weekly report - {report_date}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content("\n".join(report_lines))
    message.add_attachment(
        merged_csv,
        maintype="text",
        subtype="csv",
        filename=f"weekly_report_{report_date}.csv",
    )
    return message


def send_email(message: EmailMessage) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    if not host or not username or not password:
        raise ConfigurationError("Set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD in .env.")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as exc:
        raise ConfigurationError("SMTP_PORT must be a number.") from exc
    use_tls = parse_bool("SMTP_USE_TLS", True)
    use_ssl = parse_bool("SMTP_USE_SSL", False)
    if use_tls and use_ssl:
        raise ConfigurationError("Use either SMTP_USE_TLS or SMTP_USE_SSL, not both.")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context()) as server:
                server.login(username, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                server.login(username, password)
                server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise ReportError(f"Email could not be sent: {exc}") from exc


def archive_files(files: list[Path], merged_csv: bytes) -> None:
    run_stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archive_dir = BASE_DIR / os.getenv("ARCHIVE_FOLDER", "archive") / run_stamp
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            destination = archive_dir / path.name
            if destination.exists():
                raise ReportError(
                    f"Archive already contains {path.name}; source file was not moved."
                )
            path.replace(destination)
        merged_path = archive_dir / f"weekly_report_{date.today().isoformat()}.csv"
        merged_path.write_bytes(merged_csv)
    except OSError as exc:
        raise ReportError(
            f"The email was sent, but source files could not be fully archived: {exc}"
        ) from exc


def run_report() -> None:
    load_settings()
    input_dir = BASE_DIR / os.getenv("INPUT_FOLDER", "input")
    if not input_dir.exists():
        raise ReportError(f"Input folder does not exist: {input_dir}")

    files, headers, rows, warnings = load_csv_files(input_dir)
    totals = calculate_totals(headers, rows)
    merged_csv = make_merged_csv(headers, rows)
    try:
        trend_comment = generate_trend_comment(totals, len(rows))
    except AIUnavailable as exc:
        trend_comment = "AI comment unavailable; review the processing notes."
        warnings.append(f"AI trend comment was not generated ({exc}).")
        LOG.error("AI trend comment was not generated: %s", exc)

    message = build_message(totals, rows, files, merged_csv, warnings, trend_comment)
    send_email(message)
    LOG.info("Weekly report email sent with %s rows from %s file(s).", len(rows), len(files))
    archive_files(files, merged_csv)
    LOG.info("Archived processed CSV files in %s.", BASE_DIR / os.getenv("ARCHIVE_FOLDER", "archive"))


def main() -> int:
    configure_logging()
    try:
        run_report()
    except ReportError as exc:
        LOG.error("%s", exc)
        return 1
    except (OSError, UnicodeError, csv.Error) as exc:
        LOG.error("Report could not be completed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())