import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path for validation + config imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATA_DIR, MAX_TOKENS_CONVERT
from pipeline._client import call_with_retry

_log = logging.getLogger("mindci")


def _repair_json(bad_json: str, is_truncation: bool = False) -> str:
    """Ask Claude to fix its own malformed JSON output.

    When ``is_truncation`` is True and the payload is large, sends only the
    head + tail instead of the full response — the middle is already valid and
    Claude can't do anything useful with it.  Asks for a single recovered
    object; the caller is responsible for wrapping it in a list if needed.
    """
    if is_truncation and len(bad_json) > 1200:
        snippet = bad_json[:300] + "\n...[middle omitted]...\n" + bad_json[-800:]
        prompt = (
            "A JSON array was cut off before it could finish. "
            "Here is the start and the truncated end:\n\n"
            f"{snippet}\n\n"
            "Return ONLY the last incomplete entry as a single valid JSON object "
            "(not an array), completing any truncated strings or fields. "
            "If the entry cannot be recovered, return an empty object: {}"
        )
    else:
        prompt = (
            "The following JSON is malformed. "
            "Fix it and return ONLY valid JSON, nothing else.\n\n"
            f"MALFORMED JSON:\n{bad_json}"
        )
    return call_with_retry(prompt, max_tokens=MAX_TOKENS_CONVERT)


def _salvage_partial_json(text: str) -> list:
    """Extract complete JSON objects from a truncated array response.

    Walks the string tracking brace depth and string/escape state so it
    can identify fully-formed ``{...}`` objects even when the surrounding
    array is cut off mid-entry (the most common truncation pattern).
    Returns a (possibly empty) list of successfully parsed dicts.
    """
    s = text.strip().lstrip("[").lstrip()
    objects: list = []
    depth = 0
    start: int | None = None
    in_string = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_string:
            if ch == "\\":
                i += 2  # skip the escaped character entirely (e.g. \", \\)
                continue
            if ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objects.append(json.loads(s[start : i + 1]))
                    except json.JSONDecodeError:
                        pass
                    start = None
        i += 1
    return objects


def detect_note_sections(
    text: str, min_chars: int = 150
) -> tuple[list[dict], str]:
    """Heuristically split a long note into logical sections for user review.

    Strategies tried in priority order:
    1. Explicit CPM ``——SECTION——`` markers
    2. Markdown headings (``#`` through ``####``)
    3. Triple-or-more blank lines (strong section-break signal)
    4. Double blank lines, grouped so no chunk is tiny
    5. Fallback: bisect at the paragraph boundary nearest the midpoint

    Returns ``(sections, strategy_label)`` where sections is a list of
    ``{title, content, word_count}`` dicts and strategy_label names which
    heuristic fired so callers can surface it to the user.
    """
    # Strategy 1: explicit CPM section markers
    if re.search(r'——SECTION——', text):
        parts = re.split(r'——SECTION——', text)
        chunks = [p.strip() for p in parts if len(p.strip()) >= min_chars]
        if len(chunks) >= 2:
            return _chunks_to_section_dicts(chunks), "CPM section markers"

    # Strategy 2: markdown headings
    heading_re = re.compile(r'^#{1,4}\s+\S', re.MULTILINE)
    positions = [m.start() for m in heading_re.finditer(text)]
    if len(positions) >= 2:
        chunks = []
        for i, pos in enumerate(positions):
            end = positions[i + 1] if i + 1 < len(positions) else len(text)
            chunk = text[pos:end].strip()
            if len(chunk) >= min_chars:
                chunks.append(chunk)
        if len(chunks) >= 2:
            return _chunks_to_section_dicts(chunks), "markdown headings"

    # Strategy 3: triple+ blank lines
    parts = re.split(r'\n{3,}', text)
    chunks = [p.strip() for p in parts if len(p.strip()) >= min_chars]
    if len(chunks) >= 2:
        return _chunks_to_section_dicts(chunks), "triple blank lines"

    # Strategy 4: double blank lines, grouped to avoid tiny chunks
    parts = re.split(r'\n{2,}', text)
    chunks = [p.strip() for p in parts if len(p.strip()) >= min_chars]
    if len(chunks) >= 2:
        return _group_small_sections(chunks, target_chars=1500), "double blank lines"

    # Fallback: bisect at the nearest paragraph boundary to the midpoint
    mid = len(text) // 2
    split_pos = text.find('\n\n', mid)
    if split_pos == -1:
        split_pos = text.find('\n', mid)
    if split_pos == -1:
        split_pos = mid
    halves = [text[:split_pos].strip(), text[split_pos:].strip()]
    return (
        _chunks_to_section_dicts([h for h in halves if h]),
        "midpoint split (no strong boundaries found)",
    )


def _chunks_to_section_dicts(chunks: list[str]) -> list[dict]:
    result = []
    for i, content in enumerate(chunks):
        lines = [l for l in content.splitlines() if l.strip()]
        raw_title = lines[0][:70] if lines else f"Section {i + 1}"
        title = re.sub(r'^#+\s*', '', raw_title).strip() or f"Section {i + 1}"
        result.append({
            "title": title,
            "content": content,
            "word_count": len(content.split()),
        })
    return result


def _group_small_sections(chunks: list[str], target_chars: int = 1500) -> list[dict]:
    """Merge consecutive small chunks until the group reaches target_chars."""
    groups: list[str] = []
    current: list[str] = []
    current_len = 0
    for chunk in chunks:
        current.append(chunk)
        current_len += len(chunk)
        if current_len >= target_chars:
            groups.append("\n\n".join(current))
            current, current_len = [], 0
    if current:
        groups.append("\n\n".join(current))
    return _chunks_to_section_dicts(groups)


def _strip_fences(text):
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    return clean.strip()


