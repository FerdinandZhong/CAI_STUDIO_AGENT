"""
Layer 3 — Domain policy checks.

Pure Python, no model inference, no network calls. Runs in < 5 ms.
Each domain has its own private check function; the public check() dispatches.
"""

from __future__ import annotations

import re
from typing import List, Optional

from models import LayerResult

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# DDL/write-intent words checked as whole words against the token set
_DDL_WORDS = frozenset({
    "drop", "delete", "truncate", "alter", "insert", "update", "replace",
    "create", "rename", "merge", "upsert", "overwrite", "modify", "remove",
    "clear", "wipe", "erase", "purge",
})

# Sensitive column name patterns (PII, credentials, financial)
_SENSITIVE_COL_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\b(password|passwd|pwd|secret|credential|token|api_key|private_key|secret_key)\b",
        r"\b(ssn|social_security|nric|passport_no|national_id|sin)\b",
        r"\b(credit_card|card_number|card_no|cvv|cvc|pan)\b",
        r"\b(salary|compensation|payroll|base_pay|bonus)\b",
        r"\b(encryption_key|private_key|signing_key)\b",
    ]
]

# Heuristic: extract table-like tokens following SQL keywords in NL text
_TABLE_REF_RE = re.compile(
    r"\b(?:from|join|update|into|table|on)\s+([a-zA-Z_][a-zA-Z0-9_.]{1,80})",
    re.IGNORECASE,
)


def _word_set(text: str) -> frozenset:
    return frozenset(re.findall(r"\b[a-z_]+\b", text.lower()))


def _extract_table_refs(text: str) -> List[str]:
    return [m.group(1).lower() for m in _TABLE_REF_RE.finditer(text)]


# ---------------------------------------------------------------------------
# text_to_sql domain
# ---------------------------------------------------------------------------

def _check_text_to_sql(
    text: str,
    allowed_tables: Optional[List[str]],
    strictness: str,
) -> LayerResult:
    categories: List[str] = []
    reasons: List[str] = []

    words = _word_set(text)

    # DDL intent via word presence
    ddl_hits = words & _DDL_WORDS
    if ddl_hits:
        categories.append("sql_ddl_intent")
        reasons.append(f"write-intent words: {', '.join(sorted(ddl_hits))}")

    # Table allowlist enforcement
    if allowed_tables:
        allowed_set = {t.lower().strip() for t in allowed_tables}
        mentioned = _extract_table_refs(text)
        violations = [t for t in mentioned if t not in allowed_set]
        if violations:
            categories.append("table_access_violation")
            reasons.append(f"tables not in allowlist: {', '.join(violations)}")

    # Sensitive column access (moderate + strict only)
    if strictness in ("moderate", "strict"):
        for pat in _SENSITIVE_COL_PATTERNS:
            if pat.search(text):
                categories.append("sensitive_column_access")
                reasons.append("query references sensitive column (credentials/PII/financial)")
                break

    if categories:
        return LayerResult(
            blocked=True,
            threat_categories=categories,
            confidence=0.95,
            reason="; ".join(reasons),
        )
    return LayerResult(blocked=False, threat_categories=[], confidence=0.95, reason=None)


# ---------------------------------------------------------------------------
# code_generation domain
# ---------------------------------------------------------------------------

_CODE_DANGER_PATTERNS = [
    (re.compile(r"\b(os\.environ|os\.getenv|dotenv|load_dotenv)\b", re.IGNORECASE), "env_var_exfiltration"),
    (re.compile(r"\b(requests|urllib|httpx|aiohttp)\b.{0,40}\b(get|post|put|delete|patch|fetch)\b", re.IGNORECASE), "network_exfiltration"),
    (re.compile(r"\bopen\s*\(.{0,60}['\"]r['\"]", re.IGNORECASE), "arbitrary_file_read"),
    (re.compile(r"\bshutil\.(copy|move|rmtree)\b", re.IGNORECASE), "filesystem_manipulation"),
    (re.compile(r"\bpickle\.(load|loads)\b", re.IGNORECASE), "unsafe_deserialization"),
]


def _check_code_generation(text: str, strictness: str) -> LayerResult:
    for pattern, category in _CODE_DANGER_PATTERNS:
        if pattern.search(text):
            return LayerResult(
                blocked=True,
                threat_categories=[category],
                confidence=0.90,
                reason=f"code request targets dangerous operation: {category}",
            )
    return LayerResult(blocked=False, threat_categories=[], confidence=0.90, reason=None)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check(
    input_text: str,
    domain: str,
    allowed_tables: Optional[List[str]],
    strictness: str,
) -> LayerResult:
    if domain == "text_to_sql":
        return _check_text_to_sql(input_text, allowed_tables, strictness)
    if domain == "code_generation":
        return _check_code_generation(input_text, strictness)
    # general domain: no additional policy
    return LayerResult(blocked=False, threat_categories=[], confidence=1.0, reason=None)
