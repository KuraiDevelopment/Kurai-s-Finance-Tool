# Finance Tool Project Summary

This document summarizes the key milestones and capabilities developed so far for the personal finance tool.

## 1. Core Data Ingestion

- **CSV ingestion:** The tool accepts one or more CSV files downloaded from banking or credit‑card websites. Each file can contain up to two years of transactions. A modified parser scans for the header row (e.g., `Date,Amount,Description`), ignores metadata above it, and splits each transaction line at the first two commas. This handles messy exports where addresses or notes include commas. The parser adds the year to dates that lack a year.
- **PDF ingestion:** When a PDF is provided, the script first tries to extract text using Poppler’s `pdftotext`. If the PDF has an embedded text layer (common in digital statements), the extraction succeeds automatically. Otherwise, it falls back to an OCR pipeline using `pdftoppm` and Tesseract (provided the user installs both). Tesseract is an open‑source OCR engine originally developed by Hewlett‑Packard and later sponsored by Google:contentReference[oaicite:0]{index=0}. Poppler’s Windows package can be installed via `winget` (`winget install --id=oschwartz10612.Poppler -e`):contentReference[oaicite:1]{index=1}. If OCR isn’t possible, the user is instructed to request a text‑based statement or use third‑party OCR software.

## 2. Transaction Processing and Analysis

- **Date filtering:** Users can restrict analysis to any timeframe within a two‑year window via command‑line arguments or interactive UI controls.
- **Categorization:** Transactions are initially labelled with categories based on keywords (e.g., `Grocery`, `Rent`, `Entertainment`). A machine‑learning classifier using scikit‑learn’s `TfidfVectorizer` and `MultinomialNB` is trained on already‑categorised transactions. It predicts categories for uncategorised items. If scikit‑learn is not installed, the tool falls back to manual categorisation.
- **Summaries and trends:** The script aggregates spending by category and month, identifies top merchants by total spend and frequency, and calculates “bad habit” categories (highest average monthly spending). Users can specify how many top merchants or categories to display.

## 3. AI‑driven Suggestions

- The tool generates tailored advice for high‑spending categories. For example, if “Groceries” is a major expense, it suggests meal planning or shopping at discount grocers and cites local options in Youngstown such as Aldi on Belmont Ave, Sparkle Market on South Ave and Save‑A‑Lot on Gypsy Ln:contentReference[oaicite:2]{index=2}:contentReference[oaicite:3]{index=3}:contentReference[oaicite:4]{index=4}. Similar advice is provided for other categories (e.g., dining out, entertainment, transportation).

## 4. User Interfaces

- **Command‑line script:** Users can run `python spending_analysis.py <files> --output report.xlsx` with optional arguments for date ranges, category mapping JSON, number of top merchants and categories. The script writes an Excel report with worksheets for raw data, category summary, monthly spending, top merchants, bad habits and suggestions.
- **Streamlit web app:** An interactive UI built with Streamlit allows users to upload CSV or PDF files, choose date filters, upload custom categories, set monthly budgets and detect trends. It features:
  - Editable categories: users can recategorise transactions and update the summaries instantly.
  - Budget alerts: users can enter monthly budget targets per category; the app highlights overspending.
  - Trend detection: it lists categories with the largest month‑to‑month increases.
  - AI suggestions tab: summarises cost‑saving ideas based on the user’s spending patterns.
  - Download buttons to export the cleaned data or full Excel report.

## 5. Robustness and Fallback Logic

- If scikit‑learn is unavailable, the tool disables ML categorisation gracefully and notifies the user.
- If the parser cannot detect date or amount columns, it raises a clear error and suggests cleaning the input or renaming columns.
- For scanned PDFs, the tool instructs users to install Tesseract OCR and Poppler or obtain a text‑based statement.

## 6. Dependencies and Installation

To run the command‑line tool or web app, install the following Python packages:

```bash
pip install pandas xlsxwriter streamlit altair scikit-learn
