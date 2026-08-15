"""
Peak detection and lineshape fitting for field-swept traces.

The sweep data arrives as a 2-D array indexed (field, frequency). Everything
here works on the *other* orientation: one trace per frequency, each running
along the field axis, because that is the axis a resonance moves along.

Both directions count as peaks. A resonance in |S21| is an absorption dip,
not a maximum, so anything that only looks for maxima finds nothing on the
transmission data this rig mostly produces.

Detection is deliberately two stages, and they are not the same thing:

  * find_peaks locates candidates. It is discrete -- it can only ever return
    a position that is one of the sampled field values.
  * a Gaussian or Lorentzian fit around each candidate refines that position
    to somewhere between samples, and is what gets reported.

The second stage matters because the field step is set from the Experiment
tab and is typically coarse next to a resonance width; a peak reported at the
nearest sampled field is quantised to that step, which is a systematic error
no amount of averaging removes. When a fit will not converge the detected
position is kept and flagged, rather than the peak being dropped.

Nothing in this module reads configuration, touches the filesystem, or draws
anything.
"""
import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

#: Fit shapes offered in the Plotter window.
LORENTZIAN = 'lorentzian'
GAUSSIAN = 'gaussian'
FIT_SHAPES = (LORENTZIAN, GAUSSIAN)


def lorentzian(x, amplitude, centre, width, offset):
    """Lorentzian peak. `width` is the half-width at half-maximum."""
    return offset + amplitude * width ** 2 / ((x - centre) ** 2 + width ** 2)


def gaussian(x, amplitude, centre, width, offset):
    """Gaussian peak. `width` is the standard deviation."""
    return offset + amplitude * np.exp(-((x - centre) ** 2) / (2 * width ** 2))


SHAPE_FUNCTIONS = {LORENTZIAN: lorentzian, GAUSSIAN: gaussian}


def orient_by_field(matrix):
    """
    (n_field, n_frequency) -> (n_frequency, n_field).

    Turns the stored array into a sequence of traces, one per frequency, each
    running along the field axis.
    """
    return np.asarray(matrix, dtype=float).T


def select_evenly(count, wanted):
    """
    Indices of `wanted` items spread evenly across `count`, ends included.

    With 10 traces and 3 wanted this gives 0, 4, 9 -- the first, the middle
    and the last -- so the selection always spans the full frequency range
    rather than clustering at one end.
    """
    if wanted <= 0 or count <= 0:
        return []
    if wanted == 1:
        return [0]
    if wanted >= count:
        return list(range(count))
    return sorted({int(round(i)) for i in np.linspace(0, count - 1, wanted)})


def _noise_estimate(values):
    """
    Robust noise scale, via the median absolute deviation.

    A standard deviation over a trace containing a resonance is inflated by
    the resonance itself, which then raises the detection threshold and hides
    the very feature being looked for. The MAD is not fooled by a few large
    outliers, which is exactly what a peak is.
    """
    residual = values - np.median(values)
    mad = np.median(np.abs(residual))
    if mad > 0:
        return 1.4826 * mad          # scaled to match a Gaussian sigma
    spread = np.ptp(values)
    return spread / 100.0 if spread > 0 else 1.0


def _candidates(trace, threshold):
    """
    Local extrema of both signs, as [(index, prominence), ...].

    Both signs, because a resonance is not always a maximum. In |S21| it is
    an absorption *dip*, so searching only for maxima finds nothing on the
    very trace this tool exists to analyse; in a rectified DC voltage it can
    be either way up. scipy only finds maxima, so the minima are found by
    running it again on the negated trace.
    """
    found = []
    for sign in (1.0, -1.0):
        indices, properties = find_peaks(sign * trace, prominence=threshold)
        found.extend(zip(indices.tolist(), properties['prominences'].tolist()))
    return found


