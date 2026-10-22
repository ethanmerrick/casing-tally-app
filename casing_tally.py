"""
Casing tally calculator - calculation module
============================================
Builds TWO casing tallies for a prognosed drilled depth:
    Design A - the closest design that is slightly SHORTER than the target
    Design B - the closest design that is slightly LONGER than the target

String layout (bottom -> up):
    casing shoe -> casing joint (pool joint 1) -> float joint -> casing joints ... -> top
A pup joint is inserted at a set position in the string. Centralisers are
placed from a list of rules (see DEFAULT_CONFIG below).

Joints are numbered 1, 2, 3 ... from the BOTTOM of the well going UP and are
taken from "available_joints" in the order listed (ascending order).

Target shoe depth = drilled depth - rathole.

This module has no web code. The Streamlit page (app.py) calls build_designs().
You can also run this file directly to print the tallies for DEFAULT_CONFIG:
    python casing_tally.py
Use effective (make-up) lengths for every joint.
"""

SCALE = 100                      # lengths are handled to 0.01 (don't change)
MAX_SEARCH_BYTES = 300_000_000   # safety limit for the joint search's memory use
CENTRALISER_KINDS = ("shoe_track", "float", "casing", "pup")

# =============================================================================
# DEFAULT INPUTS (sample data - replace with your own)
# =============================================================================
DEFAULT_CONFIG = {
    "units": "m",

    "target_depth": 450.00,      # prognosed DRILLED-TO depth (not the shoe depth)
    "rathole": 8.00,             # shoe depth = drilled depth - rathole
    "stickup": 0.00,             # length of string ABOVE the depth datum when landed

    # 0 = joints run strictly in order, none skipped (the two designs then differ
    #     by exactly one joint). >0 = the tool may leave up to this many joints on
    #     the rack, using as few as it can to land within depth_tolerance.
    "max_skipped_joints": 0,
    "depth_tolerance": 0.50,     # only used when max_skipped_joints > 0

    "shoe_length": 0.55,
    "float_joint_length": 11.95,  # run directly above the shoe-track joint

    "pup_length": 3.05,          # 0 = no pup joint
    "pup_above_number": 20,      # pup sits directly above the item with this tally number
    "number_the_shoe": False,    # False: shoe unnumbered, shoe-track joint = 1

    # Couplings (used when a centraliser position is "coupling")
    "coupling_length": 0.25,
    "coupling_end": "top",       # "top": joints run pin-down / coupling-up

    # Centraliser rules - one dict per rule:
    #   on        "shoe_track", "float", "casing" or "pup"
    #   type      label shown on the tally
    #   every     1 = every joint, 2 = every second joint ...        (default 1)
    #   start     which joint of the group gets the first, from the bottom (default 1)
    #   per_joint centralisers on each chosen joint (>1 = spaced evenly) (default 1)
    #   position  "centre", "coupling", or a fraction 0-1 up the joint  (default "centre")
    #   from_no / to_no   optional range of tally numbers
    "centraliser_design": [
        {"on": "shoe_track", "type": "Rigid", "position": "centre"},
        {"on": "casing", "type": "Bow spring", "every": 2, "start": 1,
         "position": "centre"},
    ],

    # Casing joints in the order you want them run. Joint 1 is the shoe-track joint.
    "available_joints": [
        11.89, 11.74, 12.19, 11.67, 12.08, 11.93, 11.65, 12.06,
        11.63, 11.99, 11.66, 11.68, 11.98, 12.34, 11.71, 11.80,
        12.16, 12.45, 12.12, 11.96, 12.48, 11.64, 12.37, 11.86,
        11.73, 11.71, 11.88, 12.33, 11.76, 12.12, 12.18, 11.94,
        12.09, 11.66, 11.65, 11.79, 12.21, 11.98, 11.88, 12.13,
        12.01, 11.87, 12.31, 12.23, 11.82, 12.12, 12.07, 12.39,
    ],
}


