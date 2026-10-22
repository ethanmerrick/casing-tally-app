"""
Casing tally calculator - Streamlit web page.

Run locally:   streamlit run app.py
The calculation lives in casing_tally.py; this file only collects inputs and
shows results.
"""

import hmac
import re

import pandas as pd
import streamlit as st

import casing_tally as ct
from exporters import to_excel_bytes
from table_inputs import read_lengths_from_upload, rules_from_table

st.set_page_config(page_title="Casing tally calculator", page_icon="🛢️", layout="wide")

D = ct.DEFAULT_CONFIG


# -----------------------------------------------------------------------------
# Access control
# -----------------------------------------------------------------------------
def check_access():
    """
    Returns True if this visitor may use the calculator.

    Today the app is open to everyone, unless you set an APP_PASSWORD secret
    (Streamlit Cloud: app settings -> Secrets), in which case visitors must enter it.
    When you add a paywall, replace the body of this function with your login and
    subscription check (see README.md). Nothing else in the app needs to change.
    """
    try:
        required = st.secrets.get("APP_PASSWORD")
    except Exception:           # no secrets configured
        required = None
    if not required:
        return True
    if st.session_state.get("authenticated"):
        return True

    st.info("Enter the access password to use the calculator.")
    entered = st.text_input("Password", type="password")
    if entered:
        if hmac.compare_digest(entered.encode(), str(required).encode()):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("That password is not correct.")
    return False


# -----------------------------------------------------------------------------
# Page
# -----------------------------------------------------------------------------
st.title("Casing tally calculator")
st.caption("Builds the two closest casing tallies to your target - one slightly shorter, "
           "one slightly longer - with centralisers, depths and rathole.")

if not check_access():
    st.stop()

# ---- sidebar: single-value inputs -------------------------------------------
with st.sidebar:
    st.header("Well")
    units = st.selectbox("Units", ["m", "ft"], help="Labels only - enter all lengths in the same unit.")
    target_depth = st.number_input(f"Prognosed drilled depth ({units})", min_value=0.0,
                                   value=D["target_depth"], step=1.0, format="%.2f",
                                   help="The depth you expect to drill to, not the shoe depth.")
    rathole = st.number_input(f"Rathole ({units})", min_value=0.0, value=D["rathole"],
                              step=0.5, format="%.2f",
                              help="Shoe depth = drilled depth - rathole.")
    stickup = st.number_input(f"Stick-up above datum ({units})", min_value=0.0,
                              value=D["stickup"], step=0.1, format="%.2f",
                              help="Length of string above the depth datum (e.g. rotary table) when landed.")

    st.header("Fixed items")
    shoe_length = st.number_input(f"Shoe length ({units})", min_value=0.0,
                                  value=D["shoe_length"], step=0.05, format="%.2f")
    float_length = st.number_input(f"Float joint length ({units})", min_value=0.0,
                                   value=D["float_joint_length"], step=0.05, format="%.2f")
    pup_length = st.number_input(f"Pup joint length ({units})", min_value=0.0,
                                 value=D["pup_length"], step=0.05, format="%.2f",
                                 help="Set to 0 for no pup joint.")
    pup_above = st.number_input("Pup joint sits above tally no.", min_value=1,
                                value=D["pup_above_number"], step=1,
                                help="The pup takes the next number; joints above it shift up by one.")
    number_shoe = st.checkbox("Number the shoe as joint 1", value=D["number_the_shoe"],
                              help="Off: the shoe is unnumbered and the shoe-track joint is 1.")

    st.header("Couplings")
    coupling_length = st.number_input(f"Coupling length ({units})", min_value=0.0,
                                      value=D["coupling_length"], step=0.01, format="%.2f",
                                      help="Used when a centraliser position is 'coupling'.")
    coupling_end = st.selectbox("Coupling is at the", ["top", "bottom"],
                                help="Top: joints run pin-down / coupling-up.")

    st.header("Joint selection")
    max_skips = st.slider("Most joints that may be skipped", 0, 6, D["max_skipped_joints"],
                          help="0 runs joints strictly in order. A higher number lets the tool "
                               "leave a few joints on the rack to land closer to the target.")
    tolerance = st.number_input(f"Depth tolerance ({units})", min_value=0.0,
                                value=D["depth_tolerance"], step=0.1, format="%.2f",
                                disabled=(max_skips == 0),
                                help="Only used when skipping is allowed.")

