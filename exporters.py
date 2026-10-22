"""Excel export for the casing tally results."""

from io import BytesIO

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import casing_tally as ct


def _sheet_name(prefix, kind):
    return f"{prefix} {kind}"[:31]


def _style_sheet(ws):
    header_fill = PatternFill("solid", fgColor="0F5C6E")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col_cells in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = min(max(width + 2, 9), 40)
        for c in col_cells[1:]:
            if isinstance(c.value, float):
                c.number_format = "0.00"
    ws.freeze_panes = "A2"


def to_excel_bytes(cfg, designs):
    """
    Returns an .xlsx file (as bytes) with an Inputs sheet plus, for each design,
    a tally sheet and a centraliser schedule sheet.
    `designs` is the list returned by casing_tally.build_designs().
    """
    cfg = {**ct.DEFAULT_CONFIG, **cfg}
    u = cfg["units"]

    inputs = [
        ("Prognosed drilled depth", cfg["target_depth"], u),
        ("Rathole", cfg["rathole"], u),
        ("Target shoe depth", cfg["target_depth"] - cfg["rathole"], u),
        ("Stick-up", cfg["stickup"], u),
        ("Shoe length", cfg["shoe_length"], u),
        ("Float joint length", cfg["float_joint_length"], u),
        ("Pup joint length", cfg["pup_length"], u),
        ("Pup sits above tally no.", cfg["pup_above_number"], ""),
        ("Coupling length", cfg["coupling_length"], u),
        ("Coupling end", cfg["coupling_end"], ""),
        ("Max skipped joints", cfg["max_skipped_joints"], ""),
        ("Joints in pool", len(cfg["available_joints"]), ""),
    ]
    inputs_df = pd.DataFrame(inputs, columns=["Input", "Value", "Units"])

    summary_rows = []
    for title, items, info in designs:
        if items is None:
            continue
        summary_rows += [
            (title, "", ""),
            ("Shoe depth", info["shoe_depth"], u),
            ("Difference from target shoe depth", info["shoe_error"], u),
            ("Drilled depth this design needs", info["required_td"], u),
            ("Rathole if drilled to prognosed depth", info["actual_rathole"], u),
            ("String length", info["total_length"], u),
            ("Centralisers", len(info["centralisers"]), ""),
            ("", "", ""),
        ]
    summary_df = pd.DataFrame(summary_rows, columns=["Result", "Value", "Units"])

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        inputs_df.to_excel(writer, sheet_name="Inputs", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

        for n, (title, items, info) in enumerate(designs):
            if items is None:
                continue
            prefix = "Exact match" if len(designs) == 1 else f"Design {'AB'[n]}"
            pd.DataFrame(ct.tally_table_rows(items)).to_excel(
                writer, sheet_name=_sheet_name(prefix, "tally"), index=False)
            if info["centralisers"]:
                pd.DataFrame(ct.centraliser_table_rows(info)).to_excel(
                    writer, sheet_name=_sheet_name(prefix, "centralisers"), index=False)

        for ws in writer.book.worksheets:
            _style_sheet(ws)

    return buffer.getvalue()
