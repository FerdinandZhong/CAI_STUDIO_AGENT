#!/usr/bin/env python3
"""
NLToSQLEvaluator — schema-aware feasibility gate for NL-to-SQL pipelines.

Workflow:
  1. Keyword-based schema lookup for tables relevant to the question
  2. Single LLM call to assess whether the question is translatable to SQL
  3. Returns verdict + schema_context for downstream SQL agents

Verdicts:
  FEASIBLE     — clear, specific banking data question answerable with SQL
  NOT_FEASIBLE — off-topic or data not available in banking_chatbot_db
  CLARIFY      — banking-related but too vague to generate SQL

Fail-open: any LLM error returns FEASIBLE so the SQL pipeline can attempt it.
"""

import json

from openai import OpenAI


# ---------------------------------------------------------------------------
# Mock Atlas schemas — swap for real atlas-mcp client call if available
# ---------------------------------------------------------------------------

_MOCK_ATLAS_SCHEMAS = {
    'banking_chatbot_db.customers': {
        'database': 'banking_chatbot_db', 'table': 'customers',
        'estimated_rows': 20,
        'columns': [
            ('customer_id',        'STRING',        'unique customer ID (CUST-BNNNN)'),
            ('full_name',          'STRING',        'customer full legal name'),
            ('email',              'STRING',        'primary email address'),
            ('phone',              'STRING',        'primary phone number'),
            ('date_of_birth',      'DATE',          'date of birth'),
            ('address',            'STRING',        'registered home address'),
            ('kyc_status',         'STRING',        'VERIFIED | PENDING | FAILED'),
            ('registration_date',  'DATE',          'date account relationship opened'),
            ('preferred_language', 'STRING',        'ISO 639-1 language code'),
            ('customer_segment',   'STRING',        'RETAIL | PREMIUM | BUSINESS'),
            ('notes',              'STRING',        'free-text notes'),
        ],
    },
    'banking_chatbot_db.accounts': {
        'database': 'banking_chatbot_db', 'table': 'accounts',
        'estimated_rows': 30,
        'columns': [
            ('account_id',            'STRING',        'unique account ID (ACC-NNNNNN)'),
            ('customer_id',           'STRING',        'FK -> customers.customer_id'),
            ('account_type',          'STRING',        'CHECKING | SAVINGS | MONEY_MARKET | CD'),
            ('account_number_masked', 'STRING',        'last-4 masked (****NNNN)'),
            ('currency',              'STRING',        'ISO 4217 currency code'),
            ('current_balance',       'DECIMAL(18,2)', 'current ledger balance'),
            ('available_balance',     'DECIMAL(18,2)', 'available balance (0 if locked)'),
            ('status',                'STRING',        'ACTIVE | LOCKED | FROZEN | UNDER_REVIEW | DORMANT | CLOSED'),
            ('lock_reason',           'STRING',        'FRAUD_ALERT | SUSPICIOUS_ACTIVITY | LOAN_DEFAULT | CUSTOMER_REQUEST | NULL'),
            ('opened_date',           'DATE',          'date account was opened'),
            ('last_activity_date',    'DATE',          'date of last transaction'),
            ('interest_rate_pct',     'DECIMAL(6,3)',  'annual interest rate %'),
            ('overdraft_limit',       'DECIMAL(18,2)', 'overdraft limit'),
            ('daily_transfer_limit',  'DECIMAL(18,2)', 'daily transfer cap'),
            ('notes',                 'STRING',        'free-text notes'),
        ],
    },
    'banking_chatbot_db.transactions': {
        'database': 'banking_chatbot_db', 'table': 'transactions',
        'estimated_rows': 200,
        'columns': [
            ('transaction_id',    'STRING',        'unique txn ID (TXN-YYYY-NNNNNN)'),
            ('account_id',        'STRING',        'FK -> accounts.account_id'),
            ('customer_id',       'STRING',        'FK -> customers.customer_id'),
            ('transaction_type',  'STRING',        'DEBIT | CREDIT | TRANSFER | PAYMENT | WITHDRAWAL | DEPOSIT | WIRE | FEE'),
            ('amount',            'DECIMAL(18,2)', 'transaction amount (always positive)'),
            ('currency',          'STRING',        'ISO 4217 currency code'),
            ('balance_after',     'DECIMAL(18,2)', 'account balance after transaction'),
            ('merchant_name',     'STRING',        'merchant or payee name'),
            ('merchant_category', 'STRING',        'merchant category code'),
            ('description',       'STRING',        'transaction description'),
            ('status',            'STRING',        'COMPLETED | PENDING | FAILED | REVERSED'),
            ('initiated_at',      'TIMESTAMP',     'transaction initiation timestamp'),
            ('completed_at',      'TIMESTAMP',     'transaction completion timestamp'),
            ('channel',           'STRING',        'MOBILE | WEB | ATM | BRANCH | API'),
            ('reference_number',  'STRING',        'external reference number'),
            ('failure_reason',    'STRING',        'reason for failure if status=FAILED'),
            ('notes',             'STRING',        'free-text notes'),
        ],
    },
    'banking_chatbot_db.loans': {
        'database': 'banking_chatbot_db', 'table': 'loans',
        'estimated_rows': 25,
        'columns': [
            ('loan_id',             'STRING',        'unique loan ID (LOAN-YYYY-NNNN)'),
            ('customer_id',         'STRING',        'FK -> customers.customer_id'),
            ('loan_type',           'STRING',        'PERSONAL | MORTGAGE | AUTO | STUDENT | BUSINESS'),
            ('original_amount',     'DECIMAL(18,2)', 'original principal disbursed'),
            ('outstanding_balance', 'DECIMAL(18,2)', 'current outstanding balance'),
            ('interest_rate_pct',   'DECIMAL(6,3)',  'annual interest rate %'),
            ('monthly_payment',     'DECIMAL(18,2)', 'required monthly payment'),
            ('next_payment_date',   'DATE',          'due date of next payment'),
            ('payments_overdue',    'INT',           'number of missed payments'),
            ('status',              'STRING',        'ACTIVE | PAID_OFF | DELINQUENT | DEFAULT | RESTRUCTURED'),
            ('origination_date',    'DATE',          'loan origination date'),
            ('maturity_date',       'DATE',          'loan maturity date'),
            ('notes',               'STRING',        'free-text notes'),
        ],
    },
    'banking_chatbot_db.cards': {
        'database': 'banking_chatbot_db', 'table': 'cards',
        'estimated_rows': 35,
        'columns': [
            ('card_id',            'STRING',        'unique card ID (CARD-NNNNNN)'),
            ('account_id',         'STRING',        'FK -> accounts.account_id'),
            ('customer_id',        'STRING',        'FK -> customers.customer_id'),
            ('card_type',          'STRING',        'DEBIT | CREDIT'),
            ('card_number_masked', 'STRING',        'last-4 masked (****NNNN)'),
            ('status',             'STRING',        'ACTIVE | BLOCKED | EXPIRED | CANCELLED'),
            ('block_reason',       'STRING',        'FRAUD | LOST | STOLEN | CUSTOMER_REQUEST | NULL'),
            ('expiry_date',        'DATE',          'card expiry date'),
            ('credit_limit',       'DECIMAL(18,2)', 'credit limit (NULL for debit)'),
            ('current_balance',    'DECIMAL(18,2)', 'current outstanding balance'),
            ('available_credit',   'DECIMAL(18,2)', 'available credit remaining'),
            ('issued_date',        'DATE',          'card issue date'),
            ('last_used_date',     'DATE',          'date of last card transaction'),
            ('notes',              'STRING',        'free-text notes'),
        ],
    },
    'banking_chatbot_db.support_cases': {
        'database': 'banking_chatbot_db', 'table': 'support_cases',
        'estimated_rows': 40,
        'columns': [
            ('case_id',        'STRING',    'unique case ID (CASE-YYYY-NNNN)'),
            ('customer_id',    'STRING',    'FK -> customers.customer_id'),
            ('case_type',      'STRING',    'FRAUD_REPORT | ACCOUNT_LOCK | TRANSACTION_DISPUTE | LOAN_INQUIRY | COMPLIANCE | GENERAL'),
            ('subject',        'STRING',    'one-line case summary'),
            ('status',         'STRING',    'OPEN | IN_PROGRESS | RESOLVED | CLOSED'),
            ('priority',       'STRING',    'CRITICAL | HIGH | MEDIUM | LOW'),
            ('assigned_agent', 'STRING',    'agent handling the case'),
            ('created_at',     'TIMESTAMP', 'case creation timestamp'),
            ('updated_at',     'TIMESTAMP', 'last update timestamp'),
            ('resolved_at',    'TIMESTAMP', 'resolution timestamp (NULL if open)'),
            ('account_id',     'STRING',    'linked account if applicable'),
            ('transaction_id', 'STRING',    'linked transaction if applicable'),
            ('notes',          'STRING',    'free-text case notes'),
        ],
    },
}

