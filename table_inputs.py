"""Helpers that turn the web page's tables and uploads into calculator inputs."""

import pandas as pd


def _blank(value):
    return value is None or (isinstance(value, float) and pd.isna(value))


def parse_position(text):
    text = "" if text is None or (isinstance(text, float) and pd.isna(text)) else str(text).strip()
    if text.lower() in ("", "centre", "center"):
        return "centre"
    if text.lower() == "coupling":
        return "coupling"
    try:
        return float(text)
    except ValueError:
        raise ValueError(f'Position "{text}" is not valid. Use centre, coupling, '
                         f'or a number between 0 and 1.')


def rules_from_table(df):
    """Turns the centraliser rules table into the list the calculator expects."""
    rules = []
    for _, row in df.iterrows():
        if _blank(row.get("on")):
            continue
        rule = {
            "on": row["on"],
            "type": "Centraliser" if _blank(row.get("type")) or not str(row["type"]).strip()
                    else str(row["type"]).strip(),
            "every": 1 if _blank(row.get("every")) else int(row["every"]),
            "start": 1 if _blank(row.get("start")) else int(row["start"]),
            "per_joint": 1 if _blank(row.get("per_joint")) else int(row["per_joint"]),
            "position": parse_position(row.get("position")),
        }
        if not _blank(row.get("from_no")):
            rule["from_no"] = int(row["from_no"])
        if not _blank(row.get("to_no")):
            rule["to_no"] = int(row["to_no"])
        rules.append(rule)
    return rules


def read_lengths_from_upload(uploaded):
    """Finds the column with the most numbers in a CSV/Excel file and returns them."""
    if uploaded.name.lower().endswith(".csv"):
        raw = pd.read_csv(uploaded, header=None)
    else:
        raw = pd.read_excel(uploaded, header=None)
    best = []
    for col in raw.columns:
        nums = pd.to_numeric(raw[col], errors="coerce").dropna()
        if len(nums) > len(best):
            best = nums.tolist()
    return [float(x) for x in best if x > 0]
