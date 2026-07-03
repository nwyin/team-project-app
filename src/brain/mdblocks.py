"""Markdown <-> Notion blocks. Covers paragraphs, headings 1-3, bulleted/numbered lists,
fenced code, and quotes; anything else round-trips as plain-text paragraphs."""

MAX_SPAN = 2000  # Notion rich_text span limit


def _rich_text(text: str) -> list[dict]:
    chunks = [text[i : i + MAX_SPAN] for i in range(0, len(text), MAX_SPAN)] or [""]
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _plain(rich_text: list[dict]) -> str:
    return "".join(span.get("plain_text") or span.get("text", {}).get("content", "") for span in rich_text)


def _block(block_type: str, text: str, **extra) -> dict:
    return {"object": "block", "type": block_type, block_type: {"rich_text": _rich_text(text), **extra}}


def md_to_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            language = stripped[3:].strip() or "plain text"
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence
            blocks.append(_block("code", "\n".join(code_lines), language=language))
            continue
        if stripped.startswith("### "):
            blocks.append(_block("heading_3", stripped[4:]))
        elif stripped.startswith("## "):
            blocks.append(_block("heading_2", stripped[3:]))
        elif stripped.startswith("# "):
            blocks.append(_block("heading_1", stripped[2:]))
        elif stripped.startswith("- "):
            blocks.append(_block("bulleted_list_item", stripped[2:]))
        elif len(stripped) > 2 and stripped[0].isdigit() and stripped.split(".", 1)[0].isdigit() and "." in stripped:
            _, rest = stripped.split(".", 1)
            if rest.startswith(" "):
                blocks.append(_block("numbered_list_item", rest[1:]))
            else:
                blocks.append(_block("paragraph", stripped))
        elif stripped.startswith("> "):
            blocks.append(_block("quote", stripped[2:]))
        else:
            blocks.append(_block("paragraph", stripped))
        i += 1
    return blocks


_LIST_TYPES = {"bulleted_list_item", "numbered_list_item"}


def blocks_to_md(blocks: list[dict]) -> str:
    lines: list[str] = []
    numbered = 0
    previous_type = None
    for block in blocks:
        block_type = block.get("type", "paragraph")
        payload = block.get(block_type, {})
        text = _plain(payload.get("rich_text", []))
        if block_type != "numbered_list_item":
            numbered = 0
        if lines and not (block_type in _LIST_TYPES and block_type == previous_type):
            lines.append("")  # blank line between blocks, but same-type list items stay adjacent
        if block_type == "heading_1":
            lines.append(f"# {text}")
        elif block_type == "heading_2":
            lines.append(f"## {text}")
        elif block_type == "heading_3":
            lines.append(f"### {text}")
        elif block_type == "bulleted_list_item":
            lines.append(f"- {text}")
        elif block_type == "numbered_list_item":
            numbered += 1
            lines.append(f"{numbered}. {text}")
        elif block_type == "quote":
            lines.append(f"> {text}")
        elif block_type == "code":
            language = payload.get("language", "")
            fence_lang = "" if language == "plain text" else language
            lines.append(f"```{fence_lang}\n{text}\n```")
        else:  # paragraph and anything unknown degrades to plain text
            lines.append(text)
        previous_type = block_type
    return "\n".join(lines) + ("\n" if lines else "")
