"""
Streamlit UI for the personal finance analysis tool.

This application wraps the core functionality of ``spending_analysis.py``
into an easy‑to‑use web interface.  Users can upload one or more CSV
files exported from their bank(s) or credit‑card provider(s), set
optional filters, supply a custom category mapping, and generate
interactive tables, charts and downloadable reports.  Suggestions for
reducing spending are displayed alongside the summary results.

To run the app locally, first install the required dependencies:

::

    pip install streamlit pandas xlsxwriter

Then start the application with:

::

    streamlit run streamlit_app.py

The UI will open in your default browser at ``http://localhost:8501``.
"""

import io
import json
from datetime import date
from pathlib import Path
from typing import List


import pandas as pd
import streamlit as st
import re
import io
try:
    import altair as alt
    _ALT_AVAILABLE = True
except ImportError:
    _ALT_AVAILABLE = False

import spending_analysis as sa
import tempfile


def main() -> None:
    st.set_page_config(page_title="Personal Finance Analysis", layout="wide")
    st.title("Personal Finance Analysis Tool")
    st.write("Upload your bank or credit‑card statements (CSV) below, choose optional settings, and click **Analyze** to generate summaries and suggestions.")

    # File uploader for one or more CSV files
    uploaded_files = st.file_uploader(
        "CSV or PDF files",
        type=["csv", "pdf"],
        accept_multiple_files=True,
        help="Export your statements as CSV files from your bank or upload PDF statements. PDF files will be converted to CSV automatically.",
    )

    # Optional date filters
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start date", value=None)
    with col2:
        end_date = st.date_input("End date", value=None)

    # Optional category mapping file
    categories_file = st.file_uploader(
        "Categories JSON (optional)", type=["json"], help="Upload a JSON file mapping keywords to categories, or leave blank to use defaults."
    )

    # Parameters for analysis
    top_merchants = st.number_input("Number of top merchants", min_value=1, max_value=50, value=10, step=1)
    top_bad_habits = st.number_input("Number of categories to highlight as bad habits", min_value=1, max_value=20, value=5, step=1)
    # Check if scikit‑learn is available for ML categorisation
    has_ml = getattr(sa, "_SKLEARN_AVAILABLE", False)
    use_ml = st.checkbox(
        "Use machine learning to categorise uncategorised transactions",
        value=has_ml,
        disabled=not has_ml,
        help="When enabled (and scikit‑learn is installed), a simple text classifier will try to assign categories to transactions that weren't matched by keywords."
    )
    if not has_ml:
        st.info("Machine learning category prediction is unavailable because scikit‑learn is not installed.")

    analyze_button = st.button("Analyze")

    if analyze_button:
        if not uploaded_files:
            st.error("Please upload at least one CSV file.")
            return

        # Read and preprocess uploaded files.  Support both CSV and PDF
        # uploads.  PDF statements are converted to CSV using the
        # ``convert_pdf_to_csv`` function from ``spending_analysis``.
        data_frames: List[pd.DataFrame] = []
        for file in uploaded_files:
            # Determine file type by extension
            filename = file.name
            ext = Path(filename).suffix.lower()
            if ext == ".pdf":
                # Save the uploaded PDF to a temporary file
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
                    tmp_pdf.write(file.getbuffer())
                    tmp_pdf_path = Path(tmp_pdf.name)
                # Create a temporary file for the CSV output
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp_csv:
                    tmp_csv_path = Path(tmp_csv.name)
                try:
                    # Convert PDF to CSV
                    try:
                        sa.convert_pdf_to_csv(tmp_pdf_path, tmp_csv_path)
                    except Exception as e:
                        st.error(f"Error converting PDF {filename}: {e}")
                        return
                    # Read the resulting CSV
                    try:
                        df_raw = pd.read_csv(tmp_csv_path)
                    except Exception as e:
                        st.error(f"Error reading converted CSV from {filename}: {e}")
                        return
                finally:
                    # Clean up temporary files
                    try:
                        tmp_pdf_path.unlink()
                    except Exception:
                        pass
                    try:
                        tmp_csv_path.unlink()
                    except Exception:
                        pass
            else:
                # Assume CSV; perform a fallback if parsing fails
                try:
                    df_raw = pd.read_csv(file)
                except Exception as e:
                    # Fallback for messy CSVs: locate the header row and parse
                    try:
                        import io
                        # Read entire file as string
                        file.seek(0)
                        text = file.getvalue().decode('utf-8', errors='ignore')
                        content = text.splitlines()
                        # Find header row containing Date and Amount columns
                        header_idx = None
                        for i, line in enumerate(content):
                            if re.search(r"^\s*Date\s*,\s*.*Amount.*", line, re.IGNORECASE):
                                header_idx = i
                                break
                        if header_idx is None:
                            raise Exception('No header row found')
                        csv_part = "\n".join(content[header_idx:])
                        # Manually parse transactions to allow commas in descriptions.
                        lines = csv_part.split("\n")
                        # Extract year from metadata lines above the header
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
                            date_raw = parts[0].strip()
                            amount_str = parts[1].strip()
                            desc = ",".join(parts[2:]).strip()
                            if statement_year is not None and re.match(r"^\d{1,2}/\d{1,2}$", date_raw):
                                date_final = f"{date_raw}/{statement_year}"
                            else:
                                date_final = date_raw
                            try:
                                amount_val = float(amount_str)
                            except ValueError:
                                continue
                            transactions.append([date_final, desc, amount_val])
                        if transactions:
                            df_raw = pd.DataFrame(transactions, columns=["Date", "Description", "Amount"])
                        else:
                            df_raw = pd.read_csv(io.StringIO(csv_part))
                    except Exception:
                        st.error(f"Error reading {filename}: {e}")
                        return
            # Preprocess the DataFrame (detect columns, unify amount, parse dates)
            try:
                df_proc = sa.preprocess_dataframe(df_raw)
            except Exception as e:
                st.error(f"Error processing {filename}: {e}")
                return
            data_frames.append(df_proc)
        df = pd.concat(data_frames, ignore_index=True).sort_values("Date")

        # Apply date filters
        if start_date:
            df = df[df["Date"] >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df["Date"] <= pd.to_datetime(end_date)]

        # Load category mapping
        if categories_file is not None:
            try:
                mapping_dict = json.load(categories_file)
                mapping = {k.lower(): v for k, v in mapping_dict.items()}
            except Exception as e:
                st.error(f"Error reading categories JSON: {e}")
                return
        else:
            mapping = sa.load_categories(None)

        # Categorise
        df["Category"] = df["Description"].apply(lambda x: sa.categorise_row(x, mapping))
        # Optionally use machine learning to categorise uncategorised rows
        if use_ml:
            df = sa.ml_categorise_uncategorised(df)

        # Compute summaries
        summary = sa.summarise_by_category(df)
        monthly = sa.summarise_monthly_category(df)
        top_merch = sa.summarise_top_merchants(df, n=int(top_merchants))
        bad_habits = sa.detect_bad_habits(df, top_n=int(top_bad_habits))
        suggestions = sa.generate_ai_suggestions(bad_habits)

        # Allow the user to edit categories interactively
        st.subheader("Edit Categories (optional)")
        st.write("You can correct the automatically assigned categories before generating the tables. Changes will update the summaries.")
        edited_df = st.data_editor(
            df[["Date", "Description", "Amount", "Category"]],
            hide_index=True,
            num_rows="dynamic",
            key="edit_categories",
        )
        if st.button("Apply category edits"):
            df["Category"] = edited_df["Category"]
            # Recompute summaries after edits
            summary = sa.summarise_by_category(df)
            monthly = sa.summarise_monthly_category(df)
            top_merch = sa.summarise_top_merchants(df, n=int(top_merchants))
            bad_habits = sa.detect_bad_habits(df, top_n=int(top_bad_habits))
            suggestions = sa.generate_ai_suggestions(bad_habits)

        # Budget inputs
        st.subheader("Monthly Budgets (optional)")
        budgets = {}
        if summary.empty:
            st.info("No categories to budget.")
        else:
            for cat in summary["Category"].tolist():
                budgets[cat] = st.number_input(
                    f"Budget for {cat}",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                )

        # Budget alerts if budgets are set (non‑zero)
        budget_alerts = None
        if any(budgets.values()):
            alerts = []
            for cat, budget in budgets.items():
                if budget > 0:
                    # Compute average monthly expense for this category
                    avg_exp = monthly[monthly["Category"] == cat]["Expense"].mean()
                    if pd.isna(avg_exp):
                        continue
                    overspend = avg_exp - budget
                    alerts.append({
                        "Category": cat,
                        "Budget": budget,
                        "AvgMonthlyExpense": avg_exp,
                        "Overspend": overspend,
                    })
            budget_alerts = pd.DataFrame(alerts)
            if not budget_alerts.empty:
                budget_alerts = budget_alerts.sort_values("Overspend", ascending=False)

        # Trend detection: compute average month‑to‑month change for each category
        trend_records = []
        for cat in summary["Category"].tolist():
            series = monthly[monthly["Category"] == cat].set_index("YearMonth")["Expense"].sort_index()
            # Compute percentage change between months
            pct_change = series.pct_change().dropna()
            if len(pct_change) > 0:
                avg_change = pct_change.mean()
                trend_records.append({"Category": cat, "AvgMonthlyChange": avg_change})
        trends_df = pd.DataFrame(trend_records).sort_values("AvgMonthlyChange", ascending=False)

        # Display results in tabs
        tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
            "Raw Data",
            "Category Summary",
            "Monthly Spending",
            "Top Merchants",
            "Bad Habits",
            "Suggestions",
            "Budgets & Trends",
        ])

        with tab1:
            st.subheader("Raw Transactions")
            st.dataframe(df, use_container_width=True)
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download raw data as CSV",
                data=csv_bytes,
                file_name="raw_transactions.csv",
                mime="text/csv",
            )

        with tab2:
            st.subheader("Summary by Category")
            st.dataframe(summary, use_container_width=True)
            # Plot bar chart using Altair if available
            if _ALT_AVAILABLE and not summary.empty:
                bar = (
                    alt.Chart(summary)
                    .mark_bar()
                    .encode(
                        x=alt.X("Expense", title="Total Expense"),
                        y=alt.Y("Category", sort="-x"),
                        tooltip=["Category", "Expense", "Income", "Net"],
                    )
                )
                st.altair_chart(bar, use_container_width=True)
            else:
                st.bar_chart(summary.set_index("Category")["Expense"])
            # Download summary
            st.download_button(
                label="Download summary as CSV",
                data=summary.to_csv(index=False).encode("utf-8"),
                file_name="summary_by_category.csv",
                mime="text/csv",
            )

        with tab3:
            st.subheader("Monthly Spending by Category")
            st.dataframe(monthly, use_container_width=True)
            # Plot top categories monthly line chart
            top_cats = summary.sort_values("Expense", ascending=False)["Category"].head(5).tolist()
            pivot_mon = monthly.pivot_table(index="YearMonth", columns="Category", values="Expense", fill_value=0)
            if not pivot_mon.empty:
                if _ALT_AVAILABLE:
                    # Prepare data for Altair: unpivot to long format
                    chart_data = pivot_mon[top_cats].reset_index().melt(id_vars="YearMonth", var_name="Category", value_name="Expense")
                    line = (
                        alt.Chart(chart_data)
                        .mark_line()
                        .encode(
                            x="YearMonth", y="Expense", color="Category",
                            tooltip=["YearMonth", "Category", "Expense"],
                        )
                    )
                    st.altair_chart(line, use_container_width=True)
                else:
                    st.line_chart(pivot_mon[top_cats])
            st.download_button(
                label="Download monthly summary as CSV",
                data=monthly.to_csv(index=False).encode("utf-8"),
                file_name="monthly_spending.csv",
                mime="text/csv",
            )

        with tab4:
            st.subheader("Top Merchants")
            st.dataframe(top_merch, use_container_width=True)
            if _ALT_AVAILABLE and not top_merch.empty:
                bar = (
                    alt.Chart(top_merch)
                    .mark_bar()
                    .encode(
                        x=alt.X("TotalExpense", title="Total Expense"),
                        y=alt.Y("Merchant", sort="-x"),
                        tooltip=["Merchant", "TotalExpense", "Frequency"],
                    )
                )
                st.altair_chart(bar, use_container_width=True)
            else:
                st.bar_chart(top_merch.set_index("Merchant")["TotalExpense"])
            st.download_button(
                label="Download top merchants as CSV",
                data=top_merch.to_csv(index=False).encode("utf-8"),
                file_name="top_merchants.csv",
                mime="text/csv",
            )

        with tab5:
            st.subheader("Bad Habits (Highest Avg Monthly Spend)")
            st.dataframe(bad_habits, use_container_width=True)
            if _ALT_AVAILABLE and not bad_habits.empty:
                bar = (
                    alt.Chart(bad_habits)
                    .mark_bar()
                    .encode(
                        x=alt.X("AvgMonthlyExpense", title="Average Monthly Expense"),
                        y=alt.Y("Category", sort="-x"),
                        tooltip=["Category", "AvgMonthlyExpense"],
                    )
                )
                st.altair_chart(bar, use_container_width=True)
            else:
                st.bar_chart(bad_habits.set_index("Category")["AvgMonthlyExpense"])
            st.download_button(
                label="Download bad habits as CSV",
                data=bad_habits.to_csv(index=False).encode("utf-8"),
                file_name="bad_habits.csv",
                mime="text/csv",
            )

        with tab6:
            st.subheader("Suggestions for Reducing Spend")
            st.dataframe(suggestions, use_container_width=True)
            st.download_button(
                label="Download suggestions as CSV",
                data=suggestions.to_csv(index=False).encode("utf-8"),
                file_name="suggestions.csv",
                mime="text/csv",
            )

        with tab7:
            st.subheader("Budgets & Trends")
            if budget_alerts is not None and not budget_alerts.empty:
                st.write("### Budget Alerts")
                st.dataframe(budget_alerts, use_container_width=True)
            else:
                st.info("No budget alerts. You may not have entered any budgets or spending is within limits.")
            if not trends_df.empty:
                st.write("### Categories with the highest average month‑to‑month change")
                st.dataframe(trends_df, use_container_width=True)
            else:
                st.info("Not enough monthly data to compute trends.")

        # Provide Excel download
        if st.button("Download full report (Excel)"):
            output = io.BytesIO()
            # Use the same report writer from spending_analysis
            sa.write_excel_report(df, Path("report.xlsx"), summary, monthly, top_merch, bad_habits, suggestions)
            # Read the generated file and send to BytesIO
            with open("report.xlsx", "rb") as f:
                output.write(f.read())
            output.seek(0)
            st.download_button(
                label="Click to download report.xlsx",
                data=output,
                file_name="financial_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


if __name__ == "__main__":
    main()