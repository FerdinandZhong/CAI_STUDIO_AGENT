"""
Layer 1 — Regex / heuristic rules.

Pure stdlib, zero external dependencies. Runs in < 1 ms.
Patterns are compiled once at module load and reused for every call.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from models import LayerResult

# ---------------------------------------------------------------------------
# Pattern definitions  (pattern_string, threat_category)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: List[Tuple[str, str]] = [
    # Instruction override
    (r"\bignore\b.{0,40}\b(instructions?|prompt|rules?|constraints?|above|previous|system)\b", "prompt_injection"),
    (r"\bforget\b.{0,30}\b(everything|all|instructions?|previous|above)\b", "prompt_injection"),
    (r"\bdisregard\b.{0,40}\b(your|all|previous|instructions?|rules?)\b", "prompt_injection"),
    (r"\boverride\b.{0,30}\b(safety|guardrail|filter|restriction|instruction)\b", "prompt_injection"),
    (r"\bnew\s+(system\s+)?instructions?[\s:]+", "prompt_injection"),
    (r"<\s*/?system\s*>", "prompt_injection"),
    (r"\[\s*SYSTEM\s*\]", "prompt_injection"),
    # Role hijacking
    (r"\byou\s+are\s+now\b", "role_hijacking"),
    (r"\b(act|behave|pretend|roleplay|simulate)\b.{0,30}\bas\b", "role_hijacking"),
    (r"\byour\s+(new\s+)?(role|persona|identity|name)\s+is\b", "role_hijacking"),
    # Jailbreak keywords
    (r"\b(DAN|jailbreak|developer\s+mode|god\s+mode|unrestricted\s+mode|do\s+anything\s+now)\b", "jailbreak"),
    (r"\bremove\b.{0,20}\b(all\s+)?restrictions?\b", "jailbreak"),
    (r"\b(bypass|circumvent|evade)\b.{0,30}\b(safety|filter|guardrail|moderation|policy)\b", "jailbreak"),
    # Hypothetical / fictional framing used to extract unsafe content
    (r"\bhypothetically\b.{0,60}\b(if\s+you|you\s+could|could\s+you|what\s+if)\b", "jailbreak"),
    (r"\bfor\s+(educational|research|fictional|creative|academic|illustrative)\s+purposes\b.{0,60}\b(how\s+to|tell\s+me|explain|show\s+me)\b", "jailbreak"),
    (r"\bimagine\s+you\s+(are|have\s+no|don'?t\s+have)\b", "jailbreak"),
    # Indirect prompt injection (content passing through the model)
    (r"---\s*instruction\s*---", "prompt_injection"),
    (r"\|\s*system\s*\|", "prompt_injection"),
]

_SQL_DDL_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(drop|truncate)\b.{0,30}\b(table|database|schema|view|index|sequence)\b", "sql_ddl"),
    (r"\bdelete\b.{0,30}\bfrom\b", "sql_ddl"),
    (r"\b(alter|modify)\b.{0,30}\b(table|column|schema|database|view)\b", "sql_ddl"),
    (r"\bcreate\b.{0,30}\b(table|database|schema|index|view|procedure|trigger)\b", "sql_ddl"),
    (r"\binsert\b.{0,20}\binto\b", "sql_ddl"),
    (r"\bupdate\b.{0,30}\bset\b", "sql_ddl"),
    (r"\b(grant|revoke)\b.{0,30}\b(access|permission|privilege|on|to|from)\b", "sql_privilege_escalation"),
]

_SQL_PRIVILEGE_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(admin|root|superuser|dba|sa)\b.{0,30}\b(password|access|credentials?|login|account)\b", "sql_privilege_escalation"),
    (r"\binformation_schema\b", "sql_privilege_escalation"),
    (r"\bpg_(catalog|tables|class|attribute|namespace)\b", "sql_privilege_escalation"),
    (r"\bsys\.(tables|columns|databases|objects|procedures)\b", "sql_privilege_escalation"),
    (r"\bmysql\.(user|db|grants|tables_priv)\b", "sql_privilege_escalation"),
    (r"\b(show|list|display)\b.{0,30}\b(all\s+)?(users?|passwords?|credentials?|grants?|privileges?)\b", "sql_privilege_escalation"),
]

_SQL_SCHEMA_FISHING_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(list|show|display|enumerate|get)\b.{0,40}\b(all\s+)?(tables?|databases?|schemas?|views?|relations?)\b", "schema_fishing"),
    (r"\bdescribe\s+all\b", "schema_fishing"),
    (r"\bwhat\s+(tables?|databases?|schemas?|views?)\s+(exist|are\s+(there|available)|do\s+you\s+have)\b", "schema_fishing"),
    (r"\bshow\s+me\s+(the\s+)?(full\s+)?schema\b", "schema_fishing"),
]

_SQL_SCOPE_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(dump|export|extract)\b.{0,30}\b(all|entire|full|complete|whole)\b.{0,30}\b(table|database|data|records?)\b", "sql_scope_abuse"),
    (r"\bgive\s+me\s+(all|everything|every\s+(row|record))\b", "sql_scope_abuse"),
    (r"\bfull\s+(table|database)\s+(dump|export|download|backup)\b", "sql_scope_abuse"),
    (r"\ball\s+records?\s+(with(out)?\s+)?no\s+(filter|limit|condition|where)\b", "sql_scope_abuse"),
]

_CODE_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(os\.system|subprocess\.(run|call|Popen|check_output)|commands\.getoutput)\b", "code_execution"),
    (r"\b__import__\s*\(", "code_execution"),
    (r"\beval\s*\(.{0,200}\)", "code_execution"),
    (r"\bexec\s*\(.{0,200}\)", "code_execution"),
    (r"\bimportlib\.import_module\b", "code_execution"),
]

# Domain → extra patterns applied on top of the general injection patterns
_DOMAIN_EXTRA_PATTERNS: Dict[str, List[Tuple[str, str]]] = {
    "text_to_sql": _SQL_DDL_PATTERNS + _SQL_PRIVILEGE_PATTERNS,
    "code_generation": _CODE_PATTERNS,
    "general": [],
}

# Strictness-gated extras (only for text_to_sql)
_STRICTNESS_PATTERNS: Dict[str, List[Tuple[str, str]]] = {
    "moderate": _SQL_SCHEMA_FISHING_PATTERNS,
    "strict":   _SQL_SCHEMA_FISHING_PATTERNS + _SQL_SCOPE_PATTERNS,
}


# ---------------------------------------------------------------------------
# Pre-compile everything at module load
# ---------------------------------------------------------------------------

def _compile(pairs: List[Tuple[str, str]]) -> List[Tuple[re.Pattern, str]]:
    return [(re.compile(p, re.IGNORECASE | re.DOTALL), cat) for p, cat in pairs]


_C_INJECTION = _compile(_INJECTION_PATTERNS)
_C_DOMAIN: Dict[str, List[Tuple[re.Pattern, str]]] = {
    k: _compile(v) for k, v in _DOMAIN_EXTRA_PATTERNS.items()
}
_C_STRICTNESS: Dict[str, List[Tuple[re.Pattern, str]]] = {
    k: _compile(v) for k, v in _STRICTNESS_PATTERNS.items()
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def check(input_text: str, domain: str, strictness: str) -> LayerResult:
    """
    Run all applicable regex patterns against input_text.
    Returns on first match per category (accumulates all triggered categories).
    """
    text = input_text.strip()
    categories: List[str] = []
    reasons: List[str] = []

    def _scan(compiled: List[Tuple[re.Pattern, str]]) -> None:
        for pattern, category in compiled:
            if category not in categories and pattern.search(text):
                categories.append(category)
                reasons.append(f"regex:{category}")

    # Always-on: general injection / jailbreak
    _scan(_C_INJECTION)

    # Domain-specific patterns
    _scan(_C_DOMAIN.get(domain, _C_DOMAIN["general"]))

    # Strictness-gated SQL extras (moderate blocks schema fishing; strict also blocks scope abuse)
    if domain == "text_to_sql" and strictness in _C_STRICTNESS:
        _scan(_C_STRICTNESS[strictness])

    if categories:
        return LayerResult(
            blocked=True,
            threat_categories=categories,
            confidence=1.0,          # regex matches are deterministic
            reason="; ".join(reasons),
        )

    return LayerResult(blocked=False, threat_categories=[], confidence=1.0, reason=None)
