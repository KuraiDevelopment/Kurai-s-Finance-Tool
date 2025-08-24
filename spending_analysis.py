"""
Personal Finance Spending Analysis Tool
-------------------------------------

This script provides a command‑line interface for analyzing and summarizing
personal spending habits from one or more CSV files (e.g. bank or credit
card statements).  It automatically categorizes transactions based on user
defined keyword mappings, aggregates expenses by category and by month, and
produces a clean Excel report with tables and charts for easy exploration.

Key Features
~~~~~~~~~~~~
* Accepts an arbitrary number of CSV files exported from your bank(s) or
  credit card provider(s).
* Supports optional date filters so you can focus on a specific time
  window (up to two years as requested by the user).
* Automatically detects common column names for dates, descriptions and
  amounts, and unifies debit/credit formats into a single "Amount" field.
* Categorises each transaction using a simple keyword matching approach.
  The default keyword mapping covers common categories such as groceries,
  dining, utilities, entertainment, etc., but you can provide your own
  ``categories.json`` file to override or extend the defaults.
* Generates an Excel workbook (.xlsx) containing multiple sheets:
  - Raw data: all transactions after processing.
  - Category summary: total spending and income per category.
  - Monthly category summary: spending per category for each month.
  - Top merchants: merchants sorted by total spend and frequency.
  - Charts: bar and line charts visualising spending patterns.
* Highlights potential "bad habits" by identifying categories with the
  highest average monthly spend and listing merchants with unusually
  frequent transactions.

Usage Example
~~~~~~~~~~~~

::

    python spending_analysis.py data/bank_statement.csv \
        --output my_analysis.xlsx \
        --start-date 2023-01-01 \
        --end-date 2025-01-01 \
        --categories categories.json

The above command would read ``bank_statement.csv``, apply categories from
``categories.json``, filter the results between January 1 2023 and
January 1 2025, and write a report to ``my_analysis.xlsx``.

When run without any date filters the script analyses the full date range
contained in the input files.  Should you supply multiple CSVs the tool
will concatenate them automatically.

This script requires ``pandas`` and ``xlsxwriter``, both of which should
already be installed in the provided environment.  If you encounter
errors related to missing packages you can install them with
``pip install pandas xlsxwriter``.

"""

import argparse
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import subprocess
import tempfile
import re  # For regex pattern matching
# Attempt to import scikit‑learn for ML categorisation.  If unavailable
# (e.g. not installed on the user's machine), we disable the ML
# categorisation gracefully.
try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.naive_bayes import MultinomialNB  # type: ignore
    # Import logistic regression for more flexible classification
    from sklearn.linear_model import LogisticRegression  # type: ignore
    _SKLEARN_AVAILABLE = True
except ImportError:
    # If scikit‑learn (or any of the required modules) is not installed,
    # disable machine learning categorisation gracefully.
    _SKLEARN_AVAILABLE = False



def detect_columns(df: pd.DataFrame) -> Tuple[str, str, str]:
    """Attempt to guess the date, description and amount columns.

    Many banks export statements with slightly different column names.
    This helper looks for typical substrings to identify the relevant
    columns.  If a required column cannot be located an informative
    ``ValueError`` is raised.

    Parameters
    ----------
    df: DataFrame
        The input data frame whose columns should be analysed.

    Returns
    -------
    Tuple[str, str, str]
        A tuple containing the detected date column, description column
        and amount column names.
    """
    date_candidates = [
        c for c in df.columns
        if re.search(r"date", str(c), re.IGNORECASE)
    ]
    desc_candidates = [
        c for c in df.columns
        if re.search(r"desc|memo|details|transaction", str(c), re.IGNORECASE)
    ]
    # Some banks split credit and debit into separate columns.  If you
    # find columns containing "amount", "debit" or "credit", treat them
    # accordingly.  We'll unify them later into a single Amount column.
    amount_candidates = [
        c for c in df.columns
        if re.search(r"amount|debit|credit", str(c), re.IGNORECASE)
    ]

    if not date_candidates:
        raise ValueError(
            "Could not detect a date column. Please rename your date column to include the word 'date'."
        )
    if not desc_candidates:
        raise ValueError(
            "Could not detect a description column. Please rename your description/memo column to include 'desc', 'memo' or 'transaction'."
        )
    if not amount_candidates:
        raise ValueError(
            "Could not detect an amount column. Please ensure your CSV has a column named 'Amount', 'Debit', 'Credit', etc."
        )

    # Pick the first candidate for each type
    date_col = date_candidates[0]
    desc_col = desc_candidates[0]
    amount_col = amount_candidates[0]
    return date_col, desc_col, amount_col


