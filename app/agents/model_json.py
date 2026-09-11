"""The one JSON object in a model's reply. SPEC 3.7.

Models say more than they are asked to — a fence around the object, a note
after it, a sentence before it. Three times that extra text broke a parser here,
and each time the error surfaced as something else: a driver told his words
were unreadable, a wrong answer scored as a provider outage, a verdict the
check had counted as the check not running. So every parser reads through
this, and only the object is the answer.

What is still refused: no object at all — a model answering in prose is a
failure, and its error says so — and more than one, because picking one would
be a guess.
"""

import json
import re

FENCED = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.S)
_decoder = json.JSONDecoder()


class NoSingleObject(ValueError):
    """The reply did not carry exactly one JSON object."""


def extract(content: str):
    blocks = FENCED.findall(content)
    if len(blocks) > 1:
        raise NoSingleObject(f"{len(blocks)} JSON blocks")
    text = blocks[0] if blocks else content
    found, index = [], 0
    while (start := text.find("{", index)) != -1:
        try:
            value, end = _decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        found.append(value)
        index = end
    if not found:
        raise NoSingleObject("no JSON object")
    if len(found) > 1:
        raise NoSingleObject(f"{len(found)} JSON objects")
    return found[0]