# ---- joint pool and centraliser rules ---------------------------------------
st.session_state.setdefault("pool_df", pd.DataFrame({"Length": D["available_joints"]}))
st.session_state.setdefault("pool_ver", 0)

left, right = st.columns([1, 2], gap="large")

with left:
    st.subheader("Joint pool")
    st.caption("Joints in the order they will be run. Row 1 is the shoe-track joint. "
               "You can paste a column of lengths from Excel.")
    pool_df = st.data_editor(
        st.session_state["pool_df"],
        key=f"pool_editor_{st.session_state['pool_ver']}",
        num_rows="dynamic", hide_index=True, height=360,
        column_config={"Length": st.column_config.NumberColumn(
            f"Length ({units})", min_value=0.01, format="%.2f", required=True)},
    )
    with st.expander("Load joint lengths from a file"):
        uploaded = st.file_uploader("CSV or Excel file", type=["csv", "xlsx"],
                                    label_visibility="collapsed")
        if uploaded is not None and st.button("Replace joint pool with this file"):
            try:
                lengths = read_lengths_from_upload(uploaded)
                if not lengths:
                    st.error("No joint lengths found. Put the lengths in one column.")
                else:
                    st.session_state["pool_df"] = pd.DataFrame({"Length": lengths})
                    st.session_state["pool_ver"] += 1
                    st.rerun()
            except Exception as exc:
                st.error(f"Could not read that file: {exc}")
    st.caption("The sample joint lengths above are placeholder data - replace them with your own.")

with right:
    st.subheader("Centraliser rules")
    st.caption("One row per rule. Position is centre, coupling, or a fraction of the way up the "
               "joint (0.25). Every 2 with start 1 puts one on joints 1, 3, 5 ...")
    default_rules = pd.DataFrame(
        [{"on": r["on"], "type": r["type"], "every": r.get("every", 1),
          "start": r.get("start", 1), "per_joint": r.get("per_joint", 1),
          "position": str(r.get("position", "centre")),
          "from_no": None, "to_no": None} for r in D["centraliser_design"]])
    rules_df = st.data_editor(
        default_rules, key="rules_editor", num_rows="dynamic", hide_index=True,
        column_config={
            "on": st.column_config.SelectboxColumn("On", options=list(ct.CENTRALISER_KINDS),
                                                   required=True, help="Which joints"),
            "type": st.column_config.TextColumn("Type"),
            "every": st.column_config.NumberColumn("Every", min_value=1, step=1,
                                                   help="1 = every joint, 2 = every second joint"),
            "start": st.column_config.NumberColumn("Start", min_value=1, step=1,
                                                   help="Which joint of that group gets the first one"),
            "per_joint": st.column_config.NumberColumn("Per joint", min_value=1, step=1),
            "position": st.column_config.TextColumn("Position",
                                                    help="centre, coupling, or 0-1"),
            "from_no": st.column_config.NumberColumn("From no.", min_value=1, step=1,
                                                     help="Optional: first tally number"),
            "to_no": st.column_config.NumberColumn("To no.", min_value=1, step=1,
                                                   help="Optional: last tally number"),
        },
    )