def load_categories(path: Optional[Path]) -> Dict[str, str]:
    """Load a keyword→category mapping from a JSON file.

    If the user provides a categories file, load it; otherwise use
    built‑in defaults.  The mapping should be a dictionary where the
    keys are keywords (case insensitive) and the values are the
    corresponding category names.  When categorising transactions
    the first matching keyword (in sorted order) will determine the
    category.

    Parameters
    ----------
    path: Path or None
        The path to a JSON file or None to use defaults.

    Returns
    -------
    dict
        A mapping from lowercase keyword to category.
    """
    if path is None:
        # Default keyword to category mapping.  Feel free to extend this
        # list or override it with a custom JSON file.  See the README
        # for details on the expected format.
        return {
            # Groceries
            "grocery": "Groceries",
            "supermarket": "Groceries",
            "walmart": "Groceries",
            "target": "Groceries",
            "whole foods": "Groceries",
            # Dining
            "restaurant": "Dining",
            "cafe": "Dining",
            "coffee": "Dining",
            "starbucks": "Dining",
            "mcdonald": "Dining",
            "chipotle": "Dining",
            "pizza": "Dining",
            # Transportation
            "uber": "Transportation",
            "lyft": "Transportation",
            "taxi": "Transportation",
            "gas": "Transportation",
            "fuel": "Transportation",
            "shell": "Transportation",
            # Utilities & Housing
            "electric": "Utilities",
            "utility": "Utilities",
            "water": "Utilities",
            "rent": "Housing",
            "mortgage": "Housing",
            "internet": "Utilities",
            "comcast": "Utilities",
            "phone": "Utilities",
            # Entertainment
            "netflix": "Entertainment",
            "hulu": "Entertainment",
            "spotify": "Entertainment",
            "movie": "Entertainment",
            "cinema": "Entertainment",
            # Shopping
            "amazon": "Shopping",
            "ebay": "Shopping",
            "mall": "Shopping",
            "shop": "Shopping",
            "fashion": "Shopping",
            # Healthcare
            "pharmacy": "Healthcare",
            "drug": "Healthcare",
            "doctor": "Healthcare",
            "hospital": "Healthcare",
            "clinic": "Healthcare",
            # Insurance
            "insurance": "Insurance",
            "premium": "Insurance",
            # Travel
            "hotel": "Travel",
            "air": "Travel",
            "flight": "Travel",
            "airbnb": "Travel",
            # Income / deposits
            "salary": "Income",
            "payroll": "Income",
            "deposit": "Income",
            "interest": "Income",
            # Transfers and others
            "transfer": "Transfer",

            # Peer‑to‑Peer / third‑party payment services
            # Explicitly classify popular P2P services separately from generic
            # transfers so they don’t all fall under the broad 'Transfer' category.
            "cashapp": "Peer to Peer",
            "cash app": "Peer to Peer",
            "cash app payment": "Peer to Peer",
            "zelle": "Peer to Peer",
            "venmo": "Peer to Peer",
            "paypal": "Peer to Peer",

            # Food delivery services
            # Truncated codes (e.g. "DOORDA") often appear on statements; map
            # them explicitly to a delivery category.
            "doordash": "Food Delivery",
            "door dash": "Food Delivery",
            "doorda": "Food Delivery",

            # Kids / Childcare
            "kid": "Kids",
            "child": "Kids",
            "childcare": "Kids",
            "toy": "Kids",
            "toy store": "Kids",
            "baby": "Kids",
            "pediatrician": "Kids",

            # Pets
            "pet": "Pets",
            "dog": "Pets",
            "cat": "Pets",
            "vet": "Pets",
            "petco": "Pets",
            "petsmart": "Pets",

            # Home Improvement
            "home depot": "Home Improvement",
            "home improvement": "Home Improvement",
            "lowe's": "Home Improvement",
            "lowes": "Home Improvement",
            "hardware": "Home Improvement",
            "renovation": "Home Improvement",
            "flooring": "Home Improvement",
            "plumbing": "Home Improvement",
            "furnish": "Home Improvement",
            "ikea": "Home Improvement",

            # Office Supplies
            "office depot": "Office Supplies",
            "staples": "Office Supplies",
            "office supply": "Office Supplies",
            "officemax": "Office Supplies",
            "office max": "Office Supplies",

            # Software Subscriptions & SaaS
            "software": "Software Subscription",
            "subscription": "Software Subscription",
            "saas": "Software Subscription",
            "adobe": "Software Subscription",
            "microsoft": "Software Subscription",
            "office 365": "Software Subscription",
            "zoom": "Software Subscription",
            "dropbox": "Software Subscription",
            "aws": "Software Subscription",
            "azure": "Software Subscription",
            "g suite": "Software Subscription",
            "google": "Software Subscription",
            "slack": "Software Subscription",
            "github": "Software Subscription",

            # Business Travel
            "airline": "Business Travel",
            "delta": "Business Travel",
            "american airlines": "Business Travel",
            "united": "Business Travel",
            "hotel": "Business Travel",
            "marriott": "Business Travel",
            "hilton": "Business Travel",
            "expedia": "Business Travel",
            "travelocity": "Business Travel",

            # Client Entertainment
            "client": "Client Entertainment",
            "business dinner": "Client Entertainment",
            "business lunch": "Client Entertainment",
            "team lunch": "Client Entertainment",

            # Payroll and Contractors
            "payroll": "Payroll/Contractors",
            "contractor": "Payroll/Contractors",
            "employee": "Payroll/Contractors",
            "wages": "Payroll/Contractors",
            "salary payment": "Payroll/Contractors",
        }
    else:
        with open(path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        # Normalize keys to lower case for case‑insensitive matching
        return {k.lower(): v for k, v in mapping.items()}


def load_regex_patterns(path: Optional[Path]) -> Dict[str, str]:
    """Load a dictionary of regex patterns to categories.

    Each key in the returned dictionary should be a valid regular
    expression pattern (compatible with ``re.search``), and the value
    is the corresponding category.  If ``path`` is None or the
    file cannot be read, an empty dictionary is returned.

    Parameters
    ----------
    path : Path or None
        The path to a JSON file containing regex→category mappings.

    Returns
    -------
    dict
        A mapping of regex patterns to category names.
    """
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            patterns = json.load(f)
        # Ensure keys are strings and values are category names
        return {str(p): c for p, c in patterns.items()}
    except Exception:
        return {}


def categorise_row(
    description: str,
    mapping: Dict[str, str],
    regex_mapping: Optional[Dict[str, str]] = None,
) -> str:
    """Assign a category to a transaction description using regex and keyword mapping.

    The categorisation routine follows a series of steps to improve
    accuracy and avoid over–classifying transactions as peer‑to‑peer:

    1. **Normalise** the description to uppercase and strip extraneous
       store numbers or location suffixes.  This helps match variations
       in vendor names.
    2. **Aggregator detection**: If the description indicates a third‑party
       payment platform such as PayPal, Venmo, Cash App or Zelle, the
       function attempts to extract the underlying merchant (e.g.
       ``PAYPAL *WALMART``).  The extracted merchant is then
       classified using the same regex and keyword mappings; only if
       no merchant can be resolved does the function return the
       generic ``"Peer to Peer"`` category.
    3. **Regular expression matching**: High‑priority regex patterns are
       checked first.  The first pattern that matches assigns the
       category.
    4. **Keyword matching**: If no regex matches, the function
       searches for keywords from the mapping in order and assigns
       the corresponding category.
    5. **Fallback**: If nothing matches, return ``"Uncategorized"``.

    Parameters
    ----------
    description : str
        The transaction description (memo or details) field.
    mapping : dict
        A mapping of lowercase keywords to category names.
    regex_mapping : dict or None
        A mapping of regex pattern strings to category names.  Patterns
        are evaluated using :func:`re.search` on the normalised
        description.  If ``None`` or empty, regex matching is skipped.

    Returns
    -------
    str
        The assigned category.
    """
    if not isinstance(description, str):
        # Guard against non‑string descriptions
        desc_raw = ""
    else:
        desc_raw = description

    # Normalise: uppercase, remove long numeric sequences, store numbers and
    # trailing state abbreviations/zip codes, and collapse spaces.  This
    # improves matching consistency across formats such as "WAL‑MART #1234"
    # and "Walmart Supercenter 0047, OH".
    def _normalise(d: str) -> str:
        d = d.upper()
        # Remove long numeric blobs and store numbers (e.g. "#1234", "ST 0047")
        d = re.sub(r"[#-]?\d{3,}", " ", d)
        d = re.sub(r"\bST\s*\d{2,}\b", " ", d)
        # Remove trailing city/state/zip codes (", OH", ", OH 44512")
        d = re.sub(r",\s*[A-Z]{2}(\s+\d{5})?$", "", d)
        # Collapse multiple whitespace
        d = re.sub(r"\s+", " ", d).strip()
        return d

    desc_norm = _normalise(desc_raw)
    desc_lower = desc_norm.lower()

    # Aggregator / peer‑to‑peer detection.  If a description contains
    # PayPal, Venmo, Cash App, Zelle or similar, attempt to extract the
    # merchant after a star ("*") pattern.  If a merchant is found we
    # classify that merchant using regex/keyword mappings.  Otherwise
    # default to the generic 'Peer to Peer' category.
    aggregator_keywords = [
        "PAYPAL", "PP", "VENMO", "CASH APP", "CASHAPP", "ZELLE", "SQUARE CASH", "SQUARE", "CASH \*", "VENMO \*"
    ]
    upper_norm = desc_norm.upper()
    # We search only if the description contains a known aggregator keyword
    if any(agg in upper_norm for agg in aggregator_keywords):
        # Attempt to extract a merchant after a star.  Many processors
        # format descriptions like "PAYPAL * WALMART" or "PAYPAL *GIRARDWOK".
        # We'll search for a star followed by one or more alphanumeric
        # segments separated by spaces or punctuation.
        m = re.search(r"\*\s*([A-Z0-9 ._-]+)", upper_norm)
        if m:
            merchant_fragment = m.group(1).strip()
            # Normalise merchant fragment and classify using regex/keywords
            merchant_norm = _normalise(merchant_fragment)
            # Check regex patterns with the merchant only
            if regex_mapping:
                for pattern, cat in regex_mapping.items():
                    try:
                        if re.search(pattern, merchant_norm.lower()):
                            return cat
                    except re.error:
                        continue
            for keyword, category in mapping.items():
                if keyword in merchant_norm.lower():
                    return category
            # If merchant fragment fails to match, fall back to 'Peer to Peer'
            return "Peer to Peer"
        # If no merchant found after star, treat as pure P2P
        return "Peer to Peer"

    # Step 3: check regex patterns on the full normalised description
    if regex_mapping:
        for pattern, cat in regex_mapping.items():
            try:
                if re.search(pattern, desc_lower):
                    return cat
            except re.error:
                # Ignore invalid regex patterns
                continue
    # Step 4: keyword matching on the normalised description
    for keyword, category in mapping.items():
        if keyword in desc_lower:
            return category
    # Step 5: nothing matched
    return "Uncategorized"


def preprocess_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and normalise the raw transaction data.

    This function ensures the date column is parsed correctly, merges
    separate debit/credit columns into a single numeric Amount field,
    and trims unnecessary whitespace from textual fields.  It also
    attempts to preserve the original column names for reference.

    Parameters
    ----------
    df: DataFrame
        Raw input data read directly from CSV.

    Returns
    -------
    DataFrame
        Cleaned data with standardised columns: Date, Description,
        Amount.
    """
    date_col, desc_col, amount_col = detect_columns(df)

    # Rename to standard names for easier downstream processing
    df = df.rename(columns={date_col: "Date", desc_col: "Description"})

    # Handle amount columns: some banks have separate debit and credit
    # columns.  If the detected amount column is 'debit' or 'credit'
    # we'll attempt to compute a unified Amount field accordingly.
    cols = [c.lower() for c in df.columns]
    debit_cols = [c for c in df.columns if re.search(r"debit", c, re.IGNORECASE)]
    credit_cols = [c for c in df.columns if re.search(r"credit", c, re.IGNORECASE)]

    if debit_cols and credit_cols:
        # Create unified Amount: credit minus debit (credits positive, debits negative)
        debit_col_name = debit_cols[0]
        credit_col_name = credit_cols[0]
        df["Amount"] = df[credit_col_name].fillna(0).astype(float) - df[debit_col_name].fillna(0).astype(float)
    else:
        # Single amount column might already carry positive or negative values
        df = df.rename(columns={amount_col: "Amount"})
        df["Amount"] = pd.to_numeric(df["Amount"].astype(str).str.replace(',', '').str.replace('$', ''), errors="coerce")

    # Parse dates
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    # Remove rows with invalid dates or missing amounts
    df = df.dropna(subset=["Date", "Amount"])
    # Strip whitespace from descriptions
    df["Description"] = df["Description"].astype(str).str.strip()
    return df[["Date", "Description", "Amount"]]


def load_transactions(files: Iterable[Path]) -> pd.DataFrame:
    """Load and combine multiple CSV files into a single DataFrame.

    Parameters
    ----------
    files: Iterable[Path]
        A list of CSV file paths.

    Returns
    -------
    DataFrame
        Combined and preprocessed transaction data.
    """
    frames: List[pd.DataFrame] = []
    for path in files:
        # Support PDF inputs by converting them to CSV on the fly.  If the
        # file has a .pdf suffix we invoke ``convert_pdf_to_csv`` to
        # generate a temporary CSV and then read it.  Otherwise we read
        # the CSV directly.  Temporary files are cleaned up after
        # reading.
        input_path = Path(path)
        if input_path.suffix.lower() == ".pdf":
            # Create a temporary CSV file for the conversion
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                tmp_csv = Path(tmp.name)
            try:
                convert_pdf_to_csv(input_path, tmp_csv)
                df_read = pd.read_csv(tmp_csv)
            except Exception as e:
                raise RuntimeError(f"Failed to convert PDF {input_path}: {e}")
            finally:
                # Remove the temporary file
                try:
                    tmp_csv.unlink()
                except Exception:
                    pass
        else:
            try:
                df_read = pd.read_csv(input_path)
            except Exception as e:
                # Attempt to recover from tokenisation errors by locating
                # the transaction section of the file.  Some bank exports
                # include account metadata with commas (e.g. an address)
                # before the header row.  We'll scan for a line that
                # resembles the transaction header (Date,Amount,Description)
                # and parse the remainder as CSV.  If this fails, re‑raise
                # the original error.
                try:
                    import io
                    content = input_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    header_idx = None
                    for i, line in enumerate(content):
                        if re.search(r"^\s*Date\s*,\s*.*Amount.*", line, re.IGNORECASE):
                            header_idx = i
                            break
                    if header_idx is None:
                        raise
                    csv_part = "\n".join(content[header_idx:])
                    # Manually parse lines to handle descriptions with commas.
                    lines = csv_part.split("\n")
                    # Extract year from metadata above the header (look for YYYY)
                    statement_year = None
                    year_pattern = re.compile(r"(19|20)\d{2}")
                    for meta_line in content[:header_idx]:
                        match = year_pattern.search(meta_line)
                        if match:
                            statement_year = match.group(0)
                            break
                    transactions = []
                    for row in lines[1:]:
                        if not row.strip():
                            continue
                        parts = row.split(",")
                        if len(parts) < 3:
                            continue
                        date_str_raw = parts[0].strip()
                        amount_str = parts[1].strip()
                        desc = ",".join(parts[2:]).strip()
                        # Append year if missing (e.g. 07/14)
                        if statement_year is not None and re.match(r"^\d{1,2}/\d{1,2}$", date_str_raw):
                            date_str = f"{date_str_raw}/{statement_year}"
                        else:
                            date_str = date_str_raw
                        try:
                            amount_val = float(amount_str)
                        except ValueError:
                            continue
                        transactions.append([date_str, desc, amount_val])
                    if transactions:
                        df_read = pd.DataFrame(transactions, columns=["Date", "Description", "Amount"])
                    else:
                        # Fall back to pandas if manual parse fails
                        df_read = pd.read_csv(io.StringIO(csv_part))
                except Exception:
                    raise RuntimeError(f"Failed to read {input_path}: {e}")
        # Preprocess the DataFrame (date parsing, column detection, etc.)
        df_proc = preprocess_dataframe(df_read)
        frames.append(df_proc)
    if not frames:
        return pd.DataFrame(columns=["Date", "Description", "Amount"])
    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values("Date", inplace=True)
    return combined


def summarise_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate total spend and income per category.

    Expenses are indicated by negative amounts and incomes by positive
    amounts.  The returned DataFrame includes separate columns for
    expenses and incomes so you can quickly identify where the money
    goes and comes from.  A net column is included for completeness.

    Parameters
    ----------
    df: DataFrame
        Data with a 'Category' column and numeric 'Amount' column.

    Returns
    -------
    DataFrame
        Aggregated summary by category with columns 'Expense',
        'Income' and 'Net'.  Expenses are shown as positive numbers
        for easier reading.
    """
    # Separate expenses and incomes
    df["Expense"] = df["Amount"].where(df["Amount"] < 0, 0).abs()
    df["Income"] = df["Amount"].where(df["Amount"] > 0, 0)
    summary = (
        df.groupby("Category")[["Expense", "Income"]]
        .sum()
        .sort_values("Expense", ascending=False)
        .assign(Net=lambda d: d["Income"] - d["Expense"])
    )
    return summary.reset_index()


def summarise_monthly_category(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate spend by category for each calendar month.

    The function groups data by year‑month and category, summing
    negative amounts (expenses) only.  Positive amounts (income) are
    ignored in this summary to focus on spending patterns.  If a
    category does not appear in a given month, its value will be
    omitted.  You can pivot this table later for a matrix view.

    Parameters
    ----------
    df: DataFrame
        Data with columns 'Date', 'Category' and numeric 'Amount'.

    Returns
    -------
    DataFrame
        Monthly spending per category with columns 'YearMonth',
        'Category' and 'Expense'.
    """
    df = df.copy()
    df["YearMonth"] = df["Date"].dt.to_period("M").astype(str)
    monthly = (
        df[df["Amount"] < 0]
        .groupby(["YearMonth", "Category"])["Amount"]
        .sum()
        .abs()
        .reset_index(name="Expense")
        .sort_values(["YearMonth", "Expense"], ascending=[True, False])
    )
    return monthly


def summarise_comparative_periods(
    df: pd.DataFrame,
    freq: str = "M",
) -> pd.DataFrame:
    """Compare spending across the two most recent periods.

    This function aggregates total spending (expenses) by category for the two
    most recent periods (months or quarters) in the data set and computes
    the difference and percentage change between them.  Positive amounts
    represent expenses (i.e. negative numbers in the original data are
    converted to positive for reporting purposes).

    Parameters
    ----------
    df : DataFrame
        Transaction data with columns 'Date', 'Category' and 'Amount'.
    freq : {'M', 'Q'}, default 'M'
        The period frequency: 'M' for monthly comparisons or 'Q' for
        quarterly comparisons.  See :func:`pandas.Grouper` for other
        available frequencies.

    Returns
    -------
    DataFrame
        A DataFrame with columns ``['Category', 'Period1', 'Period2', 'Diff', 'PctChange']``.
        ``Period1`` is the earlier period, ``Period2`` is the most recent period.
        ``Diff`` is ``Period2 - Period1`` and ``PctChange`` is the percentage change.
        If there are fewer than two distinct periods, an empty DataFrame is returned.
    """
    if df.empty:
        return pd.DataFrame(columns=["Category", "Period1", "Period2", "Diff", "PctChange"])
    # Ensure Date is datetime
    dates = pd.to_datetime(df["Date"], errors="coerce")
    # Build period labels
    periods = dates.dt.to_period(freq)
    df = df.copy()
    df["Period"] = periods
    # Compute total spend per category and period (expenses only; treat positive incomes separately)
    grouped = (
        df.groupby(["Category", "Period"])["Amount"]
        .sum()
        .reset_index()
    )
    # Convert amounts to positive for expenses (assuming negative values represent expenses)
    grouped["Spend"] = grouped["Amount"].apply(lambda x: -x if x < 0 else x)
    # Determine the two most recent periods
    unique_periods = grouped["Period"].dropna().unique()
    if len(unique_periods) < 2:
        return pd.DataFrame(columns=["Category", "Period1", "Period2", "Diff", "PctChange"])
    # Sort periods chronologically
    unique_periods = sorted(unique_periods)
    period1, period2 = unique_periods[-2], unique_periods[-1]
    # Pivot to get spending per category for the two periods
    pivot = grouped[grouped["Period"].isin([period1, period2])].pivot(
        index="Category", columns="Period", values="Spend"
    ).fillna(0)
    # Ensure both periods are present
    if period1 not in pivot.columns:
        pivot[period1] = 0
    if period2 not in pivot.columns:
        pivot[period2] = 0
    # Compute difference and percentage change
    pivot = pivot.rename(columns={period1: "Period1", period2: "Period2"})
    pivot["Diff"] = pivot["Period2"] - pivot["Period1"]
    # Avoid division by zero
    pivot["PctChange"] = pivot.apply(
        lambda row: (row["Diff"] / row["Period1"]) * 100 if row["Period1"] != 0 else None,
        axis=1,
    )
    pivot = pivot.reset_index()
    return pivot[["Category", "Period1", "Period2", "Diff", "PctChange"]]


def summarise_top_merchants(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Identify top merchants by total spend and frequency.

    This function simply groups transactions by the cleaned description
    text.  To make the output more readable, descriptions are normalised
    by trimming extra spaces and converting to title case.  Both
    expense and income transactions are included, but the totals shown
    are for absolute spend (expenses only).  The frequency column
    counts how many transactions occurred with each merchant.

    Parameters
    ----------
    df: DataFrame
        Data with 'Description' and numeric 'Amount' columns.
    n: int
        Number of top merchants to return.

    Returns
    -------
    DataFrame
        Top merchants sorted by total expense and frequency.
    """
    # Normalise description: collapse multiple spaces and capitalise
    df_clean = df.copy()
    df_clean["Merchant"] = (
        df_clean["Description"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip().str.title()
    )
    # Only consider expenses for total spend
    expenses = df_clean[df_clean["Amount"] < 0]
    summary = (
        expenses.groupby("Merchant")["Amount"]
        .agg([("TotalExpense", lambda x: -x.sum()), ("Frequency", "count")])
        .sort_values(["TotalExpense", "Frequency"], ascending=[False, False])
        .head(n)
        .reset_index()
    )
    return summary


def detect_bad_habits(df: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """Identify categories that may indicate bad spending habits.

    A simple heuristic is applied: calculate the average monthly spend
    per category and flag the categories with the highest averages.
    A high monthly average does not necessarily mean a habit is
    "bad", but it signals where a disproportionate amount of money is
    being spent.  Adjust ``top_n`` to see more or fewer categories.

    Parameters
    ----------
    df: DataFrame
        Data with 'Date', 'Category' and numeric 'Amount'.
    top_n: int
        Number of categories to highlight.

    Returns
    -------
    DataFrame
        Categories ranked by average monthly expense.
    """
    # Filter to expenses only
    expenses = df[df["Amount"] < 0].copy()
    expenses["YearMonth"] = expenses["Date"].dt.to_period("M").astype(str)
    monthly_totals = (
        expenses.groupby(["Category", "YearMonth"])["Amount"].sum().abs().reset_index()
    )
    # Compute average per month
    avg_monthly = (
        monthly_totals.groupby("Category")["Amount"]
        .mean()
        .reset_index(name="AvgMonthlyExpense")
        .sort_values("AvgMonthlyExpense", ascending=False)
        .head(top_n)
    )
    return avg_monthly


def convert_pdf_to_csv(pdf_path: Path, output_csv_path: Path) -> None:
    """Convert a bank statement PDF into a CSV file.

    This function relies on the ``pdftotext`` command line utility (part of
    the Poppler tools) to extract plain text from the PDF.  It then uses
    simple heuristics to parse each line into a date, description and
    amount.  The result is saved as a CSV with columns ``Date``,
    ``Description`` and ``Amount``.  Note that PDF statements vary
    significantly in format; this parser works best for statements
    where each transaction is on a separate line with the date at the
    start and the amount at the end.

    Parameters
    ----------
    pdf_path: Path
        Path to the PDF file to convert.
    output_csv_path: Path
        Path where the resulting CSV should be written.

    Raises
    ------
    RuntimeError
        If ``pdftotext`` is not installed or parsing fails.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    # Run pdftotext to extract text with layout preserved
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise RuntimeError("pdftotext command not found. Please install poppler-utils.") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"pdftotext failed: {e.stderr.decode()}")
    text = result.stdout.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    rows = []
    import re
    date_pattern = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4})")
    amount_pattern = re.compile(r"[-]?\$?\d[\d,]*\.\d{2}")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        date_match = date_pattern.match(line)
        if not date_match:
            continue
        date_str = date_match.group(1)
        # Remove date from line
        remainder = line[len(date_str):].strip()
        # Find last amount in the line
        amt_match = amount_pattern.search(remainder)
        if not amt_match:
            continue
        amount_str = amt_match.group(0)
        # Remove amount from remainder
        desc = remainder[: amt_match.start()].strip()
        # Clean amount (remove $ and commas)
        amount_clean = amount_str.replace("$", "").replace(",", "")
        try:
            amount = float(amount_clean)
        except ValueError:
            continue
        rows.append([date_str, desc, amount])
    if not rows:
        # Fall back to OCR if no text could be extracted.  Some statements
        # are scanned images without an embedded text layer, so pdftotext
        # will return nothing.  If Tesseract OCR and pdftoppm are
        # available, attempt to recognise the text from images.
        if not _try_ocr_pdf_to_csv(pdf_path, output_csv_path):
            raise RuntimeError(
                "No transactions could be parsed from the PDF. Review the PDF layout or adjust the parser. "
                "If the statement is scanned, install Tesseract OCR and Poppler tools (pdftoppm) to enable OCR."
            )
        return
    # Write CSV normally
    df = pd.DataFrame(rows, columns=["Date", "Description", "Amount"])
    df.to_csv(output_csv_path, index=False)


def _try_ocr_pdf_to_csv(pdf_path: Path, output_csv_path: Path) -> bool:
    """Attempt to OCR a scanned PDF into CSV.

    This helper uses ``pdftoppm`` to convert each PDF page into a PNG
    image and ``tesseract`` to recognise text.  It parses the
    extracted text with the same heuristics as ``convert_pdf_to_csv``.

    Parameters
    ----------
    pdf_path : Path
        The source PDF file.
    output_csv_path : Path
        Where to write the resulting CSV.

    Returns
    -------
    bool
        True if OCR succeeded and at least one transaction was parsed,
        False otherwise.
    """
    import shutil
    # Check for required external commands
    if shutil.which("pdftoppm") is None:
        return False
    if shutil.which("tesseract") is None:
        return False
    # Create a temporary directory for intermediate files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir = Path(tmpdir)
        prefix = tmp_dir / "page"
        # Convert PDF pages to PNG images
        try:
            subprocess.run([
                "pdftoppm",
                "-png",
                str(pdf_path),
                str(prefix),
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            return False
        # Collect generated images
        images = sorted(tmp_dir.glob("page-*.png"))
        if not images:
            return False
        rows: List[List[object]] = []
        date_pattern = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4})")
        amount_pattern = re.compile(r"[-]?\$?\d[\d,]*\.\d{2}")
        for img_path in images:
            # Run tesseract on the image
            out_base = tmp_dir / img_path.stem
            try:
                subprocess.run([
                    "tesseract",
                    str(img_path),
                    str(out_base),
                    "-l",
                    "eng",
                    "--psm",
                    "6",
                ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError:
                continue
            txt_path = out_base.with_suffix(".txt")
            if not txt_path.exists():
                continue
            content = txt_path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                date_match = date_pattern.match(line)
                if not date_match:
                    continue
                date_str = date_match.group(1)
                remainder = line[len(date_str):].strip()
                amt_match = amount_pattern.search(remainder)
                if not amt_match:
                    continue
                amount_str = amt_match.group(0)
                desc = remainder[: amt_match.start()].strip()
                amount_clean = amount_str.replace("$", "").replace(",", "")
                try:
                    amount_val = float(amount_clean)
                except ValueError:
                    continue
                rows.append([date_str, desc, amount_val])
        if rows:
            df = pd.DataFrame(rows, columns=["Date", "Description", "Amount"])
            df.to_csv(output_csv_path, index=False)
            return True
        return False


# Predefined tips for each spending category.  These suggestions are
# generic and can be customised further.  Where possible we include
# local recommendations for Youngstown, Ohio.  The Yellow Pages list
# identifies discount grocers like Aldi (3497 Belmont Ave) with
# friendly staff and clean aisles【162116632031487†L87-L105】, Sparkle Market on
# South Ave which is praised for good service and reasonable prices【162116632031487†L131-L154】,
# and Save‑A‑Lot on Gypsy Ln offering discount groceries【162116632031487†L253-L267】.
CATEGORY_TIPS: Dict[str, str] = {
    "Groceries": (
        "Plan meals and make a shopping list to avoid impulse purchases. "
        "Consider shopping at discount grocers such as Aldi at 3497 Belmont Ave, "
        "Sparkle Market on South Ave or Save‑A‑Lot on Gypsy Ln for lower prices【162116632031487†L87-L105】【162116632031487†L131-L154】【162116632031487†L253-L267】."
    ),
    "Dining": (
        "Cook at home more often and pack meals for work. Limit coffee shop "
        "visits and choose restaurants for special occasions to cut costs."
    ),
    "Shopping": (
        "Pause before making non‑essential purchases. Unsubscribe from marketing "
        "emails and consider waiting 24 hours before checking out online carts."
    ),
    "Transportation": (
        "Use public transportation or carpool when possible. Maintain your vehicle "
        "to improve fuel efficiency and combine errands into a single trip."
    ),
    "Entertainment": (
        "Review your streaming and subscription services and cancel those you rarely "
        "use. Look for free community events or library programmes."
    ),
    "Utilities": (
        "Turn off lights when not in use, switch to energy‑saving bulbs and program "
        "thermostats to reduce heating/cooling costs. Compare providers for better rates."
    ),
    "Housing": (
        "Consider negotiating rent or refinancing your mortgage. Evaluate whether you "
        "could downsize or share space to lower housing costs."
    ),
    "Healthcare": (
        "Schedule regular preventative check‑ups to avoid costly emergencies. Compare "
        "pharmacy prices and choose generic medications when appropriate."
    ),
    "Insurance": (
        "Shop around for insurance policies annually to ensure you get competitive "
        "rates. Adjust deductibles and bundle policies to save money."
    ),
    "Travel": (
        "Plan trips in advance and travel during off‑peak seasons. Use loyalty programmes "
        "and credit card points to reduce costs."
    ),
    "Dining": (
        "Cook at home more often and pack meals for work. Limit coffee shop visits and "
        "choose restaurants for special occasions to cut costs."
    ),
    "Uncategorized": (
        "Review these transactions individually; consider updating your category keywords "
        "to improve future analyses."
    ),
}


def generate_ai_suggestions(bad_habits_df: pd.DataFrame) -> pd.DataFrame:
    """Generate saving suggestions for categories identified as bad habits.

    This function looks up a predefined tip for each category flagged
    by ``detect_bad_habits``.  If a category is not in the predefined
    dictionary, a generic prompt encourages users to reflect on ways to
    reduce spending in that category.

    Parameters
    ----------
    bad_habits_df: DataFrame
        Output from ``detect_bad_habits``, containing a 'Category'
        column.

    Returns
    -------
    DataFrame
        A table with columns 'Category' and 'Suggestion'.
    """
    suggestions: List[Tuple[str, str]] = []
    for cat in bad_habits_df["Category"]:
        tip = CATEGORY_TIPS.get(cat, f"Consider ways to reduce spending in your {cat} category.")
        suggestions.append((cat, tip))
    return pd.DataFrame(suggestions, columns=["Category", "Suggestion"])


def ml_categorise_uncategorised(df: pd.DataFrame) -> pd.DataFrame:
    """Use a machine‑learning model to assign categories to uncategorised rows.

    This function trains a simple text classifier (Multinomial Naive Bayes)
    on existing categorised transactions and predicts categories for
    descriptions where ``Category`` is "Uncategorized".  It uses a
    TF‑IDF vectoriser to convert descriptions into feature vectors.

    Parameters
    ----------
    df : DataFrame
        Transaction data with columns 'Description' and 'Category'.

    Returns
    -------
    DataFrame
        DataFrame with potentially fewer uncategorised rows.  If
        training data is insufficient (fewer than 2 categories), the
        input is returned unchanged.
    """
    # If scikit‑learn is not available, return the input unchanged
    if not _SKLEARN_AVAILABLE:
        return df
    # Identify labelled examples
    labelled = df[df["Category"] != "Uncategorized"]
    if labelled.empty:
        return df
    # Need at least two distinct categories and a minimum number of samples to train
    if labelled["Category"].nunique() < 2 or len(labelled) < 5:
        return df
    # Train TF‑IDF vectoriser on all descriptions (to include vocab for unlabelled)
    vectoriser = TfidfVectorizer(stop_words="english")
    X_all = vectoriser.fit_transform(df["Description"].astype(str))
    # Split into train and all indexes
    X_train = X_all[labelled.index]
    y_train = labelled["Category"]
    # Train classifier.  Prefer Logistic Regression for richer multi‑class
    # discrimination; if it fails (e.g. ill‑posed problem), fall back to
    # Multinomial Naive Bayes.
    try:
        clf = LogisticRegression(max_iter=1000)
        clf.fit(X_train, y_train)
    except Exception:
        clf = MultinomialNB()
        clf.fit(X_train, y_train)
    # Predict on all descriptions
    y_pred = clf.predict(X_all)
    df = df.copy()
    df["_ML_Pred"] = y_pred
    # Replace category for uncategorised rows
    uncategorised_mask = df["Category"] == "Uncategorized"
    df.loc[uncategorised_mask, "Category"] = df.loc[uncategorised_mask, "_ML_Pred"]
    df = df.drop(columns=["_ML_Pred"])
    return df


def write_excel_report(
    df: pd.DataFrame,
    output_path: Path,
    summary: pd.DataFrame,
    monthly: pd.DataFrame,
    top_merchants: pd.DataFrame,
    bad_habits: pd.DataFrame,
    suggestions: pd.DataFrame,
    comparative_month: Optional[pd.DataFrame] = None,
    comparative_quarter: Optional[pd.DataFrame] = None,
) -> None:
    """Create an Excel workbook report with multiple sheets and charts.

    Parameters
    ----------
    df: DataFrame
        The cleaned and categorised transaction data.
    output_path: Path
        The destination path for the .xlsx file.
    summary: DataFrame
        Category summary returned by ``summarise_by_category``.
    monthly: DataFrame
        Monthly category summary returned by ``summarise_monthly_category``.
    top_merchants: DataFrame
        Top merchants summary.
    bad_habits: DataFrame
        Categories ranked by average monthly expense.
    """
    with pd.ExcelWriter(output_path, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        df.to_excel(writer, sheet_name="RawData", index=False)
        summary.to_excel(writer, sheet_name="CategorySummary", index=False)
        monthly.to_excel(writer, sheet_name="MonthlyCategory", index=False)
        top_merchants.to_excel(writer, sheet_name="TopMerchants", index=False)
        bad_habits.to_excel(writer, sheet_name="BadHabits", index=False)
        suggestions.to_excel(writer, sheet_name="Suggestions", index=False)
        # Comparative analyses (optional)
        if comparative_month is not None and not comparative_month.empty:
            comparative_month.to_excel(writer, sheet_name="CompareMonth", index=False)
        if comparative_quarter is not None and not comparative_quarter.empty:
            comparative_quarter.to_excel(writer, sheet_name="CompareQuarter", index=False)

        workbook = writer.book
        # Define a simple header style
        header_format = workbook.add_format({
            "bold": True,
            "bg_color": "#F7F7F7",
            "border": 1
        })
        # Mapping of sheet names to the DataFrame used to generate them
        sheet_data = {
            "RawData": df,
            "CategorySummary": summary,
            "MonthlyCategory": monthly,
            "TopMerchants": top_merchants,
            "BadHabits": bad_habits,
            "Suggestions": suggestions,
        }
        # Apply header styling and freeze panes
        for sheet_name, data in sheet_data.items():
            worksheet = writer.sheets[sheet_name]
            worksheet.freeze_panes(1, 0)
            for col_idx, value in enumerate(data.columns):
                worksheet.write(0, col_idx, value, header_format)

        # Chart: Bar chart for category expenses
        chart1 = workbook.add_chart({"type": "bar"})
        # The CategorySummary sheet has columns: Category, Expense, Income, Net
        cat_sheet = writer.sheets["CategorySummary"]
        cat_count = len(summary)
        # Add series for Expenses (col B) vs categories (col A)
        chart1.add_series({
            "name": "Expenses",
            "categories": ["CategorySummary", 1, 0, cat_count, 0],
            "values": ["CategorySummary", 1, 1, cat_count, 1],
        })
        chart1.set_title({"name": "Total Expenses by Category"})
        chart1.set_y_axis({"name": "Expense (absolute value)"})
        chart1.set_x_axis({"name": "Category"})
        chart1.set_legend({"position": "top"})
        # Insert chart into CategorySummary sheet
        cat_sheet.insert_chart("F2", chart1, {"x_scale": 1.2, "y_scale": 1.2})

        # Chart: Line chart for monthly spending of top categories
        # Identify the top 5 categories by total expense for charting
        top_categories = summary.sort_values("Expense", ascending=False)["Category"].head(5).tolist()
        # Pivot monthly table for the line chart
        pivot_monthly = monthly.pivot_table(index="YearMonth", columns="Category", values="Expense", fill_value=0)
        # Limit to top categories
        pivot_monthly = pivot_monthly[top_categories]
        # Write pivot_monthly to a hidden sheet for chart data
        pivot_sheet_name = "_PivotData"
        pivot_monthly.to_excel(writer, sheet_name=pivot_sheet_name)
        pivot_sheet = writer.sheets[pivot_sheet_name]
        # Create the line chart
        chart2 = workbook.add_chart({"type": "line"})
        # Determine number of rows and columns for the pivot table
        rows, cols = pivot_monthly.shape
        # Add a series for each category
        col_idx = 1  # first column in pivot table after index
        for i, category in enumerate(top_categories):
            chart2.add_series({
                "name":       [pivot_sheet_name, 0, col_idx],
                "categories": [pivot_sheet_name, 1, 0, rows, 0],
                "values":     [pivot_sheet_name, 1, col_idx, rows, col_idx],
            })
            col_idx += 1
        chart2.set_title({"name": "Monthly Expenses for Top Categories"})
        chart2.set_y_axis({"name": "Expense"})
        chart2.set_x_axis({"name": "Month"})
        chart2.set_legend({"position": "bottom"})
        # Insert line chart into MonthlyCategory sheet
        writer.sheets["MonthlyCategory"].insert_chart("H2", chart2, {"x_scale": 1.5, "y_scale": 1.5})

        # Chart: Bar chart for top merchants
        chart3 = workbook.add_chart({"type": "bar"})
        merchant_count = len(top_merchants)
        chart3.add_series({
            "name": "Total Spend",  # series name
            "categories": ["TopMerchants", 1, 0, merchant_count, 0],
            "values": ["TopMerchants", 1, 1, merchant_count, 1],
        })
        chart3.set_title({"name": "Top Merchants by Expense"})
        chart3.set_y_axis({"name": "Expense"})
        chart3.set_x_axis({"name": "Merchant"})
        chart3.set_legend({"position": "top"})
        writer.sheets["TopMerchants"].insert_chart("F2", chart3, {"x_scale": 1.2, "y_scale": 1.2})

        # Remove the pivot data sheet from the user view by hiding it
        pivot_sheet.hide()

        # Format the Suggestions sheet: wrap text and set column widths for readability
        if "Suggestions" in writer.sheets:
            suggestions_sheet = writer.sheets["Suggestions"]
            wrap_format = workbook.add_format({"text_wrap": True})
            # Freeze the header row
            suggestions_sheet.freeze_panes(1, 0)
            # Set width for Category and Suggestion columns
            suggestions_sheet.set_column(0, 0, 20)
            suggestions_sheet.set_column(1, 1, 80, wrap_format)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Analyse personal spending from CSV files and generate an Excel report.")
    parser.add_argument(
        "csv_files",
        nargs="+",
        type=Path,
        help="One or more CSV or PDF files containing transactions. PDF statements will be converted automatically.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("spending_report.xlsx"), help="Output Excel file path.")
    parser.add_argument(
        "--start-date", type=str, default=None, help="Optional start date (YYYY-MM-DD) to filter transactions.")
    parser.add_argument(
        "--end-date", type=str, default=None, help="Optional end date (YYYY-MM-DD) to filter transactions.")
    parser.add_argument(
        "--categories", type=Path, default=None, help="Path to a JSON file containing keyword to category mapping.")
    parser.add_argument(
        "--top-merchants", type=int, default=10, help="Number of top merchants to display.")
    parser.add_argument(
        "--top-bad-habits", type=int, default=5, help="Number of categories to highlight as potential bad habits.")

    parser.add_argument(
        "--regex-categories",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing regular expression patterns to categories. "
            "Each key should be a regex and the value the desired category. Patterns "
            "are checked before keyword mappings."
        ),
    )

    args = parser.parse_args(argv)

    # Load all transactions
    df = load_transactions(args.csv_files)

    # Apply date filters if provided
    if args.start_date:
        start = pd.to_datetime(args.start_date)
        df = df[df["Date"] >= start]
    if args.end_date:
        end = pd.to_datetime(args.end_date)
        df = df[df["Date"] <= end]

    # Load category mapping and optional regex patterns
    mapping = load_categories(args.categories)
    regex_mapping = load_regex_patterns(args.regex_categories)
    # Categorise using regex and keyword mappings
    df["Category"] = df["Description"].apply(lambda x: categorise_row(x, mapping, regex_mapping))
    # Use ML to predict categories for uncategorised rows
    df = ml_categorise_uncategorised(df)

    # Summaries
    summary = summarise_by_category(df)
    monthly = summarise_monthly_category(df)
    top_merch = summarise_top_merchants(df, n=args.top_merchants)
    bad_habits = detect_bad_habits(df, top_n=args.top_bad_habits)
    # Comparative period analysis (monthly and quarterly)
    comp_monthly = summarise_comparative_periods(df, freq="M")
    comp_quarterly = summarise_comparative_periods(df, freq="Q")

    # Generate AI suggestions based on bad habits
    suggestions = generate_ai_suggestions(bad_habits)
    # Write report including suggestions
    # Write report including comparative analysis
    write_excel_report(
        df,
        args.output,
        summary,
        monthly,
        top_merch,
        bad_habits,
        suggestions,
        comparative_month=comp_monthly,
        comparative_quarter=comp_quarterly,
    )
    print(f"Report written to {args.output.resolve()}")


if __name__ == "__main__":
    main()