def detect_peaks(field, trace, n_peaks, prominence_sigma=3.0):
    """
    Indices of the `n_peaks` most prominent features in `trace`.

    Maxima and minima compete on equal terms, ranked by prominence. Returns
    at most `n_peaks` indices, ordered by field. Fewer are returned when the
    trace genuinely does not contain that many distinct features -- padding
    the list out with the next-highest samples would invent resonances that
    are not there, so the shortfall is reported instead (see fit_trace's
    'requested'/'found' counts).
    """
    trace = np.asarray(trace, dtype=float)
    if trace.size < 3 or n_peaks <= 0:
        return np.array([], dtype=int)

    threshold = prominence_sigma * _noise_estimate(trace)
    candidates = _candidates(trace, threshold)

    if not candidates:
        # Nothing clears the noise-scaled threshold. Fall back to bare local
        # extrema so a clean, low-contrast trace still yields something,
        # rather than the caller getting an empty plot with no explanation.
        # prominence=0 rather than omitting it: scipy only returns the
        # 'prominences' key when it was asked to compute them.
        candidates = _candidates(trace, 0)
        if not candidates:
            return np.array([], dtype=int)

    candidates.sort(key=lambda item: item[1], reverse=True)
    strongest = [index for index, _ in candidates[:n_peaks]]
    return np.sort(np.array(strongest, dtype=int))


def _fit_window(field, trace, index, span):
    """Slice of the trace around `index`, used to seed and bound a fit."""
    low = max(index - span, 0)
    high = min(index + span + 1, trace.size)
    return slice(low, high)


def refine_peak(field, trace, index, shape):
    """
    Fits `shape` around a detected peak, returning (centre, width, ok).

    `ok` is False when the fit does not converge or wanders outside the
    window, in which case the detected sample position is returned unchanged.
    A fit that has run away is worse than no fit, and silently returning it
    would put a confident wrong number in the legend.
    """
    function = SHAPE_FUNCTIONS[shape]
    field = np.asarray(field, dtype=float)
    trace = np.asarray(trace, dtype=float)

    step = np.median(np.abs(np.diff(field))) if field.size > 1 else 1.0
    window = _fit_window(field, trace, index, span=max(int(trace.size * 0.1), 4))
    x = field[window]
    y = trace[window]
    if x.size < 4:                       # four free parameters
        return float(field[index]), float(step), False

    # Seed the fit the right way up. For a dip the baseline is the top of the
    # window and the amplitude is negative; guessing an upward peak on a dip
    # sends the optimiser off in the wrong direction and it will not recover.
    if trace[index] >= np.median(y):
        offset_guess = float(np.min(y))
    else:
        offset_guess = float(np.max(y))
    amplitude_guess = float(trace[index] - offset_guess)
    guess = [amplitude_guess or 1.0, float(field[index]), step * 2, offset_guess]

    try:
        popt, _ = curve_fit(function, x, y, p0=guess, maxfev=5000)
    except (RuntimeError, ValueError, TypeError):
        return float(field[index]), float(step), False

    centre, width = float(popt[1]), abs(float(popt[2]))
    # A centre outside the fitting window means the optimiser walked off; the
    # detected position is the more trustworthy answer at that point.
    if not (x.min() <= centre <= x.max()) or not np.isfinite(centre):
        return float(field[index]), float(step), False
    return centre, width, True


def fit_trace(field, trace, n_peaks, shape):
    """
    Detects and refines up to `n_peaks` peaks in one field-swept trace.

    Returns a dict with the refined centres (ascending), their widths, a
    per-peak flag saying whether the fit converged, and how many peaks were
    asked for versus actually found.
    """
    if shape not in SHAPE_FUNCTIONS:
        raise ValueError(f"Unknown fit shape '{shape}'. "
                         f"Expected one of: {', '.join(FIT_SHAPES)}.")

    indices = detect_peaks(field, trace, n_peaks)
    centres, widths, converged = [], [], []
    for index in indices:
        centre, width, ok = refine_peak(field, trace, index, shape)
        centres.append(centre)
        widths.append(width)
        converged.append(ok)

    order = np.argsort(centres) if centres else []
    return {
        'centres': [centres[i] for i in order],
        'widths': [widths[i] for i in order],
        'converged': [converged[i] for i in order],
        'indices': [int(indices[i]) for i in order],
        'requested': int(n_peaks),
        'found': len(centres),
    }


def format_peak_label(result, unit, prefix=''):
    """
    Legend text for one trace: the peak positions, in field units.

    A peak whose fit did not converge is marked with a trailing '~' so a
    quantised, unrefined position is never mistaken for a fitted one.
    """
    if not result['centres']:
        return f"{prefix}no peaks"
    parts = []
    for centre, ok in zip(result['centres'], result['converged']):
        parts.append(f"{centre:.3g}{unit}" + ("" if ok else "~"))
    text = f"{prefix}peaks: " + ", ".join(parts)
    if result['found'] < result['requested']:
        text += f" ({result['found']}/{result['requested']})"
    return text
