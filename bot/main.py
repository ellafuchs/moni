"""Entry point: watch the Knesset budget-transfer feed and email a summary per letter.

The pipeline reads top-to-bottom in main():
  1. aggregate — collect candidate letter URLs from the Knesset feed
  2. extract   — one Agent reads each letter's fields + budget table (with a `master`
                 column) and decides relevance (does any code match the master set?)
  3. render    — write a summary PDF for each relevant letter
  4. email     — send the PDFs to the mailing list

    uv run python bot/main.py                      # email the config.json mailing list
    uv run python bot/main.py --to me@example.com  # test run: email only this address
    uv run python bot/main.py --no-email           # render PDFs, send nothing
    uv run python bot/main.py --all                # re-read letters already handled

Letters already handled are remembered in files/outputs/processed.json and skipped on
the next run, so a daily run touches only what is new; --all ignores that memory.

Non-secret config (model, mailing list, schedule) comes from files/config.json; secrets
(OpenAI key, Gmail OAuth) from .env; the master program-code set from files/master.xlsx.
"""
import argparse
import json
import logging
import os
import shutil
import sys
from datetime import date
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
# httpx logs every request line (incl. the OpenAI call) at INFO — quiet it so it
# doesn't drown the app's own logs; keep warnings/errors.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

CONFIG_PATH = "./files/config.json"
MASTER_PATH = "./files/master.xlsx"
OUTPUT_DIR = Path("./files/outputs/")

# Used only when the live Knesset feed returns nothing (offline / testing).
FALLBACK_URL = (
    "https://fs.knesset.gov.il/globaldocs/FINANCE/0e793046-014d-f111-a13e-005056aa7c52/"
    "4_0e793046-014d-f111-a13e-005056aa7c52_13_21560.pdf"
)
PROCESSED_PATH = OUTPUT_DIR / "processed.json"


