from brain.mdblocks import MAX_SPAN, blocks_to_md, md_to_blocks

SAMPLE = """# Title

Intro paragraph.

## Section

- first bullet
- second bullet

1. step one
2. step two

> a wise quote

```python
def hello():
    return 1
```

### Deep heading

Closing paragraph.
"""


def test_round_trip_is_stable_for_covered_subset():
    once = blocks_to_md(md_to_blocks(SAMPLE))
    twice = blocks_to_md(md_to_blocks(once))
    assert once == twice == SAMPLE


def test_unknown_blocks_degrade_to_plain_text():
    blocks = [{"object": "block", "type": "toggle", "toggle": {"rich_text": [{"type": "text", "text": {"content": "hidden"}}]}}]
    assert blocks_to_md(blocks) == "hidden\n"


def test_long_text_is_chunked_into_2000_char_spans():
    blocks = md_to_blocks("x" * 5000)
    spans = blocks[0]["paragraph"]["rich_text"]
    assert [len(s["text"]["content"]) for s in spans] == [MAX_SPAN, MAX_SPAN, 1000]
    assert blocks_to_md(blocks) == "x" * 5000 + "\n"
