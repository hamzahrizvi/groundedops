import re

LIST_LINE_RE = re.compile(r"^\s*(?:[-*•☐]|\d+[.)])\s+")


def _split_into_units(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n", text)
    units: list[str] = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        list_like_count = sum(1 for line in lines if LIST_LINE_RE.match(line))
        short_line_count = sum(1 for line in lines if len(line.split()) <= 8)

        if list_like_count >= 2 or (len(lines) >= 3 and short_line_count >= 2):
            units.append("\n".join(lines))
        else:
            prose = " ".join(lines)
            sentences = re.split(r"(?<=[.!?])\s+", prose)
            units.extend([s.strip() for s in sentences if s.strip()])

    return units


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    if not text or not text.strip():
        return []

    units = _split_into_units(text)
    if not units:
        return []

    chunks: list[str] = []
    current = ""

    for unit in units:
        separator = "\n" if ("\n" in unit or "\n" in current) else " "
        proposed = (current + separator + unit).strip() if current else unit

        if len(proposed) > size and current:
            chunks.append(current.strip())

            if overlap and chunks:
                prev = chunks[-1]
                overlap_text = prev[-overlap:].strip()
                if "\n" in unit:
                    current = (overlap_text + "\n" + unit).strip()
                else:
                    current = (overlap_text + " " + unit).strip()
            else:
                current = unit
        else:
            current = proposed

    if current:
        chunks.append(current.strip())

    return chunks