def load_processed(path: Path = PROCESSED_PATH) -> dict:
    """{slug: {"date": ..., "relevant": bool}} of letters already handled; {} if none."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_processed(processed: dict, slug: str, relevant: bool,
                       path: Path = PROCESSED_PATH) -> None:
    """Record one handled letter and write the memory back right away."""
    processed[slug] = {"date": date.today().isoformat(), "relevant": relevant}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(processed, ensure_ascii=False, indent=1), encoding="utf-8")


def render_summary(result, slug: str, output_dir: Path,
                   relevant_programs: dict | None = None,
                   master_names: dict | None = None) -> tuple[str, str] | None:
    """Copy the original PDF and render the summary PDF for one extracted letter."""
    letter = result.letter
    try:
        shutil.copyfile(letter.doc.local_path(), output_dir / f"{slug}_original.pdf")
    except Exception:  # noqa: BLE001 - non-fatal, keep the run going
        logger.exception("%s: could not save original PDF", slug)
    # What the extraction produced, as JSON next to the PDF: lets us re-render or compare
    # the text without another model call.
    try:
        import json
        (output_dir / f"{slug}_extraction.json").write_text(json.dumps({
            "request_id": slug, "source": str(letter.source),
            "fields": result.fields.model_dump(), "coalition_reason": result.coalition_reason,
            "matched_codes": sorted(result.matched_codes), "relevant": result.relevant,
            "llm_usage": result.llm_usage,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001 - never fail the run over the side file
        logger.exception("%s: could not write extraction json", slug)
    try:
        pdf_path = Reports().write_summary(
            output_dir / f"{slug}_summary.pdf",
            fields=result.fields,
            table=result.table,
            letterhead=letter.extract_letterhead(),
            name_column=letter.NAME_COLUMN,
            budget_history=letter.extract_budget_history(),
            source_url=letter.source,
            llm_usage=result.llm_usage,
            relevant_programs=relevant_programs,
            request_id=slug,
            coalition_reason=result.coalition_reason,
            master_names=master_names,
        )
    except Exception:  # noqa: BLE001 - log and skip, keep the run going
        logger.exception("%s: summary PDF failed", slug)
        return None
   # logger.info("%s summary PDF: %s", slug, pdf_path)
    return (pdf_path, f"{slug}_summary")


EMAIL_SUBJECT = "סיכום פנייה תקציבית"


def email_subject(path_names, today: date | None = None) -> str:
    """'סיכום פנייה תקציבית DD.MM.YYYY': the date of the run, no request numbers."""
    return f"{EMAIL_SUBJECT} {(today or date.today()).strftime('%d.%m.%Y')}"


def email_body(path_names) -> str:
    """The request numbers go in the body, one per line, so the subject stays short."""
    ids = [name.removesuffix("_summary") for _, name in path_names]
    head = ("מצורף סיכום אוטומטי של פניות תקציביות הרלוונטיות לתוכניות הקרן, "
            "כל פנייה כ-PDF וכדף HTML (בו הקישורים לחיצים).")
    if not ids:
        return head
    plural = "פניות" if len(ids) > 1 else "פנייה"
    return head + f"\n\n{plural} בסיכום זה ({len(ids)}):\n" + "\n".join(f"• {i}" for i in ids)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the budget-letter pipeline.")
    parser.add_argument(
        "--to", action="append", metavar="EMAIL",
        help="send the report to this address instead of the config.json mailing list "
             "(repeat for several addresses)")
    parser.add_argument(
        "--no-email", action="store_true",
        help="render the summary PDFs but do not send any email")
    parser.add_argument(
        "--all", action="store_true",
        help="handle every letter in the feed, even ones remembered as already handled")
    return parser.parse_args(argv)


def resolve_recipients(override: list[str] | None, mailing_list: list[str] | None) -> list[str]:
    """--to wins over the config mailing list; never None."""
    return list(override or mailing_list or [])


def email_reports(sender: str | None, recipients: list[str], path_names) -> bool:
    """Attach every rendered PDF and email them. Returns True iff an email was sent."""
    if not path_names:
        logger.info("no relevant letters were rendered, nothing to send")
        return False
    if not recipients:
        logger.warning("no recipients (mailing list empty and no --to), nothing sent")
        return False
    attachments = []
    for path, name in path_names:
        with open(path, "rb") as f:
            attachments.append(Attachment(f.read(), f"{name}.pdf"))
        html = Path(path).with_suffix(".html")
        if html.is_file():  # the same page as HTML — clickable links, easy to forward
            attachments.append(Attachment(html.read_bytes(), f"{name}.html"))
    send_email(sender, recipients, email_subject(path_names), email_body(path_names), attachments)
    logger.info("emailed %d PDF(s) to %s", len(attachments), ", ".join(recipients))
    return True


def main(argv=None) -> None:
    args = parse_args(argv)
    config = ConfigManager(CONFIG_PATH)
    # Sync config's `programs` list from the master file, then use that dict.
    config.load_master(MASTER_PATH)
    master_programs = config.get_ids()  # {code: name}, now sourced from the master
    master_names = ConfigManager.read_master_names(MASTER_PATH)  # full names for the page
    extractor = agent.Agent(
        master_programs,
        api_key=config.get_api_key(),
        model=config.get_model_name(),
        provider=config.get_model_provider(),
        fallback_model=config.get_model_fallback(),
    )

    # 1. aggregate — candidate letter URLs (fall back to one URL when offline).
    urls = get_pdf_urls() or [FALLBACK_URL]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    processed = {} if args.all else load_processed()
    path_names = []
    for url in urls:
        slug = _slug(url)
        if slug in processed:
            logger.info("%s: already handled on %s, skipped", slug, processed[slug].get("date"))
            continue
        try:
            result = extractor.extract(url)
        except Exception:  # noqa: BLE001 - skip unreadable PDFs, keep the run going
            logger.warning("%s: skipped, could not extract (will retry next run)", slug)
            continue
        remember_processed(processed, slug, result.relevant)

        # 2. the master check — only summarize letters that touch a master code.
        if not result.relevant:
            logger.info("%s: not relevant (no code in master), skipped", slug)
            continue
        #logger.info("%s: master matches (%d): %s", slug,
        #            len(result.matched_codes), sorted(result.matched_codes))

        # The master programs (with names) that made THIS letter relevant — shown in the PDF.
        relevant_programs = {
            code: master_programs.get(code, "") for code in sorted(result.matched_codes)
        }

        # 3. render — one summary PDF per relevant letter.
        rendered = render_summary(result, slug, OUTPUT_DIR, relevant_programs, master_names)
        if rendered:
            path_names.append(rendered)

    # 4. email — send the PDFs to the mailing list (or to --to for a test run).
    if args.no_email:
        logger.info("--no-email: %d PDF(s) rendered, nothing sent", len(path_names))
    else:
        email_reports(
            config.get_notifier_email(),
            resolve_recipients(args.to, config.get_mailing_list()),
            path_names,
        )


if __name__ == "__main__":
    main()
