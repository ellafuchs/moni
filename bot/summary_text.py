"""Shape the letter's request narrative into blocks for the summary page.

The model returns `request_summary` as verbatim text, sometimes as one paragraph.
The page wants: an opening paragraph, then one block per program with its heading
('NNNNNN: <name> – <amount>'), 'תיאור התוכנית' and 'מטרת השינוי'. Everything here is
deterministic string work — no model involved — so the layout never depends on
whether the model kept its line breaks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Program headings come in three shapes: '231039: שם – סכום', '19-42-02 - מנהל התרבות',
# and 'תוכנית 17-31-03: מטה אזרחי – 25,600 אלפי ש"ח'. All normalise to a 6-digit code.
_HEADING = re.compile(
    r"(?:תו?כנית\s+)?(?<![\d.,/])(?P<code>\d{6}|\d{2}-\d{2}-\d{2})(?!\d)\s*[:\-–]\s*(?=\S)")
_DESC = re.compile(r"תיאור הת[ו]?כנית\s*:")
_PURPOSE = re.compile(r"מטרת השינוי(?: התקציבי)?\s*:")
_LABEL_AT_START = re.compile(r"^\s*(תיאור הת[ו]?כנית|מטרת השינוי(?: התקציבי)?)\s*:")


def structure_summary(text: str) -> str:
    """Put every program heading, 'תיאור התוכנית' and 'מטרת השינוי' on its own line.

    Idempotent: text that already has the line breaks comes back unchanged apart from
    whitespace normalisation. Known PDF split-word artifact 'השי נוי' is repaired.
    """
    text = re.sub(r"הש[יי] נוי", "השינוי", text or "")
    # The staffing statement is shown as its own fact tile; keep it out of the narrative.
    text = re.sub(r"\s*השפעה על כו?ח אדם\s*:\s*[^\n.]*\.?\s*$", "", text)
    text = _HEADING.sub(lambda m: "\n" + m.group(0), text)
    text = re.sub(r"\n\s*\n", "\n", text)
    text = _DESC.sub(lambda m: "\n" + m.group(0), text)
    text = _PURPOSE.sub(lambda m: "\n" + m.group(0), text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


@dataclass
class Program:
    code: str
    heading: str            # the rest of the heading line, e.g. 'שירותים קהילתיים – 139,917 אלפי ש"ח בהוצאה'
    description: str = ""
    purpose: str = ""
    other: list[str] = field(default_factory=list)   # lines that belong to no label


def split_programs(text: str) -> tuple[str, list[Program]]:
    """(opening paragraph, [Program, ...]) from a structured or unstructured summary."""
    lines = structure_summary(text).split("\n")
    intro: list[str] = []
    programs: list[Program] = []
    current: Program | None = None
    target = "other"
    for line in lines:
        m = _HEADING.match(line)
        if m:
            current = Program(code=m.group("code").replace("-", ""),
                              heading=line[m.end():].strip(" .-–:"))
            programs.append(current)
            target = "other"
            continue
        if current is None:
            intro.append(line)
            continue
        lm = _LABEL_AT_START.match(line)
        if lm:
            target = "description" if lm.group(1).startswith("תיאור") else "purpose"
            line = line[lm.end():].strip()
            if not line:
                continue
        if target == "other":
            current.other.append(line)
        else:
            joined = getattr(current, target)
            setattr(current, target, f"{joined} {line}".strip() if joined else line)
    # No headings at all: keep the intro line by line so labels still start lines.
    return ("\n".join(intro) if not programs else " ".join(intro)).strip(), programs