_TABLE_KEYWORDS = {
    'banking_chatbot_db.customers':     {'customer', 'customers', 'name', 'email', 'phone', 'kyc', 'identity', 'age', 'segment'},
    'banking_chatbot_db.accounts':      {'account', 'accounts', 'balance', 'savings', 'checking', 'overdraft', 'deposit', 'interest'},
    'banking_chatbot_db.transactions':  {'transaction', 'transactions', 'transfer', 'payment', 'withdrawal', 'debit', 'credit', 'merchant', 'spend', 'wire', 'fee'},
    'banking_chatbot_db.loans':         {'loan', 'loans', 'mortgage', 'repayment', 'instalment', 'installment', 'debt', 'overdue', 'delinquent', 'principal'},
    'banking_chatbot_db.cards':         {'card', 'cards', 'blocked', 'expired', 'credit limit', 'debit card', 'credit card'},
    'banking_chatbot_db.support_cases': {'case', 'cases', 'complaint', 'dispute', 'fraud', 'support', 'ticket', 'report', 'inquiry'},
}


def _fetch_schema(question: str) -> str:
    """Keyword-based schema routing — swap for real Atlas MCP client if available."""
    q = question.lower()
    schemas = [
        _MOCK_ATLAS_SCHEMAS[tbl]
        for tbl, kws in _TABLE_KEYWORDS.items()
        if any(k in q for k in kws)
    ]
    if not schemas:
        schemas = list(_MOCK_ATLAS_SCHEMAS.values())

    lines = ['SCHEMA CONTEXT\n']
    for s in schemas:
        lines.append(f"Database : {s['database']}")
        lines.append(f"Table    : {s['table']}")
        lines.append(f"Rows est.: {s['estimated_rows']}")
        lines.append('Columns  :')
        for col_name, col_type, col_desc in s['columns']:
            lines.append(f"  {col_name:<20} {col_type:<15} -- {col_desc}")
        lines.append('')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Feasibility evaluation prompt
