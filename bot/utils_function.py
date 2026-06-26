"""Small helpers shared across the entry points."""
from pdf_key_checker import PdfTableExtractor
import re
from pathlib import Path
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


def check_matching(extractor, ids_of_master) -> bool:
    """Return the tracked program codes that this letter changes.

    The result is the intersection of the letter's budget-item codes with
    `ids_of_master`; a truthy (non-empty) result means at least one tracked
    program was changed.
    """
    all_ids = set(extractor.extract_combined_table()[PdfTableExtractor.CODE_COLUMN])
    return all_ids & set(ids_of_master)


def _run_agent(agent, source: str, slug: str, output_dir: str) -> dict:
    """Extract the request fields and write the summary PDF for one agent.

    Returns the extracted {field: value} dict (empty on failure). Each step is
    wrapped so a failure (e.g. a bad OpenAI key or no network) is logged but lets
    the rest of the run finish, rather than aborting.
    """
    path_names = []
    try:
        fields = agent.extract(source)
    except Exception:  # noqa: BLE001 - log, keep going
        logger.exception("%s: %s extraction failed", slug, agent.name)
        return {}

    field_lines = "\n".join(f"  {label}: {value}" for label, value in fields.items())
    logger.info("%s request fields (%s):\n%s", slug, agent.name, field_lines)

    try:
        # render_pdf tags the filename with the method, so "<slug>_summary.pdf"
        # yields "<slug>_summary_parse.pdf" / "_llm.pdf".
        name = f"{slug}_summary"
        path = output_dir / f"{name}.pdf"
        pdf_path = agent.render_pdf(source, path, fields=fields)
        # render_pdf returns the real filename (tagged with the method, e.g.
        # "_parse.pdf"), so record that, not the un-tagged path we asked for.
        path_names.append((pdf_path, name))
        logger.info("%s summary PDF: %s", slug, pdf_path)
    except Exception:  # noqa: BLE001 - log, keep going
        logger.exception("%s: %s summary PDF failed", slug, agent.name)
    return path_names
        

def _slug(url: str) -> str:
    """A short, filesystem-safe id for a PDF URL — its trailing number, else the stem."""
    stem = Path(urlparse(url).path).stem
    tail = re.findall(r"\d+", stem)
    return tail[-1] if tail else (re.sub(r"[^A-Za-z0-9_-]+", "_", stem) or "doc")