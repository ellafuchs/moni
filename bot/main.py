import sys
import os
import logging
from pathlib import Path

# Make the project root (moni/) importable so `common` can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregator import get_pdf_urls
from common.config_manager import ConfigManager
from pdf_key_checker import PdfTableExtractor
from utils_function import check_matching, _run_agent, _slug
from agents import ParserAgent, LlmAgent
from notifier import send_email, Attachment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


PARSER = ParserAgent()
LLM = LlmAgent()

OUTPUT_DIR = Path("./files/outputs/")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = "./files/config.json"
# 1 aggregator
#url_list = get_pdf_urls()
url_list = [
    "https://fs.knesset.gov.il/globaldocs/FINANCE/0e793046-014d-f111-a13e-005056aa7c52/4_0e793046-014d-f111-a13e-005056aa7c52_13_21560.pdf",
]

# 2 config parser
config = ConfigManager(CONFIG_PATH)

# Make config
config.load_master("./files/master.xlsx")
config.set_mailing_list(["ellapomela@gmail.com"])
config.set_notifier_email("excelberl@gmail.com")
config.set_notifier_password("mzhm xnwo etwx gfej")

# 3 extracotr + data preocessing

requests = []

ids = config.get_ids()

for url in url_list:
    extractor = PdfTableExtractor(url)
    slug = _slug(url)
    try:
        if check_matching(extractor, ids):
            requests.append((url, slug))
    except Exception:  # noqa: BLE001 - skip unreadable PDFs (e.g. scanned/no text layer)
        logger.warning("%s: skipped, could not read tables (no text layer?)", slug)

# Generate report
path_names = []
for url, slug in requests:
    path_names.extend(_run_agent(PARSER, url, slug, OUTPUT_DIR))
    # llm_fields = _run_agent(LLM, url, slug)


# Email report
reports: list[Attachment] = []

for path, name in path_names:
    with open(path, "rb") as f:
        reports.append(Attachment(f.read(), name))

send_email(
    config.get_notifier_email(),
    config.get_notifier_password(),
    config.get_mailing_list(),
    "Report!!",
    "Itsa me mario!!!\n Here is ur reportaaaq",
    reports,
    )



