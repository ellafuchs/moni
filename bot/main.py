"""Entry point: watch the Knesset budget-transfer feed and email a summary per letter.

The pipeline reads top-to-bottom in main():
  1. aggregate — collect candidate letter URLs from the Knesset feed
  2. extract   — one Agent reads each letter's fields + budget table (with a `master`
                 column) and decides relevance (does any code match the master set?)
  3. render    — write a summary PDF for each relevant letter
  4. email     — send the PDFs to the mailing list (currently disabled)

Config (API key, model/provider, mailing list, notifier credentials) comes from
files/config.json; the master program-code set comes from files/master.xlsx.
"""
import csv
import logging
import os
import shutil
import sys
from pathlib import Path

# Make the project root importable so `common` can be found.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
from aggregator import get_pdf_urls
from common.config_manager import ConfigManager
from notifier import Attachment, send_email
from reports import Reports
from utils_function import _slug

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_PATH = "./files/config.json"
MASTER_PATH = "./files/master.xlsx"
OUTPUT_DIR = Path("./files/outputs/")

# Used only when the live Knesset feed returns nothing (offline / testing).
FALLBACK_URL = (
    "https://fs.knesset.gov.il/globaldocs/FINANCE/0e793046-014d-f111-a13e-005056aa7c52/"
    "4_0e793046-014d-f111-a13e-005056aa7c52_13_21560.pdf"
)


def load_config() -> ConfigManager:
    """Load the JSON config (raises FileNotFoundError if CONFIG_PATH is missing)."""
    return ConfigManager(CONFIG_PATH)


def render_summary(result, slug: str, output_dir: Path,
                   relevant_programs: dict | None = None) -> tuple[str, str] | None:
    """Copy the original PDF and render the summary PDF for one extracted letter."""
    letter = result.letter
    try:
        shutil.copyfile(letter.doc.local_path(), output_dir / f"{slug}_original.pdf")
    except Exception:  # noqa: BLE001 - non-fatal, keep the run going
        logger.exception("%s: could not save original PDF", slug)
    try:
        pdf_path = Reports().write_summary(
            output_dir / f"{slug}_summary.pdf",
            fields=result.fields,
            table=result.table,
            letterhead=letter.extract_letterhead(),
            name_column=letter.NAME_COLUMN,
            budget_history=letter.extract_budget_history(),
            source_url=letter.source,
            relevant_programs=relevant_programs,
        )
    except Exception:  # noqa: BLE001 - log and skip, keep the run going
        logger.exception("%s: summary PDF failed", slug)
        return None
    logger.info("%s summary PDF: %s", slug, pdf_path)
    return (pdf_path, f"{slug}_summary")


def email_reports(config, path_names) -> None:
    """Attach every rendered PDF and email them to the configured mailing list."""
    attachments = []
    for path, name in path_names:
        with open(path, "rb") as f:
            attachments.append(Attachment(f.read(), f"{name}.pdf"))
    send_email(
        config.get_notifier_email(),
        config.get_notifier_password(),
        config.get_mailing_list(),
        "Report!!",
        "Here is your budget-letter report.",
        attachments,
    )


def load_logged_pairs(csv_path: Path) -> set[tuple[str, str]]:
    """Read the change-log CSV -> the set of (program_code, letter) pairs already logged.

    The CSV is an append-only log: one row per "program appeared in letter" event. The
    returned set is what makes appends idempotent — a pair already present is never
    written again (dedup across runs). Empty when the file does not exist yet.
    """
    pairs: set[tuple[str, str]] = set()
    if not csv_path.is_file():
        return pairs
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("program_code") or "").strip()
            letter = (row.get("letter") or "").strip()
            if code and letter:
                pairs.add((code, letter))
    return pairs


def append_changes(csv_path: Path, changes: list[tuple[str, str]],
                   master_programs: dict[str, str]) -> None:
    """Append new (program_code, letter) change rows to the log CSV, adding names.

    Writes the header first when the file is new. Each row is one appearance:
    program_code, program_name, letter.
    """
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not csv_path.is_file()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["program_code", "program_name", "letter"])
        for code, letter in changes:
            writer.writerow([code, master_programs.get(code, ""), letter])


