import re

HEADING = re.compile(r"^#{1,6}\s")


def chunk_markdown(markdown: str, *, max_chars: int = 1600) -> list[str]:
    """The section is the unit. A heading starts a new chunk; the paragraphs
    under it pack together until `max_chars`. Positions are zero-based.

    Packing on blank lines alone put the boundary in the wrong place: a heading
    would land at the end of one chunk with the rule it names in the next, so a
    query about detention matched the chunk carrying the word "Detention" while
    the free time and the rate sat in the chunk after it. A figure and the rule
    that uses it have to retrieve together. A citation that resolves to the
    heading and not the number is worse than no citation, because it reads like
    an answer.

    A section longer than `max_chars` still splits, and the continuation does
    not repeat the heading — chunks partition the document, they do not overlap.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", markdown.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        opens_section = HEADING.match(paragraph) is not None
        if current and not opens_section and len(current) + 2 + len(paragraph) <= max_chars:
            current += "\n\n" + paragraph
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_chars:
            boundary = paragraph.rfind(" ", 0, max_chars + 1)
            if boundary <= 0:
                boundary = max_chars
            chunks.append(paragraph[:boundary])
            paragraph = paragraph[boundary:].strip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