# ---------------------------------------------------------------------------

_EVAL_PROMPT = """\
You are a SQL feasibility evaluator for banking_chatbot_db.
Available tables: customers, accounts, transactions, loans, cards, support_cases

Schema context:
{schema_context}

User question: "{question}"

Classify the question as exactly one of:
  FEASIBLE     — clear, specific banking data question that can be answered with SQL
  NOT_FEASIBLE — off-topic, or refers to data not available in banking_chatbot_db
  CLARIFY      — banking-related but too vague or ambiguous to generate SQL

Return JSON only, no explanation outside the JSON:
{{"verdict": "FEASIBLE|NOT_FEASIBLE|CLARIFY", "message": "one sentence", "suggested_tables": ["table1", "table2"]}}
"""


# ---------------------------------------------------------------------------
# NLToSQLEvaluator
# ---------------------------------------------------------------------------

class NLToSQLEvaluator:
    """
    Schema-aware feasibility gate.

    Fetches relevant schema via keyword routing, then makes a single LLM call
    to determine whether the question can be translated to SQL.

    Returns a dict with:
      verdict         — FEASIBLE | NOT_FEASIBLE | CLARIFY
      message         — one-sentence explanation
      suggested_tables — list of relevant table names
      schema_context  — full schema text for the SQL Generator agent
    """

    def __init__(self, cai_url: str, cai_model: str, cai_api_key: str):
        self.cai_model = cai_model
        self._client = OpenAI(base_url=cai_url, api_key=cai_api_key)

    def evaluate(self, question: str) -> dict:
        """Evaluate feasibility. Fail-open to FEASIBLE on any error."""
        schema_context = _fetch_schema(question)
        prompt = _EVAL_PROMPT.format(
            schema_context=schema_context,
            question=question,
        )

        try:
            response = self._client.chat.completions.create(
                model=self.cai_model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.0,
                max_tokens=256,
            )
            raw = response.choices[0].message.content.strip()

            # Strip markdown code fences if the model wrapped its JSON
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
                raw = raw.strip()

            parsed = json.loads(raw)
            verdict = parsed.get('verdict', 'FEASIBLE').upper()
            if verdict not in ('FEASIBLE', 'NOT_FEASIBLE', 'CLARIFY'):
                verdict = 'FEASIBLE'

            return {
                'verdict': verdict,
                'message': parsed.get('message', ''),
                'suggested_tables': parsed.get('suggested_tables', []),
                'schema_context': schema_context,
            }

        except Exception:
            # Fail-open: let the SQL pipeline attempt the question
            return {
                'verdict': 'FEASIBLE',
                'message': 'Feasibility check unavailable — proceeding with SQL generation.',
                'suggested_tables': [],
                'schema_context': schema_context,
            }
