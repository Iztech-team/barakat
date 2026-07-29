"""The chart of accounts a Barakat shop is born with, in the owner's language.

## Why this exists

ERPNext names every account in English when a company is created. We used to
create the company and then rename all 95 accounts into Arabic or Hebrew. That
worked for what the owner READS, but an Account's docname is minted once — as
`account_name - abbr` — and can never be changed afterwards
(`Account.allow_rename` is 0; even `frappe.rename_doc` refuses). So a translated
shop kept English ids like `Sales - BOM36` forever, and every screen that
printed an id instead of a name leaked English back at the owner.

Company creation is the ONE moment the id can be anything we want. Supplying the
chart here means the ids are born Arabic (`المبيعات - BOM36`), so there is no
English left anywhere to leak, no rename step, and no special case for the five
root accounts — which ERPNext refuses to let anyone rename at all.

Arabic docnames are not a gamble: shops on the production bench have carried
them for weeks (`المبيعات - BOM70`, `بنك فلسطين - BOM70`), created through the
setup walkthrough.

## The structure

`STRUCTURE` is our own copy of ERPNext's standard chart — same shape, same
`account_type` / `account_category` values, keyed by English name. `NAMES` maps
each English key to Arabic and Hebrew. `build_chart` walks the structure and
swaps the keys.

Keeping one structure and a name table (rather than three separate charts) is
what stops the languages drifting apart: a new account can only be added in one
place, and the test asserts every key has both translations.

## The trap

`create_charts` takes each account's currency FROM THE CHART when a custom chart
is passed, and from the Company only when it is not:

    "account_currency": child.get("account_currency") if custom_chart
                        else frappe.get_cached_value("Company", company, "default_currency")

So every node must carry `account_currency`, or the whole chart is created with
no currency. `build_chart` injects it — do not remove that.
"""

from barakat.chart_of_accounts.names import NAMES

# Keys ERPNext treats as metadata on a node rather than as a child account.
# Anything else in a node's keys is a child, which is also how ERPNext decides
# `is_group` (see identify_is_group).
METADATA_FIELDS = frozenset(
    {
        "account_name",
        "account_number",
        "account_type",
        "account_category",
        "root_type",
        "is_group",
        "tax_rate",
        "account_currency",
    }
)

SUPPORTED_LANGUAGES = ("ar", "he", "en")

