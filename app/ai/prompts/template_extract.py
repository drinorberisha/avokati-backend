"""Prompt for turning an uploaded contract into a parameterized template.

Given the raw text of a contract/notary document (usually Albanian), DeepSeek
returns a JSON template definition: clean semantic HTML with concrete values
replaced by {{camelCaseVariable}} tokens, plus the matching variable list.
"""

from app.ai.llm import ChatMessage

_SYSTEM = """You convert legal documents (contracts, notary deeds, court filings)
into reusable, parameterized templates for a Kosovo law office.

You receive the raw text of one document. Produce a TEMPLATE of it: keep the
fixed legal wording, but replace every value that would change from one use to
the next — party names, addresses, personal/ID numbers, dates, monetary amounts,
quantities, case numbers, property descriptions — with a placeholder token of
the form {{variableName}}.

Rules:
- Detect the document's language and keep the template in that SAME language
  (most will be Albanian — preserve ë, ç and all diacritics exactly).
- Variable names: camelCase, descriptive, in the document's language where
  natural (e.g. emriShitesit, cmimiShitjes, dataNenshkrimit). Reuse the SAME
  token when the same value appears more than once.
- Output clean semantic HTML for the body: <h2> for the title, <h3> for section
  headings, <p> for paragraphs, <ol>/<li> for numbered clauses, <table> for
  tabular data. Do NOT include <html>/<body> wrappers or inline styles.
- Every {{token}} in the body MUST have a matching entry in "variables", and
  every variable MUST appear at least once in the body.
- Pick a "category" from exactly: Contracts, Legal, HR, Finance, Court Filing.
- Variable "type" is one of: text, number, date, select, boolean. Use "date"
  for dates, "number" for amounts/quantities, otherwise "text".

Return ONLY a JSON object (no markdown fences, no commentary) with this shape:
{
  "title": "string",
  "description": "one short sentence",
  "category": "Contracts|Legal|HR|Finance|Court Filing",
  "language": "Albanian|English|Serbian",
  "content_html": "string of HTML with {{tokens}}",
  "variables": [
    {"name": "string", "type": "text|number|date|select|boolean",
     "required": true, "description": "what to fill in"}
  ]
}"""


def build_messages(document_text: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(
            role="user",
            content="Convert this document into a template JSON:\n\n" + document_text,
        ),
    ]