def _merged(cfg):
    """DEFAULT_CONFIG with any keys in `cfg` overriding it."""
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg or {})
    return merged


def validate_config(cfg):
    """Raises ValueError listing every problem found in the inputs."""
    problems = []

    def number(key, label, minimum=0.0, strict=False):
        try:
            value = float(cfg[key])
        except (TypeError, ValueError):
            problems.append(f"{label} must be a number.")
            return
        if value < minimum or (strict and value == minimum):
            problems.append(f"{label} must be {'greater than' if strict else 'at least'} "
                            f"{minimum:g}.")

    number("target_depth", "Prognosed drilled depth", 0.0, strict=True)
    number("rathole", "Rathole")
    number("stickup", "Stick-up")
    number("depth_tolerance", "Depth tolerance")
    number("shoe_length", "Shoe length")
    number("float_joint_length", "Float joint length", 0.0, strict=True)
    number("pup_length", "Pup joint length")
    number("coupling_length", "Coupling length")
    number("max_skipped_joints", "Max skipped joints")
    number("pup_above_number", "Pup position", 1.0)

    if cfg["coupling_end"] not in ("top", "bottom"):
        problems.append('Coupling end must be "top" or "bottom".')
    joints = cfg["available_joints"]
    if not joints:
        problems.append("Add at least one joint to the joint pool.")
    elif any((not isinstance(x, (int, float))) or x <= 0 for x in joints):
        problems.append("Every joint length must be a number greater than 0.")
    if not problems and cfg["rathole"] >= cfg["target_depth"]:
        problems.append("Rathole must be less than the drilled depth.")

    if problems:
        raise ValueError("\n".join(problems))