# Our copy of ERPNext's standard chart of accounts.
#
# Transcribed from erpnext/accounts/doctype/account/chart_of_accounts/verified/
# standard_chart_of_accounts.py, with the `_()` wrappers dropped — we do our own
# translation and must not have Frappe's locale change the keys underneath us.
#
# One deliberate addition: VAT, under Duties and Taxes. ERPNext only ships it in
# the Israel country chart, and supplying a custom chart bypasses country charts
# entirely — so without this line a shop here would have nowhere to post VAT.
STRUCTURE = {
    "Application of Funds (Assets)": {
        "Current Assets": {
            "Accounts Receivable": {
                "Debtors": {"account_type": "Receivable", "account_category": "Trade Receivables"}
            },
            "Bank Accounts": {
                "account_type": "Bank",
                "is_group": 1,
                "account_category": "Cash and Cash Equivalents",
            },
            "Cash In Hand": {
                "Cash": {"account_type": "Cash", "account_category": "Cash and Cash Equivalents"},
                "account_type": "Cash",
                "account_category": "Cash and Cash Equivalents",
            },
            "Loans and Advances (Assets)": {
                "Employee Advances": {
                    "account_type": "Payable",
                    "account_category": "Other Receivables",
                },
            },
            "Securities and Deposits": {
                "Earnest Money": {"account_category": "Other Current Assets"}
            },
            "Prepaid Expenses": {"account_category": "Other Current Assets"},
            "Short-term Investments": {"account_category": "Short-term Investments"},
            "Stock Assets": {
                "Stock In Hand": {"account_type": "Stock", "account_category": "Stock Assets"},
                "account_type": "Stock",
                "account_category": "Stock Assets",
            },
            "Tax Assets": {"is_group": 1, "account_category": "Other Current Assets"},
        },
        "Fixed Assets": {
            "Capital Equipment": {
                "account_type": "Fixed Asset",
                "account_category": "Tangible Assets",
            },
            "Electronic Equipment": {
                "account_type": "Fixed Asset",
                "account_category": "Tangible Assets",
            },
            "Furniture and Fixtures": {
                "account_type": "Fixed Asset",
                "account_category": "Tangible Assets",
            },
            "Office Equipment": {
                "account_type": "Fixed Asset",
                "account_category": "Tangible Assets",
            },
            "Plants and Machineries": {
                "account_type": "Fixed Asset",
                "account_category": "Tangible Assets",
            },
            "Buildings": {"account_type": "Fixed Asset", "account_category": "Tangible Assets"},
            "Software": {"account_type": "Fixed Asset", "account_category": "Intangible Assets"},
            "Accumulated Depreciation": {
                "account_type": "Accumulated Depreciation",
                "account_category": "Tangible Assets",
            },
            "CWIP Account": {
                "account_type": "Capital Work in Progress",
                "account_category": "Tangible Assets",
            },
        },
        "Investments": {"is_group": 1, "account_category": "Long-term Investments"},
        "Temporary Accounts": {
            "Temporary Opening": {
                "account_type": "Temporary",
                "account_category": "Other Non-current Assets",
            }
        },
        "root_type": "Asset",
    },
    "Expenses": {
        "Direct Expenses": {
            "Stock Expenses": {
                "Cost of Goods Sold": {
                    "account_type": "Cost of Goods Sold",
                    "account_category": "Cost of Goods Sold",
                },
                "Expenses Included In Asset Valuation": {
                    "account_type": "Expenses Included In Asset Valuation",
                    "account_category": "Other Direct Costs",
                },
                "Expenses Included In Valuation": {
                    "account_type": "Expenses Included In Valuation",
                    "account_category": "Other Direct Costs",
                },
                "Stock Adjustment": {
                    "account_type": "Stock Adjustment",
                    "account_category": "Other Direct Costs",
                },
            },
        },
        "Indirect Expenses": {
            "Administrative Expenses": {"account_category": "Operating Expenses"},
            "Commission on Sales": {"account_category": "Operating Expenses"},
            "Depreciation": {"account_type": "Depreciation", "account_category": "Operating Expenses"},
            "Entertainment Expenses": {"account_category": "Operating Expenses"},
            "Freight and Forwarding Charges": {
                "account_type": "Chargeable",
                "account_category": "Operating Expenses",
            },
            "Legal Expenses": {"account_category": "Operating Expenses"},
            "Marketing Expenses": {
                "account_type": "Chargeable",
                "account_category": "Operating Expenses",
            },
            "Miscellaneous Expenses": {
                "account_type": "Chargeable",
                "account_category": "Operating Expenses",
            },
            "Office Maintenance Expenses": {"account_category": "Operating Expenses"},
            "Office Rent": {"account_category": "Operating Expenses"},
            "Postal Expenses": {"account_category": "Operating Expenses"},
            "Print and Stationery": {"account_category": "Operating Expenses"},
            "Round Off": {"account_type": "Round Off", "account_category": "Operating Expenses"},
            "Salary": {"account_category": "Operating Expenses"},
            "Sales Expenses": {"account_category": "Operating Expenses"},
            "Telephone Expenses": {"account_category": "Operating Expenses"},
            "Travel Expenses": {"account_category": "Operating Expenses"},
            "Utility Expenses": {"account_category": "Operating Expenses"},
            "Write Off": {"account_category": "Operating Expenses"},
            "Exchange Gain/Loss": {"account_category": "Operating Expenses"},
            "Interest Expense": {"account_category": "Finance Costs"},
            "Bank Charges": {"account_category": "Finance Costs"},
            "Gain/Loss on Asset Disposal": {"account_category": "Other Operating Income"},
            "Impairment": {"account_category": "Operating Expenses"},
            "Tax Expense": {"account_category": "Tax Expense"},
        },
        "root_type": "Expense",
    },
    "Income": {
        "Direct Income": {
            "Sales": {"account_category": "Revenue from Operations"},
            "Service": {"account_category": "Revenue from Operations"},
        },
        "Indirect Income": {
            "Interest Income": {"account_category": "Investment Income"},
            "Interest on Fixed Deposits": {"account_category": "Investment Income"},
            "is_group": 1,
        },
        "root_type": "Income",
    },
    "Source of Funds (Liabilities)": {
        "Current Liabilities": {
            "Accounts Payable": {
                "Creditors": {"account_type": "Payable", "account_category": "Trade Payables"},
                "Payroll Payable": {"account_category": "Other Payables"},
            },
            "Accrued Expenses": {"account_category": "Other Current Liabilities"},
            "Customer Advances": {"account_category": "Other Current Liabilities"},
            "Stock Liabilities": {
                "Stock Received But Not Billed": {
                    "account_type": "Stock Received But Not Billed",
                    "account_category": "Trade Payables",
                },
                "Asset Received But Not Billed": {
                    "account_type": "Asset Received But Not Billed",
                    "account_category": "Trade Payables",
                },
            },
            "Duties and Taxes": {
                # See the module docstring: ERPNext only ships VAT in the Israel
                # country chart, which a custom chart bypasses.
                "VAT": {"account_type": "Tax", "account_category": "Current Tax Liabilities"},
                "account_type": "Tax",
                "account_category": "Current Tax Liabilities",
            },
            "Short-term Provisions": {"account_category": "Short-term Provisions"},
            "Loans (Liabilities)": {
                "Secured Loans": {"account_category": "Long-term Borrowings"},
                "Unsecured Loans": {"account_category": "Long-term Borrowings"},
                "Bank Overdraft Account": {"account_category": "Short-term Borrowings"},
            },
        },
        "Non-Current Liabilities": {
            "Long-term Provisions": {"account_category": "Long-term Provisions"},
            "Employee Benefits Obligation": {"account_category": "Other Non-current Liabilities"},
            "is_group": 1,
        },
        "root_type": "Liability",
    },
    "Equity": {
        "Capital Stock": {"account_type": "Equity", "account_category": "Share Capital"},
        "Dividends Paid": {"account_type": "Equity", "account_category": "Reserves and Surplus"},
        "Opening Balance Equity": {
            "account_type": "Equity",
            "account_category": "Reserves and Surplus",
        },
        "Retained Earnings": {"account_type": "Equity", "account_category": "Reserves and Surplus"},
        "Revaluation Surplus": {"account_type": "Equity", "account_category": "Reserves and Surplus"},
        "root_type": "Equity",
    },
}


