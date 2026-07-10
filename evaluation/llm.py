#llm.py

"""
Claude API wrapper. Replaces the Cowork agent's in-context reasoning with a stateless
Messages API call. Same judgment (the prompt is unchanged); this only handles transport:
pinned model, temperature=0, strict single-JSON-object output, and retry/backoff.
"""
from __future__ import annotations
import json
import os
import re
import time
from typing import Any

from anthropic import Anthropic, APIStatusError, APIConnectionError, RateLimitError

import config

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get(config.API_KEY_ENV)
        if not key:
            raise RuntimeError(
                f"Set {config.API_KEY_ENV} in your environment before running."
            )
        _client = Anthropic(api_key=key)
    return _client


def _extract_json(text: str) -> Any:
    """Return the first JSON object/array found in the model's text output."""
    text = text.strip()
    # Fast path: whole response is JSON.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Strip ```json fences if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Fallback: first balanced { ... } or [ ... ] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("No parseable JSON in model response:\n" + text[:2000])


def complete_json(system: str, user: str, *, max_tokens: int | None = None,
                  retries: int = 3) -> Any:
    """
    One Claude call that must return a single JSON value. Retries on transient API
    errors (backoff) and once more on an unparseable body (nudges strict JSON).
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            msg = client().messages.create(
                model=config.MODEL,
                max_tokens=max_tokens or config.MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(
                block.text for block in msg.content if getattr(block, "type", "") == "text"
            )
            return _extract_json(text)
        except (RateLimitError, APIConnectionError, APIStatusError) as e:
            last_err = e
            time.sleep(2 ** attempt)
        except ValueError as e:
            # Parse failure: retry once asking for JSON only.
            last_err = e
            user = user + "\n\nReturn ONLY the JSON value, no prose, no code fences."
    raise RuntimeError(f"Claude call failed after {retries} attempts: {last_err}")