def fetch_url_as_markdown(url: str, reader_base: str | None = None, timeout: int = 30) -> str:
    """Fetch a URL via the configured reader service and return clean markdown.

    Defaults to Jina Reader (https://r.jina.ai/) which strips boilerplate and
    returns LLM-ready markdown for any public URL. Override the endpoint with
    `MINDCI_READER_URL` for a self-hosted Trafilatura/Readability fallback.
    """
    import urllib.request

    base = reader_base or os.environ.get("MINDCI_READER_URL", "https://r.jina.ai/")
    if not base.endswith("/"):
        base += "/"
    req = urllib.request.Request(base + url,
                                 headers={"User-Agent": "MindCI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
    if not body:
        raise ValueError(f"Reader returned empty body for {url}")
    return body


def parse_markdown_with_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML-style frontmatter from a markdown file.
    Returns (metadata_dict, body_text). If no frontmatter, returns ({}, content).

    Supports a tiny subset (no new dependency): top-of-file `---` block with
    `key: value` pairs, one per line. No nested structures, no list values,
    no multi-line strings. Quotes around values are stripped.
    """
    if not content.startswith("---"):
        return {}, content
    rest = content[3:].lstrip("\n")
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return {}, content
    fm_block = rest[:end_idx]
    body = rest[end_idx + 4:].lstrip("\n")  # skip past "\n---"
    meta: dict = {}
    for line in fm_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def convert_to_json(raw_text):
    prompt = f"""
Convert the following raw technical notes into structured JSON.
Preserve the source filename in each JSON entry as "source".
Return ONLY raw JSON with no markdown, no code fences, no explanation.

Rules:
- Detect type: project, certification, exploration
- Return a JSON array
- Use fields:

project:
  error, root_cause, fix, concept, confidence, difficulty

certification:
  topic, key_points, confusion, importance, confidence, difficulty

exploration:
  tool, description, comparison (string), use_cases (string — not a list), confidence, difficulty

RAW NOTES:
{raw_text}
"""
    return call_with_retry(prompt, max_tokens=MAX_TOKENS_CONVERT)


def parse_and_save_json(raw_response):
    clean = _strip_fences(raw_response)
    salvage_warning: str | None = None

    # Attempt 1: parse directly
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as first_err:
        # Attempt 2: salvage complete objects from a truncated array.
        # No extra API call — handles the common case where the model's
        # response was cut off before the closing `]`.
        salvaged = _salvage_partial_json(clean)
        if salvaged:
            parsed = salvaged
            salvage_warning = (
                f"Response was truncated; salvaged {len(salvaged)} complete "
                f"entr{'y' if len(salvaged) == 1 else 'ies'}. "
                f"Some content from this note may be missing — consider "
                f"splitting very long notes into smaller files. "
                f"(Original parse error: {first_err})"
            )
            _log.warning("convert: %s", salvage_warning)
        else:
            # Attempt 3: ask Claude to repair (last resort).
            # Pass is_truncation so large payloads are trimmed to head+tail.
            is_trunc = "Unterminated string" in str(first_err)
            try:
                repaired_raw = _strip_fences(_repair_json(clean, is_truncation=is_trunc))
                repaired_obj = json.loads(repaired_raw)
                # Repair may return a single object for the truncation path
                parsed = [repaired_obj] if isinstance(repaired_obj, dict) else repaired_obj
            except (json.JSONDecodeError, RuntimeError) as e:
                raise ValueError(
                    f"Could not parse Claude response as JSON after repair attempt.\n"
                    f"Error: {e}\n"
                    f"First 300 chars of response: {clean[:300]}"
                )

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array, got {type(parsed).__name__}")

    history_dir = os.path.join(DATA_DIR, "history")
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(history_dir, exist_ok=True)

    current_path = os.path.join(DATA_DIR, "structured.json")

    # Load existing KB and version it before any write
    existing_entries: list = []
    if os.path.exists(current_path):
        try:
            data = json.loads(Path(current_path).read_text(encoding="utf-8"))
            existing_entries = data if isinstance(data, list) else []
        except Exception:
            existing_entries = []
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        versioned = os.path.join(history_dir, f"structured_{timestamp}.json")
        with open(versioned, "w", encoding="utf-8") as fv:
            json.dump(existing_entries, fv, indent=2, ensure_ascii=False)

    # Validate new entries only so the report counts reflect this batch
    try:
        from validation import validate_entries
        valid_new, invalid_new, warnings_new = validate_entries(parsed)
    except ImportError:
        valid_new, invalid_new, warnings_new = list(parsed), [], []

    # Deduplicate: drop retained entries whose source matches a new one.
    # Re-converting a note replaces its previous entries; split sections
    # accumulate correctly because each gets a unique _partN source name.
    new_sources = {e.get("source", "") for e in valid_new if e.get("source")}
    retained = [e for e in existing_entries if e.get("source", "") not in new_sources]
    merged = retained + valid_new

    with open(current_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    if invalid_new:
        invalid_path = os.path.join(DATA_DIR, "invalid_entries.json")
        with open(invalid_path, "w", encoding="utf-8") as f:
            json.dump(invalid_new, f, indent=2, ensure_ascii=False)

    report = {
        "valid_count": len(valid_new),
        "invalid_count": len(invalid_new),
        "warning_count": len(warnings_new),
        "invalid": invalid_new,
        "warnings": warnings_new,
    }
    if salvage_warning:
        report["salvage_warning"] = salvage_warning
    return parsed, report


def list_kb_versions():
    """Return list of versioned KB files sorted newest first."""
    history_dir = os.path.join(DATA_DIR, "history")
    if not os.path.exists(history_dir):
        return []
    files = [f for f in os.listdir(history_dir) if f.startswith("structured_")]
    return sorted(files, reverse=True)