def translate(english_name, lang):
    """The account's name in `lang`, falling back to English.

    A missing translation must not drop the account from the chart — a shop
    with a hole in its books is far worse than one English line.
    """
    if lang == "en":
        return english_name
    return NAMES.get(english_name, {}).get(lang) or english_name


def build_chart(lang, currency):
    """The chart ERPNext should create, named in `lang` and priced in `currency`.

    `currency` is stamped on every node because create_charts reads it from the
    chart (not from the Company) whenever a custom chart is supplied. See the
    module docstring.
    """
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported chart language: {lang!r}")
    if not currency:
        raise ValueError("a currency is required — create_charts reads it from the chart")

    def walk(node):
        out = {}
        for key, value in node.items():
            if key in METADATA_FIELDS:
                out[key] = value
            else:
                out[translate(key, lang)] = walk(value)
        out["account_currency"] = currency
        return out

    return {translate(name, lang): walk(node) for name, node in STRUCTURE.items()}


def account_names(lang):
    """Every account name the chart will create, in `lang`. Used by the tests."""
    names = []

    def walk(node):
        for key, value in node.items():
            if key not in METADATA_FIELDS:
                names.append(translate(key, lang))
                walk(value)

    for name, node in STRUCTURE.items():
        names.append(translate(name, lang))
        walk(node)
    return names