# ---- build the config and run -------------------------------------------------
try:
    cfg = {
        "units": units,
        "target_depth": target_depth,
        "rathole": rathole,
        "stickup": stickup,
        "max_skipped_joints": max_skips,
        "depth_tolerance": tolerance,
        "shoe_length": shoe_length,
        "float_joint_length": float_length,
        "pup_length": pup_length,
        "pup_above_number": int(pup_above),
        "number_the_shoe": number_shoe,
        "coupling_length": coupling_length,
        "coupling_end": coupling_end,
        "centraliser_design": rules_from_table(rules_df),
        "available_joints": [float(x) for x in pool_df["Length"].dropna().tolist()],
    }
    designs = ct.build_designs(cfg)
except ValueError as exc:
    st.divider()
    st.error(str(exc))
    st.stop()

# ---- results --------------------------------------------------------------------
st.divider()
st.header("Results")

st.download_button(
    "Download Excel workbook",
    data=to_excel_bytes(cfg, designs),
    file_name="casing_tally.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    help="Inputs, summary, tally and centraliser schedule for each design.",
)
st.caption("To print or save a PDF, use your browser's Print command.")

NUM = lambda label: st.column_config.NumberColumn(label, format="%.2f")


def show_design(items, info):
    if items is None:
        for w in info["warnings"]:
            st.error(w)
        return

    u = info["units"]
    cols = st.columns(5)
    cols[0].metric("Shoe depth", f"{info['shoe_depth']:.2f} {u}",
                   f"{info['shoe_error']:+.2f} {u} vs target", delta_color="off")
    cols[1].metric("Drilled depth needed", f"{info['required_td']:.2f} {u}",
                   help="Shoe depth + rathole")
    cols[2].metric("Rathole at prognosed depth", f"{info['actual_rathole']:.2f} {u}")
    cols[3].metric("String length", f"{info['total_length']:.2f} {u}")
    cols[4].metric("Centralisers", len(info["centralisers"]))

    for w in info["warnings"]:
        st.warning(w)

    st.markdown("**Tally** - numbered from the bottom of the well upward")
    st.dataframe(
        pd.DataFrame(ct.tally_table_rows(items)), hide_index=True,
        column_config={
            "No.": st.column_config.NumberColumn("No.", format="%d"),
            "Pool #": st.column_config.NumberColumn("Pool #", format="%d"),
            "Length": NUM("Length"), "Cum. length": NUM("Cum. length"),
            "Top depth": NUM("Top depth"), "Bottom depth": NUM("Bottom depth"),
        },
    )

    if info["has_centraliser_rules"]:
        st.markdown("**Centraliser schedule** - numbered from the bottom upward")
        if info["centralisers"]:
            st.dataframe(
                pd.DataFrame(ct.centraliser_table_rows(info)), hide_index=True,
                column_config={
                    "No.": st.column_config.NumberColumn("No.", format="%d"),
                    "On joint": st.column_config.NumberColumn("On joint", format="%d"),
                    "Up the joint": NUM("Up the joint"),
                    "Depth": NUM("Depth"), "Above shoe": NUM("Above shoe"),
                },
            )
            totals = ", ".join(f"{n} x {t}" for t, n in info["cent_totals"].items())
            st.caption(f"Total: {len(info['centralisers'])} ({totals})")
        else:
            st.info("No centralisers were placed.")

    if info["skipped"]:
        st.caption(f"Skipped pool joints (left on the rack): {info['skipped']}")
    if info["unused"]:
        st.caption(f"Unused pool joints: {info['unused'][0]} to {info['unused'][-1]} "
                   f"({len(info['unused'])} joints)")


if len(designs) == 1:
    st.success(designs[0][0])
    show_design(designs[0][1], designs[0][2])
else:
    tab_a, tab_b = st.tabs(["Design A - shorter", "Design B - longer"])
    with tab_a:
        show_design(designs[0][1], designs[0][2])
    with tab_b:
        show_design(designs[1][1], designs[1][2])

st.divider()
st.caption("This tool is a planning aid. Check every tally against your own calculations "
           "and the actual pipe tally before running casing.")