# =============================================================================
# JOINT SELECTION
# =============================================================================
def select_joints(lengths, required, tolerance, max_skips, side):
    """
    Pick joints from `lengths` (kept in order) so their total is as close as
    possible to `required` on one side of it:
        side = "short" -> total <= required
        side = "long"  -> total >= required

    Priority: (1) fewest skipped joints that gets within `tolerance`,
              (2) earliest joints in the list, (3) smallest error.
    If nothing is within tolerance, the closest result found is returned.
    Returns (used_indices, total_length), or (None, None) if impossible.
    """
    n = len(lengths)
    L = [round(x * SCALE) for x in lengths]          # work in whole hundredths
    req = round(required * SCALE)
    tol = round(tolerance * SCALE)
    limit = req + (max(L) if L else 0) + tol         # highest total we track
    full = (1 << (limit + 1)) - 1

    if (n + 1) * (max_skips + 1) * (limit // 8 + 1) > MAX_SEARCH_BYTES:
        raise ValueError("That combination of depth, joint count and skipped joints is "
                         "too large to search. Reduce the number of skipped joints.")

    # R[k][j] = bitset of totals reachable using the first k joints with
    # exactly j of them skipped (bit s set  ->  total of s hundredths).
    R = [[0] * (max_skips + 1) for _ in range(n + 1)]
    R[0][0] = 1
    for k in range(1, n + 1):
        for j in range(max_skips + 1):
            run_it = (R[k - 1][j] << L[k - 1]) & full
            skip_it = R[k - 1][j - 1] if j else 0
            R[k][j] = run_it | skip_it

    def nearest(bits):
        """Reachable total nearest to req on the wanted side, or None."""
        if side == "short":
            below = bits & ((1 << (req + 1)) - 1)
            return below.bit_length() - 1 if below else None
        above = bits >> req
        return req + (above & -above).bit_length() - 1 if above else None

    best = None                                      # (error, skips, k, total)
    for j in range(max_skips + 1):
        for k in range(j, n + 1):
            s = nearest(R[k][j])
            if s is None:
                continue
            err = abs(s - req)
            if err <= tol:
                best = (err, j, k, s)
                break                                # earliest k for this j
            if best is None or err < best[0]:
                best = (err, j, k, s)
        if best is not None and best[0] <= tol:
            break                                    # fewest skips wins

    if best is None:
        return None, None

    _, j, k, s = best
    used = []
    while k > 0:                                     # walk back to find the joints
        if j and (R[k - 1][j - 1] >> s) & 1:
            j -= 1                                   # joint k was left out
        else:
            used.append(k - 1)
            s -= L[k - 1]
        k -= 1
    used.reverse()
    return used, sum(lengths[i] for i in used)


# =============================================================================
# CENTRALISERS
# =============================================================================
def _position_fraction(position, rule_no):
    """Turns "centre" or a 0-1 number into a fraction up the joint."""
    if isinstance(position, str):
        if position.strip().lower() in ("centre", "center"):
            return 0.5
        raise ValueError(f'Centraliser rule {rule_no}: position must be "centre", '
                         f'"coupling" or a number between 0 and 1.')
    if not 0 < position < 1:
        raise ValueError(f"Centraliser rule {rule_no}: position must be between 0 and 1.")
    return float(position)


def _coupling_fraction(item, rule_no, cfg):
    """Fraction up the joint of the middle of its coupling."""
    length = item["length"]
    coupling = float(cfg["coupling_length"])
    if not 0 <= coupling < length:
        raise ValueError(f"Centraliser rule {rule_no}: coupling length must be 0 or more "
                         f"and shorter than the joint it sits on ({length:.2f}).")
    half = coupling / 2
    return (length - half if cfg["coupling_end"] == "top" else half) / length


def place_centralisers(items, cfg):
    """
    Applies the centraliser rules to the finished tally (call after depths are set).
    Adds it["cents"] to every item. Returns (schedule, warnings); the schedule
    lists every centraliser from the bottom of the string upward.
    """
    warnings = []
    for it in items:
        it["cents"] = []

    for rule_no, rule in enumerate(cfg["centraliser_design"], start=1):
        kind = rule.get("on")
        if kind not in CENTRALISER_KINDS:
            raise ValueError(f'Centraliser rule {rule_no}: "on" must be one of '
                             f'{CENTRALISER_KINDS}.')
        every = int(rule.get("every", 1))
        start = int(rule.get("start", 1))
        per_joint = int(rule.get("per_joint", 1))
        if every < 1 or start < 1 or per_joint < 1:
            raise ValueError(f"Centraliser rule {rule_no}: every, start and per_joint "
                             f"must be 1 or more.")
        label = rule.get("type", "Centraliser")
        low, high = rule.get("from_no"), rule.get("to_no")

        position = rule.get("position", "centre")
        over_coupling = isinstance(position, str) and position.strip().lower() == "coupling"
        if over_coupling and per_joint != 1:
            raise ValueError(f'Centraliser rule {rule_no}: position "coupling" can only '
                             f'be used with per_joint 1.')
        if over_coupling:
            fractions = None                          # worked out per joint below
        elif per_joint == 1:
            fractions = [_position_fraction(position, rule_no)]
        else:
            fractions = [(i + 1) / (per_joint + 1) for i in range(per_joint)]

        group = [it for it in items
                 if it["kind"] == kind
                 and (low is None or (it["no"] is not None and it["no"] >= low))
                 and (high is None or (it["no"] is not None and it["no"] <= high))]

        placed = 0
        for idx, it in enumerate(group, start=1):
            if idx >= start and (idx - start) % every == 0:
                for f in (fractions or [_coupling_fraction(it, rule_no, cfg)]):
                    it["cents"].append({"type": label, "frac": f, "rule": rule_no,
                                        "coupling": over_coupling})
                    placed += 1
        if placed == 0:
            warnings.append(f"Centraliser rule {rule_no} ({kind}, {label}) matched no "
                            f"joints, so no centralisers were placed for it.")

    schedule = []
    for it in items:
        for c in it["cents"]:
            along = c["frac"] * it["length"]
            schedule.append({
                "type": c["type"],
                "joint_no": it["no"],
                "frac": c["frac"],
                "coupling": c["coupling"],
                "along_joint": along,
                "above_shoe": it["cum_length"] - it["length"] + along,
                "depth": it["bottom_depth"] - along,
            })
    schedule.sort(key=lambda c: c["above_shoe"])
    for n, c in enumerate(schedule, start=1):
        c["no"] = n
    return schedule, warnings


# =============================================================================
# DESIGN BUILDING
# =============================================================================
def build_design(cfg, side):
    """
    Builds one casing design ("short" or "long").
    Returns (items, info). Items run from the bottom of the string upward.
    items is None if no design could be built.
    """
    cfg = _merged(cfg)
    validate_config(cfg)
    warnings = []

    shoe_track_joint = cfg["available_joints"][0]    # pool joint 1
    pool = cfg["available_joints"][1:]               # joints 2.. run above the float joint

    pup = cfg["pup_length"] if cfg["pup_length"] > 0 else 0.0
    target_shoe_depth = cfg["target_depth"] - cfg["rathole"]
    fixed = cfg["shoe_length"] + shoe_track_joint + cfg["float_joint_length"] + pup
    required = target_shoe_depth + cfg["stickup"] - fixed   # length still to be filled

    if required < 0:
        warnings.append("Fixed items alone are longer than the target string length.")
        required = 0.0

    used, _ = select_joints(pool, required, cfg["depth_tolerance"],
                            int(cfg["max_skipped_joints"]), side)
    if used is None:
        return None, {"warnings": [
            "Not enough joints in the joint pool to build the longer design."]}

    # ---- assemble items bottom -> up (before numbering) ----------------------
    items = [
        {"item": "Casing shoe", "kind": "shoe", "length": cfg["shoe_length"],
         "pool_no": None, "numbered": cfg["number_the_shoe"]},
        {"item": "Casing joint (shoe track)", "kind": "shoe_track",
         "length": shoe_track_joint, "pool_no": 1, "numbered": True},
        {"item": "Float joint", "kind": "float", "length": cfg["float_joint_length"],
         "pool_no": None, "numbered": True},
    ]
    for i in used:
        items.append({"item": "Casing joint", "kind": "casing", "length": pool[i],
                      "pool_no": i + 2, "numbered": True})

    def number(item_list):
        n = 0
        for it in item_list:
            if it["numbered"]:
                n += 1
                it["no"] = n
            else:
                it["no"] = None

    number(items)

    # ---- insert the pup joint -------------------------------------------------
    if pup > 0:
        pup_item = {"item": "Pup joint", "kind": "pup", "length": pup,
                    "pool_no": None, "numbered": True}
        above = int(cfg["pup_above_number"])
        pos = next((k for k, it in enumerate(items) if it["no"] == above), None)
        if pos is None:
            warnings.append(
                f"No item numbered {above} - pup joint placed at the top "
                f"of the string instead.")
            items.append(pup_item)
        else:
            items.insert(pos + 1, pup_item)
        number(items)

    # ---- depths ---------------------------------------------------------------
    total_length = sum(it["length"] for it in items)
    shoe_depth = total_length - cfg["stickup"]

    cumulative = 0.0
    for it in items:
        it["bottom_depth"] = shoe_depth - cumulative
        cumulative += it["length"]
        it["top_depth"] = shoe_depth - cumulative
        it["cum_length"] = cumulative

    # ---- centralisers ----------------------------------------------------------
    cent_schedule, cent_warnings = place_centralisers(items, cfg)
    warnings.extend(cent_warnings)
    cent_totals = {}
    for c in cent_schedule:
        cent_totals[c["type"]] = cent_totals.get(c["type"], 0) + 1

    # ---- summary info ---------------------------------------------------------
    units = cfg["units"]
    shoe_error = shoe_depth - target_shoe_depth
    used_set = set(used)
    last_used = max(used) if used else -1
    skipped = [i + 2 for i in range(last_used + 1) if i not in used_set]
    unused = [i + 2 for i in range(last_used + 1, len(pool))]

    if cfg["max_skipped_joints"] > 0 and abs(shoe_error) > cfg["depth_tolerance"] + 1e-9:
        warnings.append(
            f"Closest achievable design is {abs(shoe_error):.2f} {units} "
            f"{'deeper' if shoe_error > 0 else 'shorter'} than the target shoe depth "
            f"- outside the +/-{cfg['depth_tolerance']} {units} tolerance "
            f"(more skipped joints or a bigger tolerance would change this).")
    if not unused and last_used == len(pool) - 1 and shoe_error < -1e-9:
        warnings.append("Ran out of available joints before reaching the target shoe depth.")
    if side == "short" and shoe_error > 1e-9:
        warnings.append("Could not build a design shorter than the target.")

    info = {
        "units": units,
        "target_td": cfg["target_depth"],
        "rathole": cfg["rathole"],
        "stickup": cfg["stickup"],
        "target_shoe_depth": target_shoe_depth,
        "shoe_depth": shoe_depth,
        "shoe_error": shoe_error,
        "required_td": shoe_depth + cfg["rathole"],
        "actual_rathole": cfg["target_depth"] - shoe_depth,
        "total_length": total_length,
        "skipped": skipped,
        "unused": unused,
        "centralisers": cent_schedule,
        "cent_totals": cent_totals,
        "has_centraliser_rules": bool(cfg["centraliser_design"]),
        "warnings": warnings,
    }
    return items, info


def build_designs(cfg):
    """
    Builds the shorter and longer designs.
    Returns a list of (title, items, info) - one entry if both are identical.
    """
    short_items, short_info = build_design(cfg, "short")
    long_items, long_info = build_design(cfg, "long")

    same = (short_items is not None and long_items is not None and
            [(i["item"], i["length"]) for i in short_items] ==
            [(i["item"], i["length"]) for i in long_items])
    if same:
        return [("Exact match - the shorter and longer designs are identical",
                 short_items, short_info)]
    return [("Design A - slightly shorter than the target shoe depth",
             short_items, short_info),
            ("Design B - slightly longer than the target shoe depth",
             long_items, long_info)]


# =============================================================================
# TABLE HELPERS (used by the web page, Excel export and text output)
# =============================================================================
def cent_text(it):
    """Short description of the centralisers on one joint, e.g. '1x Rigid @50%'."""
    by_rule = {}
    for c in it.get("cents", []):
        by_rule.setdefault(c["rule"], []).append(c)
    parts = []
    for group in by_rule.values():
        if len(group) == 1 and group[0]["coupling"]:
            parts.append(f"1x {group[0]['type']} @coupling")
        elif len(group) == 1:
            parts.append(f"1x {group[0]['type']} @{group[0]['frac'] * 100:.0f}%")
        else:
            parts.append(f"{len(group)}x {group[0]['type']}")
    return ", ".join(parts) if parts else "-"


def tally_table_rows(items):
    """The tally as a list of dicts, bottom of the string first."""
    return [{
        "No.": it["no"],
        "Item": it["item"],
        "Pool #": it["pool_no"],
        "Length": it["length"],
        "Cum. length": it["cum_length"],
        "Top depth": it["top_depth"],
        "Bottom depth": it["bottom_depth"],
        "Centralisers": cent_text(it),
    } for it in items]


def centraliser_table_rows(info):
    """The centraliser schedule as a list of dicts, bottom of the string first."""
    return [{
        "No.": c["no"],
        "Type": c["type"],
        "On joint": c["joint_no"],
        "Up the joint": c["along_joint"],
        "Position": "coupling" if c["coupling"] else f"{c['frac'] * 100:.0f}%",
        "Depth": c["depth"],
        "Above shoe": c["above_shoe"],
    } for c in info["centralisers"]]


# =============================================================================
# TEXT OUTPUT (for running this file directly)
# =============================================================================
def print_design(title, items, info):
    if items is None:
        print("\n" + title.upper())
        for w in info["warnings"]:
            print(f"WARNING: {w}")
        print()
        return

    u = info["units"]
    texts = [cent_text(it) for it in items]
    cw = max([len("Centralisers")] + [len(t) for t in texts])
    header = (f"{'No.':>4}  {'Item':<27}{'Pool#':>6}  {'Length':>8}  "
              f"{'Cum. len':>9}  {'Top depth':>10}  {'Bottom depth':>12}  "
              f"{'Centralisers':<{cw}}")
    line = "-" * len(header)

    print("\n" + "=" * len(header))
    print(title.upper())
    print("=" * len(header))
    print("Numbered from the bottom of the well upward")
    print(line)
    print(header)
    print(line)
    for it, text in zip(items, texts):
        no = "-" if it["no"] is None else str(it["no"])
        pool = "-" if it["pool_no"] is None else str(it["pool_no"])
        print(f"{no:>4}  {it['item']:<27}{pool:>6}  {it['length']:>8.2f}  "
              f"{it['cum_length']:>9.2f}  {it['top_depth']:>10.2f}  "
              f"{it['bottom_depth']:>12.2f}  {text:<{cw}}")
    print(line)

    if info["has_centraliser_rules"]:
        print("\nCENTRALISER SCHEDULE  (numbered from the bottom of the well upward)")
        sline = "-" * 76
        print(sline)
        print(f"{'No.':>4}  {'Type':<18}{'On joint':>8}  {'Up the joint':>20}  "
              f"{'Depth':>9}  {'Above shoe':>10}")
        print(sline)
        for c in info["centralisers"]:
            joint = "-" if c["joint_no"] is None else str(c["joint_no"])
            how = "coupling" if c["coupling"] else f"{c['frac'] * 100:.0f}%"
            up = f"{c['along_joint']:.2f} {u} ({how})"
            print(f"{c['no']:>4}  {c['type']:<18}{joint:>8}  {up:>20}  "
                  f"{c['depth']:>9.2f}  {c['above_shoe']:>10.2f}")
        print(sline)
        if info["centralisers"]:
            totals = ", ".join(f"{n} x {t}" for t, n in info["cent_totals"].items())
            print(f"Total centralisers: {len(info['centralisers'])}   ({totals})")
        else:
            print("No centralisers placed.")

    print(f"\nPrognosed drilled depth          : {info['target_td']:.2f} {u}"
          f"   (rathole {info['rathole']:.2f} {u})")
    print(f"Target shoe depth                : {info['target_shoe_depth']:.2f} {u}")
    print(f"Shoe depth of this design        : {info['shoe_depth']:.2f} {u}"
          f"   ({info['shoe_error']:+.2f} {u} vs target)")
    print(f"Drilled depth this design needs  : {info['required_td']:.2f} {u}"
          f"   (shoe depth + rathole)")
    print(f"Rathole if drilled to prognosis  : {info['actual_rathole']:.2f} {u}")
    print(f"String length                    : {info['total_length']:.2f} {u}"
          f"   (stick-up {info['stickup']:.2f} {u})")
    if info["skipped"]:
        print(f"Skipped pool joints (left on the rack): {info['skipped']}")
    if info["unused"]:
        print(f"Unused pool joints: {info['unused'][0]} to {info['unused'][-1]}"
              f"  ({len(info['unused'])} joints)")
    for w in info["warnings"]:
        print(f"WARNING: {w}")
    print()


if __name__ == "__main__":
    for design_title, design_items, design_info in build_designs(DEFAULT_CONFIG):
        print_design(design_title, design_items, design_info)
