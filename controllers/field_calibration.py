"""
Loading, validation and use of a magnet's mT <-> A calibration curve.

Both magnet drivers turn a requested field into a commanded current the same
way -- a cubic fit of current against measured field -- so that fit lives here,
unchanged, next to the checks that decide whether the curve can support the
request at all.

The checks are here because a calibration file is easy to lose without
noticing: calibration.py rewrites it on every run, and a curve that has been
trimmed, or cut short by a sweep that failed near the end, still loads, still
fits a cubic, and still hands back a confident-looking current for a field it
has no measurements anywhere near. A bad curve is silent -- the magnet simply
sits at the wrong field for the whole experiment -- so it is worth being loud
about here, before any bytes reach the hardware.

Nothing in this module talks to a device.
"""
import os

import numpy as np
import pandas as pd

#: A cubic needs four points; fewer cannot be fitted at all.
MIN_POINTS = 4

#: A gap between adjacent calibration points wider than this fraction of the
#: whole measured span means the fit is interpolating across empty space.
MAX_GAP_FRACTION = 0.10

FIELD_COLUMN = 'Field_mT'
CURRENT_COLUMN = 'Current_A'


def load_curve(path):
    """
    Reads a calibration CSV and returns (field_mT, current_A), sorted by field.

    Rows with a missing field or current are dropped: a single failed reading
    would otherwise poison the fit and turn every subsequent set_field() into a
    NaN current. Raises with an actionable message if the file cannot support a
    cubic fit.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found - run a calibration for this magnet before "
            f"sweeping in mT."
        )

    frame = pd.read_csv(path)
    missing = {FIELD_COLUMN, CURRENT_COLUMN} - set(frame.columns)
    if missing:
        raise ValueError(
            f"{path} is missing the column(s) {', '.join(sorted(missing))}. "
            f"Expected a file written by calibration.py."
        )

    frame = frame[[FIELD_COLUMN, CURRENT_COLUMN]].apply(pd.to_numeric,
                                                        errors='coerce')
    dropped = int(frame.isna().any(axis=1).sum())
    frame = frame.dropna()
    if dropped:
        print(f"  {path}: ignoring {dropped} row(s) with unreadable values.")

    frame = frame.sort_values(FIELD_COLUMN)
    field = frame[FIELD_COLUMN].to_numpy(dtype=float)
    current = frame[CURRENT_COLUMN].to_numpy(dtype=float)

    if field.size < MIN_POINTS:
        raise ValueError(
            f"{path} has only {field.size} usable point(s); a cubic fit needs "
            f"at least {MIN_POINTS}. Re-run the calibration for this magnet."
        )
    return field, current


def _warn_about_coverage(path, field_cal, field):
    """
    Complains if `field` is not actually supported by the measured points.

    Outside the measured range is a hard error: that is extrapolation, and a
    cubic leaving its data does so steeply. Inside a large gap is a warning
    rather than an error, because a sparse curve over a near-linear magnet is
    still usable and refusing would stop a rig that may well be fine.
    """
    low, high = float(field_cal[0]), float(field_cal[-1])
    if not low <= field <= high:
        raise ValueError(
            f"{field:g} mT is outside the calibrated range {low:g} to "
            f"{high:g} mT in {path}. Extrapolating a cubic past its data gives "
            f"a wildly wrong current; re-calibrate to cover this field."
        )

    span = high - low
    if span <= 0:
        return

    index = int(np.searchsorted(field_cal, field))
    below = field_cal[max(index - 1, 0)]
    above = field_cal[min(index, field_cal.size - 1)]
    gap = float(above - below)

    if gap > MAX_GAP_FRACTION * span:
        print(
            f"  WARNING: {path} has no measurements between {below:g} and "
            f"{above:g} mT, a {gap:g} mT gap in a {span:g} mT span.\n"
            f"  WARNING: the current for {field:g} mT is interpolated across "
            f"that gap and may be well off. Re-calibrate to trust this sweep."
        )


def current_for_field(path, field):
    """
    Returns the current in Amps that the calibration says produces `field` mT.

    The fit is the same cubic of current against measured field the drivers
    have always used; only the checks around it are new.
    """
    field_cal, current_cal = load_curve(path)
    _warn_about_coverage(path, field_cal, field)
    coeffs = np.polyfit(field_cal, current_cal, 3)
    return float(np.poly1d(coeffs)(field))


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else 'field_calibration_data.csv'
    field_cal, current_cal = load_curve(path)
    print(f"{path}: {field_cal.size} points, "
          f"{field_cal[0]:g} to {field_cal[-1]:g} mT, "
          f"{current_cal.min():g} to {current_cal.max():g} A")
    if len(sys.argv) > 2:
        want = float(sys.argv[2])
        print(f"{want:g} mT -> {current_for_field(path, want):.4f} A")
