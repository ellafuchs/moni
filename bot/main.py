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
            llm_usage=result.llm_usage,
            relevant_programs=relevant_programs,
        )
    except Exception:  # noqa: BLE001 - log and skip, keep the run going
        logger.exception("%s: summary PDF failed", slug)
        return None
   # logger.info("%s summary PDF: %s", slug, pdf_path)
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


def main() -> None:
    config = ConfigManager(CONFIG_PATH)
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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    path_names = []
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
        #logger.info("%s: master matches (%d): %s", slug,
        #            len(result.matched_codes), sorted(result.matched_codes))

        # The master programs (with names) that made THIS letter relevant — shown in the PDF.
        relevant_programs = {
            code: master_programs.get(code, "") for code in sorted(result.matched_codes)
        }

        # 3. render — one summary PDF per relevant letter.
        rendered = render_summary(result, slug, OUTPUT_DIR, relevant_programs)
        if rendered:
            path_names.append(rendered)

    # 4. email — send the PDFs to the mailing list.
    # email_reports(config, path_names)


if __name__ == "__main__":
    main()
