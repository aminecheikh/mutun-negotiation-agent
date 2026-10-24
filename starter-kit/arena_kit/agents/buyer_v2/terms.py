"""Term values as agents write them, and back.

Agents name a term value with a short label - `payment_days: "60"`, `effective_date: "2027-04-01"`,
`index_clause: "none"` or `"CU-NOTATION 20% material, deadband 3%"`, `"as supplier"` for the supplier's current
value - and code maps the label to the exact value the arena accepts: an option of the case's `term_options`, or
the supplier's standard value (api.md: "every negotiable field except unit_price takes a value from term_options
(or the supplier's standard value)"). Anything else is reported, never guessed.
"""

import math

AS_SUPPLIER = "as supplier"


def label(value) -> str:
    if value is None:
        return "none"
    if isinstance(value, dict):  # index clause
        share = round(100 * float(value.get("material_share") or 0))
        return f"{value.get('series')} {share}% material, deadband {_num(value.get('deadband_pct'))}%"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _num(x) -> str:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    return str(int(f)) if f.is_integer() else str(f)


def allowed(case: dict, field: str, standing: dict, std: dict) -> dict[str, object]:
    """label -> value for one negotiable field: the case's options, the supplier's standard and current value."""
    out: dict[str, object] = {}
    for value in case.get("term_options", {}).get(field) or ():
        out[label(value)] = value
    for value in (std.get(field), standing.get(field)):
        if field in std or field in standing:
            if value is not None or field == "index_clause":
                out.setdefault(label(value), value)
    return out


def options_text(case: dict, standing: dict, std: dict) -> str:
    """One line per negotiable field with the labels an agent may use (for prompts)."""
    lines = []
    for field in case.get("negotiable") or ():
        if field == "unit_price":
            lines.append("- unit_price: any price in EUR with 2 decimals (per the part's price unit)")
            continue
        labels = list(allowed(case, field, standing, std))
        current = label(standing[field]) if field in standing else None
        lines.append(
            f"- {field}: " + " | ".join(f'"{x}"' for x in labels) + (f' (its current: "{current}")' if current else "")
        )
    return "\n".join(lines)


def parse_value(case: dict, field: str, raw, standing: dict, std: dict):
    """(value, problem). `raw` is a label, a number or the exact value."""
    options = allowed(case, field, standing, std)
    if isinstance(raw, str):
        text = raw.strip().strip('"')
        if text.lower() in {AS_SUPPLIER, "as_supplier", "keep", "unchanged"}:
            if field in standing:
                return standing[field], None
            if field in std:
                return std[field], None
            return None, f"{field}: the supplier has no current value"
        for key, value in options.items():
            if key.lower() == text.lower():
                return value, None
        if text.lower() in {"none", "null", "no clause", "no index clause"} and "none" in options:
            return None, None
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            return None, f"{field}: {text!r} is not an allowed value ({' | '.join(options) or 'none listed'})"
        raw = number
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        for value in options.values():
            if isinstance(value, int | float) and math.isclose(float(value), float(raw), abs_tol=1e-9):
                return value, None
        if not options:  # no options listed: the arena takes the value as it is
            return (int(raw) if float(raw).is_integer() else raw), None
        return None, f"{field}: {raw} is not an allowed value ({' | '.join(options)})"
    if raw is None and "none" in options:
        return None, None
    if raw in options.values():
        return raw, None
    return None, f"{field}: unreadable value {raw!r}"


def parse_sheet(case: dict, unit_price, items, standing: dict, std: dict) -> tuple[dict, list[str]]:
    """A full sheet from an agent's proposal: unit_price plus [{field, value}] over the supplier's standing terms
    (fields not named keep the supplier's current value, else its standard, else the buyer's plan)."""
    problems: list[str] = []
    sheet: dict = {}
    for field in case.get("negotiable") or ():
        if field == "unit_price":
            continue
        if field in standing:
            sheet[field] = standing[field]
        elif field in std:
            sheet[field] = std[field]
    for item in items or ():
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        if field not in (case.get("negotiable") or ()) or field == "unit_price":
            if field and field != "unit_price":
                problems.append(f"{field} is not negotiable in this case")
            continue
        value, problem = parse_value(case, field, item.get("value"), standing, std)
        if problem:
            problems.append(problem)
        else:
            sheet[field] = value
    try:
        price = float(unit_price)
        sheet["unit_price"] = round(price, 2) if price > 0 and math.isfinite(price) else None
    except (TypeError, ValueError):
        sheet["unit_price"] = None
    if sheet.get("unit_price") is None:
        problems.append("unit_price missing or invalid")
    return sheet, problems


def describe(terms: dict) -> str:
    return ", ".join(f"{k} {label(v)}" for k, v in terms.items())
