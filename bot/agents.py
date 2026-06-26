"""Two interchangeable extraction agents for budget-transfer letters.

Both agents expose the same tiny interface so callers (e.g. test_pdfs.py) can run
them side by side and compare:

    agent.name                       -> "parse" | "llm"
    agent.extract(source) -> dict    -> {REQUEST_FIELD: value}
    agent.render_pdf(source, path)   -> path of the written summary PDF

- ParserAgent uses only local text rules (no network, no cost).
- LlmAgent sends the letter text to OpenAI for a structured answer.

`source` is anything PdfTableExtractor accepts: a URL or a local path. Downloads
are cached on disk by URL, so running both agents on the same URL fetches it once.
"""
from __future__ import annotations

from pdf_key_checker import PdfTableExtractor
from reports import Reports


class ExtractionAgent:
    """Common interface; subclasses set `name` and `method`."""

    name: str = ""
    method: str = ""

    def extract(self, source) -> dict[str, str]:
        """Return the REQUEST_FIELDS extracted from `source` as a {field: value} dict."""
        return PdfTableExtractor(source).extract_request_fields(method=self.method)

    def render_pdf(self, source, output_path, *, fields: dict | None = None) -> str:
        """Write a summary PDF for `source` using this agent's method.

        Pass `fields` (e.g. the dict already returned by extract) to avoid a second
        extraction — important for LlmAgent, where re-extracting costs another call.
        The written filename is tagged with the method (…_parse.pdf / …_llm.pdf).
        """
        extractor = PdfTableExtractor(source)
        if fields is None:
            fields = self.extract(source)
        return Reports().write_summary(
            output_path,
            fields=fields,
            table=extractor.extract_combined_table(),
            letterhead=extractor.extract_letterhead(),
            name_column=PdfTableExtractor.NAME_COLUMN,
            method=self.method,
            budget_history=extractor.extract_budget_history(),
        )


class ParserAgent(ExtractionAgent):
    """Rule-based extraction — fast, free, deterministic, no network."""

    name = "parse"
    method = "parse"


class LlmAgent(ExtractionAgent):
    """OpenAI-backed extraction — handles formats the rules miss, costs API calls."""

    name = "llm"
    method = "llm"

    def __init__(self, *, model: str | None = None, api_key: str | None = None):
        self.model = model
        self.api_key = api_key

    def extract(self, source) -> dict[str, str]:
        return PdfTableExtractor(source).extract_request_fields(
            method="llm", model=self.model, api_key=self.api_key
        )