def write_counts(counts_path: Path, logged: set[tuple[str, str]],
                 master_programs: dict[str, str]) -> None:
    """Roll the (program, letter) log up into a per-program count CSV (full rewrite).

    One row per program that has appeared: program_code, program_name, letter_count,
    letters. Sorted by count descending, then code. This is the "how many letters per
    program" view, derived from the change log.
    """
    letters_by_code: dict[str, set[str]] = {}
    for code, letter in logged:
        letters_by_code.setdefault(code, set()).add(letter)
    codes = sorted(letters_by_code, key=lambda c: (-len(letters_by_code[c]), c))
    counts_path.parent.mkdir(parents=True, exist_ok=True)
    with open(counts_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["program_code", "program_name", "letter_count", "letters"])
        for code in codes:
            letters = sorted(letters_by_code[code])
            writer.writerow([
                code, master_programs.get(code, ""), len(letters), "; ".join(letters),
            ])


def main(write_tally_csv: bool = False) -> None:
    config = load_config()
    # Sync config's `programs` list from the master file, then use that dict.
    config.load_master(MASTER_PATH)
    master_programs = config.get_ids()  # {code: name}, now sourced from the master
    extractor = agent.Agent(
        master_programs,
        api_key=config.get_api_key(),
        model=config.get_model_name(),
        provider=config.get_model_provider(),
    )

    # 1. aggregate — candidate letter URLs (fall back to one URL when offline).
    urls = get_pdf_urls() or [FALLBACK_URL]
    #urls = [source] + [FALLBACK_URL]  # override: process the one hardcoded letter for now.


    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # The CSV at the configured path is an append-only change log. Load the pairs already
    # logged so we only append genuinely new (program, letter) appearances (dedup).
    tally_path = Path(config.get_tally_csv_path())
    logged = load_logged_pairs(tally_path)

    path_names = []
    changes = []  # new (program_code, letter_slug) pairs discovered this run
    for url in urls:
        slug = _slug(url)
        try:
            result = extractor.extract(url)
        except Exception:  # noqa: BLE001 - skip unreadable PDFs, keep the run going
            logger.warning("%s: skipped, could not extract", slug)
            continue

        # 2. the master check — only summarize letters that touch a master code.
        if not result.relevant:
            logger.info("%s: not relevant (no code in master), skipped", slug)
            continue
        logger.info("%s: master matches (%d): %s", slug,
                    len(result.matched_codes), sorted(result.matched_codes))

        # The master programs (with names) that made THIS letter relevant — shown in the PDF.
        relevant_programs = {
            code: master_programs.get(code, "") for code in sorted(result.matched_codes)
        }

        # Record only genuinely new (program, letter) appearances — a pair already in the
        # log (or already seen this run) is a no-op.
        for code in sorted(result.matched_codes):
            pair = (code, slug)
            if pair not in logged:
                logged.add(pair)
                changes.append(pair)

        # 3. render — one summary PDF per relevant letter.
        rendered = render_summary(result, slug, OUTPUT_DIR, relevant_programs)
        if rendered:
            path_names.append(rendered)

    # Report the changes. The CSVs are only written when explicitly requested via
    # --write-tally; otherwise this is a dry run that just logs what would be added.
    for code, letter in changes:
        logger.info("tally change: program %s += letter %s", code, letter)
    if not write_tally_csv:
        logger.info("program tally: %d change(s) (dry run; pass --write-tally to save "
                    "to %s)", len(changes), tally_path)
    else:
        if changes:
            append_changes(tally_path, changes, master_programs)
        # Always refresh the per-program "how many letters" counts CSV from the full log.
        counts_path = tally_path.with_name(tally_path.stem + "_counts.csv")
        write_counts(counts_path, logged, master_programs)
        logger.info("program tally: %d change(s) appended to %s; counts -> %s",
                    len(changes), tally_path, counts_path)

    # 4. email — send the PDFs to the mailing list.
    # email_reports(config, path_names)


if __name__ == "__main__":
    main(write_tally_csv="--write-tally" in sys.argv)
