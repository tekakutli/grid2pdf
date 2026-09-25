"""
nested_squares.py — print sheet of a nested rotating-square cascade.

==============================================================================
WHAT THIS SHEET SHOWS
==============================================================================

Three nested rounded squares, each one governing a circle.

    1. OUTER SQUARE (side L, sharp corners)
       Rotates about the origin.  Its four corners trace a circle of
       radius R_outer = L / sqrt(2), the "outer boundary".  A ring of
       tangential arrows around that radius depicts the rotation.

    2. MIDDLE SQUARE (side s, corner radius c)
       Rides in the corner of the outer square, tangent to the two walls
       that meet there.  As the outer square rotates, the middle square's
       center is carried around the origin at radius (L - s) / sqrt(2).
       The middle square also carries a small central disk of radius e.
       That disk's far edge reaches

           r_mid = (L - s) / sqrt(2) + e

       from the origin.  This is the "middle circle".

    3. INNERMOST SQUARE (same s and c as the middle square)
       Placed so that one of its far corners just touches the middle
       circle.  Its own central disk of radius e reaches

           r1 = rho_inner + e

       where rho_inner is the distance of the innermost square's center
       from the origin.  r1 is the "inner circle"; L is the sheet's
       headline result.

==============================================================================
THE DERIVATION (why the numbers come out the way they do)
==============================================================================

Let a = s / 2 - c be the distance from a rounded square's center to the
center of any of its corner arcs.  A rounded square centered at distance
rho from the origin, with local +x pointing radially outward, has its two
far corner-arc centers at world distance

    sqrt((rho + a)^2 + a^2)

from the origin.  (The two near corner-arc centers are at
sqrt((rho - a)^2 + a^2).)  So its far corners lie on a circle of radius

    sqrt((rho + a)^2 + a^2) + c.

Given inputs:

    s          — width across flats of the rounded squares
    c          — corner radius of the rounded squares
    r1_target  — desired radius of the innermost circle
    e          — radius of the central disk carried by each square

the cascade is solved bottom-up:

    a         = s/2 - c
    rho_inner = r1_target - e                   (inner disk just reaches r1)
    r_mid     = sqrt((rho_inner + a)^2 + a^2) + c
                                                (inner square far corner
                                                 touches the mid circle)
    L_side    = sqrt(2) * (r_mid - e) + s       (middle square corner-tucked
                                                 in the outer square, its
                                                 disk reaching r_mid)
    R_outer   = L_side / sqrt(2)                (circumradius of outer square)

==============================================================================
SHEET LAYOUT
==============================================================================

    +-----------------------------------------------------+-------------+
    |                                                     |             |
    |            DRAWING (single axes)                    |   LEGEND    |
    |                                                     |             |
    |     - outer square (solid, one real pose + ghosts)  |  line       |
    |     - middle square (long-dash, hatched fill)       |  samples    |
    |     - innermost square (short-dash, dotted fill)    |  + text     |
    |     - two circles: r_mid, r1_target                 |             |
    |     - central disks (hatched "xxx")                 |             |
    |     - rotation arrows on R_outer                    |             |
    |     - vertical scale ruler with r1 and r2 ticks     |             |
    |     - one side-dimension on the outer square        |             |
    |                                                     |             |
    +-----------------------------------------------------+             |
    |      FORMULA STRIP  (general | solved)              |             |
    +-----------------------------------------------------+-------------+

    Header (title + rule) sits inside the top margin.  A single MARGIN
    constant keeps the sheet's outer whitespace uniform on all four
    sides — the title's top edge, the content box's bottom edge, and the
    content box's left/right edges all sit at the same distance from the
    paper edge.  Everything is monospace and black/white; layers are
    distinguished by line pattern and hatch, never by hue.

==============================================================================
RULER LABEL STACKING
==============================================================================

Each ruler label is a (radius, name, value) triple.  The value is drawn
as a large horizontal number on the tick; the name is drawn as a smaller
line above the value, and is deliberately shifted LEFT of the value's
right edge.  This keeps the stacked pair from reading as one multi-line
number while cutting the ruler's horizontal footprint by about half.

All coordinates in the drawing are in centimetres, matching the inputs.
"""

import textwrap
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle, Rectangle
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

# ----------------------------------------------------------------------------
# INPUTS
#
# The only four numbers the sheet needs.  Everything else is derived.
# ----------------------------------------------------------------------------
s         = 10.0   # rounded-square width across flats (cm)
c         = 1.0    # rounded-square corner radius (cm)
r1_target = 3.0    # target radius of the innermost circle (cm)
e         = 1.0    # radius of the central disk carried by each square (cm)

# ----------------------------------------------------------------------------
# DERIVED GEOMETRY
#
# See the module docstring for the full derivation.  All in cm.
# ----------------------------------------------------------------------------
a         = s/2 - c
rho_inner = r1_target - e
r_mid     = np.sqrt((rho_inner + a)**2 + a**2) + c
L_side    = np.sqrt(2) * (r_mid - e) + s
R_outer   = L_side / np.sqrt(2)

# ----------------------------------------------------------------------------
# PALETTE
#
# Greyscale only.  INK is the drawing line, INK_SOFT is for helper text,
# INK_FAINT / GHOST are for ghosted poses and construction hints,
# ARROW_TONE is a middle grey so rotation arrows stay subordinate to the
# geometry they annotate.  PAPER is the background — never colored.
# ----------------------------------------------------------------------------
INK        = "#000000"
INK_SOFT   = "#555555"
INK_FAINT  = "#d0d0d0"
ARROW_TONE = "#909090"
GHOST      = "#bdbdbd"
PAPER      = "#ffffff"
FONT_MONO  = "DejaVu Sans Mono"

# ----------------------------------------------------------------------------
# LINE STYLES
#
# Six distinct line signatures — one per movable entity — so a black-and-
# white print still separates the four nested shapes at a glance.  The
# "disk" style is for the hatched central disks.
# ----------------------------------------------------------------------------
STYLE = {
    "outer_sq":  dict(ls="-",                lw=1.5),
    "outer_cir": dict(ls="-",                lw=1.4),
    "mid_sq":    dict(ls=(0, (9, 5)),        lw=1.7),
    "mid_cir":   dict(ls=(0, (7, 3, 1, 3)),  lw=1.6),
    "inner_sq":  dict(ls=(0, (4, 3)),        lw=1.7),
    "inner_cir": dict(ls="-",                lw=2.2),
    "disk":      dict(ls="-",                lw=1.2),
}

GHOST_LW_FACTOR = 0.55      # ghost line weights are 55% of the real ones
HATCH_MID       = "\\"      # texture of the middle square's interior
HATCH_INNER     = "."       # texture of the innermost square's interior
HATCH_DISK      = "xxx"     # texture of the small central disks

# ----------------------------------------------------------------------------
# ANGLES
#
# Every square is drawn in one "real" pose and one or more ghost poses.
# Ghosts help the eye read the motion without cluttering the sheet.
# ----------------------------------------------------------------------------
OUTER_ROT         = 20.0    # real outer-square rotation (degrees)
OUTER_GHOST_ANGS  = (260.0,)
MID_GHOST_ANGS    = (260.0,)
INNER_REAL_ANG    = 200.0   # angle of the innermost square's center
INNER_GHOST_ANGS  = (140.0,)

# Ring of tangential arrows drawn just outside R_outer to signal rotation.
ROT_ARROW_COUNT   = 32
ROT_ARROW_LEN     = 1.15
ROT_ARROW_HEAD    = 13      # arrowhead "mutation scale" (pt)

# Vertical scale ruler — the value font is the "large" number, the name
# font is the small line above it.  RULER_NAME_SHIFT moves the name's
# right edge LEFT of the value's right edge on purpose.
RULER_FSIZE         = 24.0
RULER_NAME_FSIZE    = RULER_FSIZE * 0.62
RULER_MINOR         = 1.0         # minor tick every 1 cm
RULER_GAP           = 1.4         # gap between ruler and drawing body
RULER_PAD           = None        # computed once BODY_HALF is known
RULER_ANCHOR_OFFSET = 1.6         # spine -> value right edge (cm)
RULER_NAME_SHIFT    = 1.4         # how far left the name sits (cm)
RULER_CHAR_W_FACTOR = 0.62        # monospace char width / pt

# Side dimension on the outer square.
SIDE_DIM_OFFSET = 1.4       # how far the dim line sits outside the square

# ============================================================================
# LAYOUT  (all fractions of the figure; FIG_W/H in inches)
#
# A single MARGIN constant keeps the sheet's outer whitespace uniform on
# all four sides.  The title's top edge, the content box's bottom edge,
# and the content box's left/right edges all sit exactly MARGIN from the
# paper edge.  The header rule and the top of the content box are
# derived from the header block height plus a small gap below the rule.
# ============================================================================
FIG_W, FIG_H = 10.5, 11.5

MARGIN          = 0.045      # uniform whitespace on every side
HEADER_BLOCK_H  = 0.034      # title text block height
HEADER_TO_BODY  = 0.030      # gap between the header rule and the body

HEADER_L       = MARGIN
HEADER_R       = 1.0 - MARGIN
HEADER_TITLE_Y = 1.0 - MARGIN       # top of title text (va='top')
HEADER_RULE_Y  = HEADER_TITLE_Y - HEADER_BLOCK_H

CONTENT_L = MARGIN
CONTENT_R = 1.0 - MARGIN
CONTENT_T = HEADER_RULE_Y - HEADER_TO_BODY
CONTENT_B = MARGIN

# Column split — left column holds the drawing and the formula strip,
# right column holds the legend.  The split is chosen to preserve the
# previous relative proportion between the two columns now that the
# content box has shrunk inward by MARGIN on both sides.
COL_SPLIT       = 0.687
GUTTER_LEGEND   = 0.020

# Left column: drawing (top), formula strip (bottom).
DRAW_L = CONTENT_L
DRAW_W = COL_SPLIT - DRAW_L - GUTTER_LEGEND

FORM_L = DRAW_L
FORM_W = DRAW_W

FORM_H        = 0.320
FORM_GAP      = 0.014
FORM_CELL_H   = (FORM_H - FORM_GAP) / 2.0

FORM_B        = CONTENT_B
FORM_T        = FORM_B + FORM_H

# Drawing sits above the formula strip.
GAP    = 0.014
DRAW_B = FORM_T + GAP
DRAW_H = CONTENT_T - DRAW_B

# Right column.
LEG_L = COL_SPLIT
LEG_W = CONTENT_R - LEG_L
LEG_B = CONTENT_B
LEG_H = CONTENT_T - CONTENT_B

# ============================================================================
# FORMULA STRINGS  (raw, unwrapped)
#
# Two blocks: the general closed form, and the same form with the current
# inputs substituted.  Both end with L at the bottom — L (the outer
# square's side) is the sheet's headline result.  The solved block is
# built from the actual inputs, so it stays correct for any (s, c, r1, e).
# ============================================================================
general_raw = (
    "GENERAL CLOSED FORM\n"
    "  a     = s/2 - c\n"
    "  r_mid = sqrt((r1 - e + a)^2 + a^2) + c\n"
    "  L     = sqrt(2) * (r_mid - e) + s"
)

solved_raw = (
    f"SOLVED FOR  (r1, s, c, e) = "
    f"({r1_target:g}, {s:g}, {c:g}, {e:g}) cm\n"
    f"  a     = {s:g}/2 - {c:g}"
    f" = {a:.4f} cm\n"
    f"  r_mid = sqrt(({r1_target:g}-{e:g}+{a:g})^2 + {a:g}^2) + {c:g}"
    f" = {r_mid:.4f} cm\n"
    f"  L     = sqrt(2)*({r_mid:.4f}-{e:g}) + {s:g}"
    f" = {L_side:.4f} cm"
)

# ============================================================================
# FORMULA WRAPPING
#
# The formula strip is narrower than a single long equation.  We measure
# how many monospace characters fit, then wrap each line so nothing is
# ever cropped.  Formula lines align their continuations under the first
# '='; header lines wrap greedily.  A hard cut is the last resort.
# ============================================================================
FORM_FSIZE  = 12.0
FORM_X_PAD  = 0.022


def _mono_char_w_in(size_pt):
    """Approximate the width in inches of one monospace character."""
    return size_pt * 0.602 / 72.0


def _formula_max_chars(cell_w_frac, size_pt, x_pad):
    """How many monospace characters fit inside a formula cell?"""
    text_w_in = (1.0 - 2.0 * x_pad) * cell_w_frac * FIG_W
    return max(8, int(text_w_in / _mono_char_w_in(size_pt)))


FORM_MAX_CHARS = _formula_max_chars(FORM_W, FORM_FSIZE, FORM_X_PAD)


def _wrap_formula_line(line, max_chars):
    """
    Wrap a single line of a formula block so it never exceeds `max_chars`.

    Formula lines (begin with two spaces and contain '=') break at
    whitespace and align their continuations under the first '='.
    Header lines wrap greedily.  A line whose first token is longer than
    the budget is hard-cut so nothing is ever cropped.
    """
    if len(line) <= max_chars:
        return [line]

    is_formula = line.startswith("  ") and "=" in line

    if not is_formula:
        # Greedy word wrap, no alignment.
        words = line.split()
        if not words:
            return [line]
        out = []
        cur = words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if len(trial) <= max_chars:
                cur = trial
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        return out

    # Formula line: break at whitespace, align continuations under '='.
    eq = line.find("=")
    align_col = min(eq, max(2, max_chars // 3))

    out = []
    remaining = line
    guard = 0
    while len(remaining) > max_chars and guard < 60:
        cut = remaining.rfind(" ", 0, max_chars + 1)
        if cut <= 0:
            cut = max_chars
        out.append(remaining[:cut].rstrip())
        rest = remaining[cut:].lstrip()
        remaining = " " * align_col + rest
        guard += 1
    out.append(remaining)
    return out


def wrap_formula_block(text, max_chars):
    """Apply `_wrap_formula_line` to every line of a multi-line block."""
    out = []
    for line in text.splitlines():
        out.extend(_wrap_formula_line(line, max_chars))
    return "\n".join(out)


general = wrap_formula_block(general_raw, FORM_MAX_CHARS)
solved  = wrap_formula_block(solved_raw,  FORM_MAX_CHARS)


# ============================================================================
# GEOMETRY
#
# All geometry functions operate in centimetres, on plain numpy arrays of
# (x, y) pairs, with the origin at the center of the whole cascade.
# ============================================================================

def rounded_square(s, c, n=64):
    """
    Return the CCW outline of a rounded square centered at the origin.

    Parameters
    ----------
    s : width across flats (side length as if the corners were sharp)
    c : corner radius, 0 <= c <= s/2
    n : samples per 90-degree corner arc

    The outline is built from four straight edges and four quarter-circle
    arcs, walked counter-clockwise starting at the top edge.  The four
    arc centers sit at (±a, ±a) where a = s/2 - c.
    """
    a_ = s/2 - c
    pts = [(-a_, s/2), (a_, s/2)]
    for t in np.linspace(np.pi/2, 0, n):
        pts.append((a_ + c*np.cos(t), a_ + c*np.sin(t)))
    pts.append((s/2, -a_))
    for t in np.linspace(0, -np.pi/2, n):
        pts.append((a_ + c*np.cos(t), -a_ + c*np.sin(t)))
    pts.append((-a_, -s/2))
    for t in np.linspace(-np.pi/2, -np.pi, n):
        pts.append((-a_ + c*np.cos(t), -a_ + c*np.sin(t)))
    pts.append((-s/2, a_))
    for t in np.linspace(np.pi, np.pi/2, n):
        pts.append((-a_ + c*np.cos(t), a_ + c*np.sin(t)))
    return np.array(pts)


def rot(pts, deg):
    """Rotate an (N, 2) point array by `deg` degrees about the origin."""
    th = np.radians(deg)
    Rm = np.array([[np.cos(th), -np.sin(th)],
                   [np.sin(th),  np.cos(th)]])
    return pts @ Rm.T


def place_corner_tucked_middle(outer_rot, L_side, s, c):
    """
    Place the middle square tucked into one corner of the outer square.

    The middle square is tangent to the two walls that meet at the
    outer square's top-right corner (in the outer square's local frame).
    Its center sits at local coordinates (L/2 - s/2, L/2 - s/2), which
    rotates with the outer square.

    Returns
    -------
    (pts, center) : polygon outline and center of the middle square,
                    both in world coordinates.
    """
    local_center = np.array([L_side/2 - s/2, L_side/2 - s/2])
    theta = np.radians(outer_rot)
    Rm = np.array([[np.cos(theta), -np.sin(theta)],
                   [np.sin(theta),  np.cos(theta)]])
    center = Rm @ local_center
    pts = rot(rounded_square(s, c), outer_rot) + center
    return pts, center


def place_inner(angle_deg):
    """
    Place the innermost square so one far corner touches the middle circle.

    The innermost square's center orbits the origin at radius rho_inner.
    At every angle, the local +x axis points radially outward, so the two
    "far" corner arcs (local (a, a) and local (a, -a)) land on the middle
    circle of radius r_mid = sqrt((rho_inner + a)^2 + a^2) + c.

    Returns
    -------
    pts   : world-coordinate polygon outline
    center: world-coordinate center
    touch : list of two world points on r_mid — the far-corner touch
            points, useful for marking tangency.
    """
    th = np.radians(angle_deg)
    cx = rho_inner * np.cos(th)
    cy = rho_inner * np.sin(th)
    pts = rot(rounded_square(s, c), angle_deg) + np.array([cx, cy])

    # The two far corner-arc centers, rotated into world space.
    arc_local = np.array([[a, a], [a, -a]])
    arc_world = rot(arc_local, angle_deg) + np.array([cx, cy])

    # Push each corner-arc center outward by c (the arc radius) to get
    # the actual far-corner point on the middle circle.
    touch = []
    for ac in arc_world:
        d = np.hypot(ac[0], ac[1])
        tp = ac * (1.0 + c / d)
        touch.append(tp)
    return pts, (cx, cy), touch


# ============================================================================
# DRAW HELPERS
#
# Thin wrappers around matplotlib patches that keep line styles, z-order,
# and ink colour consistent across the sheet.
# ============================================================================

def ghost_square(ax, pts, key, hatch=None):
    """Draw a faint polygon (a 'ghost pose') using STYLE[key] at 55% weight."""
    st = STYLE[key]
    ax.add_patch(Polygon(pts, closed=True,
                         fill=(hatch is not None),
                         fc="none" if hatch else "none",
                         hatch=hatch, ec=GHOST,
                         lw=st["lw"] * GHOST_LW_FACTOR,
                         ls=st["ls"], zorder=1))


def real_square(ax, pts, key, hatch=None):
    """Draw a solid polygon (the highlighted pose) using STYLE[key]."""
    st = STYLE[key]
    ax.add_patch(Polygon(pts, closed=True,
                         fill=(hatch is not None),
                         fc="none" if hatch else "none",
                         hatch=hatch, ec=INK,
                         lw=st["lw"], ls=st["ls"], zorder=6))


def circle(ax, radius, key, zorder=3, tone=INK):
    """Draw a circle centered at the origin, in STYLE[key]."""
    st = STYLE[key]
    ax.add_patch(Circle((0, 0), radius, fill=False,
                        ec=tone, lw=st["lw"], ls=st["ls"], zorder=zorder))


def central_disk(ax, center, radius, ghost=False):
    """
    Draw one of the small hatched disks carried at a square's center.
    Ghost disks are lighter and thinner; the real one is at full weight.
    """
    st = STYLE["disk"]
    tone = GHOST if ghost else INK
    lw = st["lw"] * (GHOST_LW_FACTOR if ghost else 1.0)
    ax.add_patch(Circle(center, radius, fill=True, fc="none",
                        hatch=HATCH_DISK, ec=tone, lw=lw,
                        zorder=2 if ghost else 5))


def tangential_arrow(ax, radius, angle_deg, length,
                     color=ARROW_TONE, lw=1.6, head=ROT_ARROW_HEAD):
    """
    Draw a small arrow tangent to a circle of the given radius, starting
    at the given angle and pointing counter-clockwise.  Used to build the
    ring of rotation indicators around R_outer.
    """
    th = np.radians(angle_deg)
    x0 = radius * np.cos(th)
    y0 = radius * np.sin(th)
    tx = -np.sin(th); ty = np.cos(th)     # tangent direction at (x0, y0)
    x1 = x0 + length * tx; y1 = y0 + length * ty
    ax.annotate("",
                xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw,
                                mutation_scale=head,
                                shrinkA=0, shrinkB=0),
                zorder=8)


def draw_vertical_scale_ruler(ax, x_pos, radii_labels,
                              name_offset_cm=2.0,
                              name_shift_cm=RULER_NAME_SHIFT,
                              minor_step=RULER_MINOR,
                              fsize=RULER_FSIZE,
                              name_fsize=RULER_NAME_FSIZE):
    """
    Draw a vertical ruler at x = x_pos.

    Parameters
    ----------
    x_pos          : data-x of the ruler spine
    radii_labels   : list of (radius, name, value) triples
                     radius — y position of the major tick
                     name   — small label drawn above the value ('' for none)
                     value  — large horizontal number centered on the tick
    name_offset_cm : how far above the value the name is stacked
    name_shift_cm  : how far LEFT of the value's right edge the name sits
    minor_step     : spacing of minor ticks in cm
    fsize          : value font size (points)
    name_fsize     : name font size (points)

    Values are right-aligned at (x_pos - RULER_ANCHOR_OFFSET).  Names are
    right-aligned further LEFT — the intentional misalignment keeps the
    stacked pair from reading as a single multi-line number.
    """
    R = max(r for r, _, _ in radii_labels)

    # Spine of the ruler
    ax.plot([x_pos, x_pos], [0, R], color=INK, lw=1.0, zorder=9)
    # Top and bottom end caps
    for yy in (0.0, R):
        ax.plot([x_pos - 0.55, x_pos + 0.55], [yy, yy],
                color=INK, lw=1.3, zorder=9)

    # Minor ticks (skip positions that coincide with a major tick)
    n_minor = int(R / minor_step)
    major_radii = [r for r, _, _ in radii_labels]
    for k in range(1, n_minor + 1):
        yk = k * minor_step
        if any(abs(yk - r) < 1e-6 for r in major_radii):
            continue
        ax.plot([x_pos - 0.20, x_pos], [yk, yk],
                color=INK, lw=0.6, zorder=9)

    # Major ticks + stacked labels
    val_anchor_x  = x_pos - RULER_ANCHOR_OFFSET
    name_anchor_x = val_anchor_x - name_shift_cm

    for r, name, value in radii_labels:
        ax.plot([x_pos - 0.40, x_pos + 0.40], [r, r],
                color=INK, lw=1.5, zorder=9)

        # Value: large, right-aligned at val_anchor_x
        ax.annotate(value,
                    xy=(x_pos - 0.55, r),
                    xytext=(val_anchor_x, r),
                    ha="right", va="center",
                    fontsize=fsize, family=FONT_MONO,
                    color=INK, zorder=9,
                    annotation_clip=False)

        # Name: small, above the value, right-aligned further LEFT
        if name:
            ax.annotate(name,
                        xy=(x_pos - 0.55, r + name_offset_cm),
                        xytext=(name_anchor_x, r + name_offset_cm),
                        ha="right", va="center",
                        fontsize=name_fsize, family=FONT_MONO,
                        color=INK_SOFT, zorder=9,
                        annotation_clip=False)


def draw_side_dimension(ax, p1, p2, label,
                        offset=1.4, tick=0.55, label_gap=0.55,
                        fsize=RULER_FSIZE):
    """
    Draw a dimension line parallel to segment (p1, p2), offset outward
    from the origin, with perpendicular end ticks and a label rotated
    to match the segment.

    Used to annotate the outer square's side length.
    """
    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    d = p2 - p1
    L = float(np.hypot(*d))
    if L < 1e-9:
        return
    u = d / L

    # Outward normal: pick the sign so the dim line moves away from
    # the origin.
    n = np.array([-u[1], u[0]])
    mid = 0.5 * (p1 + p2)
    if np.dot(mid, n) < 0:
        n = -n

    q1 = p1 + n * offset
    q2 = p2 + n * offset

    # Extension lines (dashed) from the square's corners to the dim line.
    for p, q in ((p1, q1), (p2, q2)):
        ax.plot([p[0], q[0]], [p[1], q[1]],
                color=GHOST, lw=0.6, ls=(0, (2, 3)), zorder=8)

    # Dimension line itself.
    ax.plot([q1[0], q2[0]], [q1[1], q2[1]],
            color=INK, lw=1.0, zorder=9)

    # End ticks perpendicular to the dim line.
    for q in (q1, q2):
        ax.plot([q[0] - tick * n[0], q[0] + tick * n[0]],
                [q[1] - tick * n[1], q[1] + tick * n[1]],
                color=INK, lw=1.3, zorder=9)

    # Label, rotated with the segment but flipped to stay readable.
    ang = np.degrees(np.arctan2(u[1], u[0]))
    if ang > 90.0:
        ang -= 180.0
    elif ang < -90.0:
        ang += 180.0

    lm = 0.5 * (q1 + q2) + n * label_gap
    ax.text(lm[0], lm[1], label,
            rotation=ang, rotation_mode="anchor",
            ha="center", va="center",
            fontsize=fsize, family=FONT_MONO,
            color=INK, zorder=10,
            bbox=dict(boxstyle="square,pad=0.18",
                      fc=PAPER, ec="none"))


# ============================================================================
# FIGURE
# ============================================================================
fig = plt.figure(figsize=(FIG_W, FIG_H))
fig.patch.set_facecolor(PAPER)

# ---- header ----------------------------------------------------------------
fig.text(HEADER_L, HEADER_TITLE_Y,
         "NESTED ROTATING SQUARES  —  print sheet",
         fontsize=17, family=FONT_MONO, weight="bold",
         va="top", ha="left", color=INK)

fig.add_artist(Line2D([HEADER_L, HEADER_R],
                      [HEADER_RULE_Y, HEADER_RULE_Y],
                      transform=fig.transFigure,
                      color=INK, lw=1.0))

# ---- drawing axes ----------------------------------------------------------
ax = fig.add_axes([DRAW_L, DRAW_B, DRAW_W, DRAW_H])
ax.set_facecolor(PAPER)
ax.set_aspect('equal', adjustable='datalim')

# ---- legend axes -----------------------------------------------------------
lg = fig.add_axes([LEG_L, LEG_B, LEG_W, LEG_H])
lg.axis("off")
lg.set_xlim(0, 1); lg.set_ylim(0, 1)


# ============================================================================
# DRAWING
# ============================================================================
h = L_side / 2
base = np.array([[-h,-h],[h,-h],[h,h],[-h,h]])

# ---- ghost poses -----------------------------------------------------------
# One ghost of the outer square, one of the middle square (hatched), and
# one of the innermost square (hatched).  All three at 55% line weight.
for a_ in OUTER_GHOST_ANGS:
    ghost_square(ax, rot(base, a_), "outer_sq")

for a_ in MID_GHOST_ANGS:
    pts_g, ctr_g = place_corner_tucked_middle(a_, L_side, s, c)
    ghost_square(ax, pts_g, "mid_sq", hatch=HATCH_MID)
    central_disk(ax, ctr_g, e, ghost=True)

for a_ in INNER_GHOST_ANGS:
    pts_g, ctr_g, _ = place_inner(a_)
    ghost_square(ax, pts_g, "inner_sq", hatch=HATCH_INNER)
    central_disk(ax, ctr_g, e, ghost=True)

# ---- the two reference circles --------------------------------------------
# r_mid is swept by the middle square's central disk; r1_target is the
# target radius of the innermost circle.
circle(ax, r_mid,     "mid_cir",   zorder=3)
circle(ax, r1_target, "inner_cir", zorder=4)

# ---- ring of tangential arrows at R_outer ---------------------------------
# Every arrow is tangent to the outer circle, hinting at rotation.
for th in np.linspace(0.0, 360.0, ROT_ARROW_COUNT, endpoint=False):
    tangential_arrow(ax, radius=R_outer, angle_deg=th,
                     length=ROT_ARROW_LEN)

# ---- real poses -----------------------------------------------------------
# Outer square (solid).
sq_pts_outer = rot(base, OUTER_ROT)
real_square(ax, sq_pts_outer, "outer_sq")
draw_side_dimension(ax, sq_pts_outer[1], sq_pts_outer[2],
                    label=f"L = {L_side:.2f} cm",
                    offset=SIDE_DIM_OFFSET)

# Middle square, tucked in the outer square's corner.
mid_pts, mid_ctr = place_corner_tucked_middle(OUTER_ROT, L_side, s, c)
real_square(ax, mid_pts, "mid_sq", hatch=HATCH_MID)
central_disk(ax, mid_ctr, e, ghost=False)
ax.plot(mid_ctr[0], mid_ctr[1], marker="o", ms=3.0,
        mfc=PAPER, mec=INK, mew=1.0, zorder=7)

# Innermost square, far corner on r_mid.
inner_pts, inner_ctr, inner_touch = place_inner(INNER_REAL_ANG)
real_square(ax, inner_pts, "inner_sq", hatch=HATCH_INNER)
central_disk(ax, inner_ctr, e, ghost=False)
ax.plot(inner_ctr[0], inner_ctr[1], marker="o", ms=3.0,
        mfc=PAPER, mec=INK, mew=1.0, zorder=7)

# Origin marker.
ax.plot(0, 0, marker="+", ms=12, mew=1.2, color=INK, zorder=11)


# ----------------------------------------------------------------------------
# DRAWING BODY EXTENT + RULER POSITION
#
# The ruler sits to the left of the drawing body.  RULER_PAD is computed
# from the longest ruler VALUE (names stack above so they don't add to
# the horizontal footprint), the font size, and the axes width in
# inches — so the labels always fit inside the drawing axes.
# ----------------------------------------------------------------------------
_cos = abs(np.cos(np.radians(OUTER_ROT)))
_sin = abs(np.sin(np.radians(OUTER_ROT)))
_sq_half   = (L_side / 2.0) * (_cos + _sin)
_ring_half = np.hypot(R_outer, ROT_ARROW_LEN)
DIM_EXTENT = SIDE_DIM_OFFSET + 0.55 + 0.55   # offset + tick + label gap
BODY_HALF  = max(_sq_half, _ring_half) + DIM_EXTENT

RULER_X_POS = -BODY_HALF - RULER_GAP

# ---- Ruler labels as (radius, name, value) triples -------------------------
_ruler_labels = [
    (0.0,        "",   "0"),
    (r1_target,  "r1", f"{r1_target:g}"),
    (r_mid,      "r2", f"{r_mid:.2f}"),
]

# ---- Horizontal fit: value width + name shift + name width -----------------
_val_n     = max(len(v) for _, _, v in _ruler_labels)
_name_n    = max(len(n) for _, n, _ in _ruler_labels)
_val_w_in  = _val_n * RULER_FSIZE * RULER_CHAR_W_FACTOR / 72.0
_name_w_in = _name_n * RULER_NAME_FSIZE * RULER_CHAR_W_FACTOR / 72.0
_ax_w_in    = DRAW_W * FIG_W
_data_per_in = (2.0 * BODY_HALF + RULER_GAP + 2.0) / _ax_w_in
_total_in = _val_w_in + (RULER_NAME_SHIFT + _name_w_in) / _data_per_in
RULER_PAD = RULER_ANCHOR_OFFSET + _total_in * _data_per_in + 0.50

cx_lo = RULER_X_POS - RULER_PAD
cx_hi =  BODY_HALF
cy_lo = -BODY_HALF
cy_hi =  BODY_HALF

A_box  = (DRAW_W * FIG_W) / (DRAW_H * FIG_H)
data_w = cx_hi - cx_lo
data_h = cy_hi - cy_lo

# Expand whichever axis needs it so the data box matches the axes box.
if data_w / data_h > A_box:
    new_h = data_w / A_box
    mid   = 0.5 * (cy_lo + cy_hi)
    cy_lo = mid - new_h / 2
    cy_hi = mid + new_h / 2
else:
    new_w = data_h * A_box
    mid   = 0.5 * (cx_lo + cx_hi)
    cx_lo = mid - new_w / 2
    cx_hi = mid + new_w / 2

ax.set_xlim(cx_lo, cx_hi)
ax.set_ylim(cy_lo, cy_hi)

# ---- Vertical offset for the stacked name, in data units -------------------
_data_h_cm       = cy_hi - cy_lo
_scale_cm_per_in = _data_h_cm / (DRAW_H * FIG_H)
_value_line_h_in = RULER_FSIZE * 1.2 / 72.0
_name_line_h_in  = RULER_NAME_FSIZE * 1.2 / 72.0
_name_offset_cm  = (
    (_value_line_h_in + _name_line_h_in) / 2.0 + 0.08
) * _scale_cm_per_in

# Faint radial guide along the +x axis.
ax.plot([RULER_X_POS, BODY_HALF * 0.95], [0, 0],
        color=GHOST, lw=0.6, ls=(0, (2, 3)), zorder=0)

draw_vertical_scale_ruler(
    ax,
    x_pos=RULER_X_POS,
    radii_labels=_ruler_labels,
    name_offset_cm=_name_offset_cm,
)

ax.set_xticks([]); ax.set_yticks([])
for sp in ax.spines.values():
    sp.set_visible(True); sp.set_color(INK); sp.set_linewidth(0.9)


# ============================================================================
# FORMULA STRIP
#
# One framed axes with two stacked cells.  Upper cell = general form,
# lower cell = solved form.  A faint dashed rule separates them.
# ============================================================================
ax_form = fig.add_axes([FORM_L, FORM_B, FORM_W, FORM_H])
ax_form.set_facecolor(PAPER)
ax_form.set_xticks([]); ax_form.set_yticks([])
ax_form.set_xlim(0, 1); ax_form.set_ylim(0, 1)
for sp in ax_form.spines.values():
    sp.set_visible(True); sp.set_color(INK); sp.set_linewidth(0.9)

# Split the strip into two cells with a small gap.
form_gap_frac  = FORM_GAP / FORM_H
form_cell_frac = FORM_CELL_H / FORM_H

upper_center = (1.0 + form_cell_frac + form_gap_frac) / 2.0
lower_center = form_cell_frac / 2.0

# Separator rule through the middle of the gap.
ax_form.plot([0.0, 1.0],
             [form_cell_frac + form_gap_frac / 2.0] * 2,
             color=INK_SOFT, lw=0.6, ls=(0, (2, 3)),
             transform=ax_form.transAxes, clip_on=False, zorder=1)

ax_form.text(FORM_X_PAD, upper_center, general,
             fontsize=FORM_FSIZE, family=FONT_MONO,
             va="center", ha="left", color=INK, linespacing=1.40,
             transform=ax_form.transAxes)
ax_form.text(FORM_X_PAD, lower_center, solved,
             fontsize=FORM_FSIZE, family=FONT_MONO,
             va="center", ha="left", color=INK, linespacing=1.40,
             transform=ax_form.transAxes)


# ============================================================================
# LEGEND
#
# Small helper class that lays out rows of (swatch, label, sublabel) with
# automatic text wrapping.  Each entry provides its own swatch draw
# callback — line sample, arrow sample, or hatched patch sample — so the
# legend visually matches the corresponding element in the drawing.
# ============================================================================
class Legend:
    """
    Vertical legend laid out inside an axes whose coordinates are 0..1.

    Parameters
    ----------
    ax        : the axes to draw into
    avail_in  : available horizontal space in inches (for text wrapping)
    top       : starting y in axes fraction
    """
    def __init__(self, ax, avail_in, top=0.995):
        self.ax = ax
        self.avail_in = avail_in
        self.y = top
        self.x_sw_l  = 0.02      # swatch left
        self.x_sw_r  = 0.13      # swatch right
        self.x_text  = 0.16      # text left
        self.line_h  = 0.030     # per-line height
        self.pad     = 0.018     # padding inside a row
        self.size_l  = 12.5      # label size
        self.size_s  = 10.5      # sublabel size

    def _txt(self, x, y, txt, size, color=INK, weight="normal"):
        """Write one line of monospace text at axes-fraction (x, y)."""
        self.ax.text(x, y, txt, transform=self.ax.transAxes,
                     va="center", ha="left", fontsize=size,
                     family=FONT_MONO, color=color, weight=weight)

    def _wrap(self, txt, size):
        """Wrap `txt` to the legend's available width at the given size."""
        if not txt:
            return []
        char_w = size * 0.60 / 72.0
        max_chars = max(6, int(self.avail_in / char_w))
        return textwrap.wrap(txt, width=max_chars)

    def _row(self, draw_swatch, label, sub, size_l=None, size_s=None):
        """
        Emit a legend row.  The row's height is computed from how many
        wrapped lines the label and sublabel need.  `draw_swatch(y)`
        is called with the row's vertical center.
        """
        sl = size_l if size_l is not None else self.size_l
        ss = size_s if size_s is not None else self.size_s
        lab_lines = self._wrap(label, sl)
        sub_lines = self._wrap(sub, ss)
        n = len(lab_lines) + len(sub_lines)
        H = self.pad + n * self.line_h
        y_top = self.y
        y_mid = y_top - H / 2
        draw_swatch(y_mid)
        y_cursor = y_top - self.pad / 2
        for line in lab_lines:
            y_cursor -= self.line_h / 2
            self._txt(self.x_text, y_cursor, line, sl)
            y_cursor -= self.line_h / 2
        for line in sub_lines:
            y_cursor -= self.line_h / 2
            self._txt(self.x_text, y_cursor, line, ss, INK_SOFT)
            y_cursor -= self.line_h / 2
        self.y -= H

    def header(self, txt):
        """Section header with a rule underneath."""
        H = 0.060
        y_mid = self.y - H / 2
        self._txt(0.0, y_mid, txt, 15, weight="bold")
        rule_y = self.y - H + 0.008
        self.ax.plot([0.0, 1.0], [rule_y, rule_y],
                     transform=self.ax.transAxes, color=INK, lw=1.0)
        self.y -= H

    def separator(self):
        """Faint dashed separator between legend groups."""
        H = 0.018
        y_mid = self.y - H / 2
        self.ax.plot([self.x_sw_l, 0.98], [y_mid, y_mid],
                     transform=self.ax.transAxes,
                     color=INK_SOFT, lw=0.6, ls=(0, (2, 3)))
        self.y -= H

    def line_entry(self, key, label, sub=None, ghost=False):
        """
        Legend row whose swatch is a horizontal line drawn in STYLE[key].
        `ghost=True` renders the line at ghost weight.
        """
        def swatch(y):
            st = STYLE[key]
            lw = st["lw"] * (GHOST_LW_FACTOR if ghost else 1.0)
            tone = GHOST if ghost else INK
            self.ax.add_line(Line2D([self.x_sw_l, self.x_sw_r], [y, y],
                                    transform=self.ax.transAxes,
                                    color=tone, lw=lw, ls=st["ls"]))
        self._row(swatch, label, sub)

    def arrow_entry(self, label, sub=None):
        """Legend row whose swatch is a tangential-arrow sample."""
        def swatch(y):
            self.ax.annotate("",
                             xy=(self.x_sw_r, y), xytext=(self.x_sw_l, y),
                             xycoords=self.ax.transAxes,
                             textcoords=self.ax.transAxes,
                             arrowprops=dict(arrowstyle="->",
                                             color=ARROW_TONE,
                                             lw=1.6,
                                             mutation_scale=ROT_ARROW_HEAD,
                                             shrinkA=0, shrinkB=0))
        self._row(swatch, label, sub)

    def hatch_entry(self, hatch, label, sub=None,
                    ghost=False, round_shape=False):
        """
        Legend row whose swatch is a hatched patch.  `round_shape=True`
        draws a circle (for the central disks); otherwise a rectangle.
        """
        def swatch(y):
            tone = GHOST if ghost else INK
            lw = 0.6 if ghost else 0.9
            if round_shape:
                self.ax.add_patch(Circle(
                    (self.x_sw_l + 0.028, y), 0.021,
                    transform=self.ax.transAxes,
                    fill=True, fc="none", hatch=hatch, ec=tone, lw=lw))
            else:
                self.ax.add_patch(Rectangle(
                    (self.x_sw_l, y - 0.018), 0.11, 0.036,
                    transform=self.ax.transAxes,
                    fill=True, fc="none", hatch=hatch, ec=tone, lw=lw))
        self._row(swatch, label, sub)


# ---- legend instantiation --------------------------------------------------
_leg_w_in = LEG_W * FIG_W
_avail_in = _leg_w_in * (1.0 - 0.16 - 0.02)   # swatch column + right pad

leg = Legend(lg, avail_in=_avail_in)
leg.header("LEGEND")

leg.line_entry("outer_sq",  "outer square",   f"L = {L_side:.2f} cm")
leg.arrow_entry("outer boundary",
                f"R = {R_outer:.2f} cm  (ring of rotation arrows)")
leg.line_entry("mid_sq",    "middle square",  f"s={s:g}, c={c:g} cm")
leg.line_entry("mid_cir",   "middle circle",  f"r2 = {r_mid:.2f} cm")
leg.line_entry("inner_sq",  "innermost sq.",  f"s={s:g}, c={c:g} cm")
leg.line_entry("inner_cir", "inner circle",   f"r1 = {r1_target:g} cm")

leg.separator()
leg.hatch_entry(HATCH_DISK, "central disks",
                sub=f"e = {e:g} cm",
                round_shape=True)

leg.separator()
leg.hatch_entry(HATCH_MID,   "middle square fill")
leg.hatch_entry(HATCH_INNER, "innermost square fill")


# ============================================================================
# OUTPUT
# ============================================================================
with PdfPages("nested_squares.pdf") as pdf:
    pdf.savefig(fig, facecolor=PAPER)
plt.close(fig)

# ---- console summary -------------------------------------------------------
print(f"a         = {a:.4f} cm")
print(f"e         = {e:.4f} cm   (both disks)")
print(f"rho_inner = {rho_inner:.4f} cm   (r1 - e)")
print(f"r_mid     = {r_mid:.4f} cm")
print(f"L_side    = {L_side:.4f} cm")
print(f"R_outer   = {R_outer:.4f} cm")
print()
print("--- layout margins ---")
print(f"MARGIN          = {MARGIN:.4f}")
print(f"HEADER_L        = {HEADER_L:.4f}")
print(f"HEADER_R        = {HEADER_R:.4f}")
print(f"HEADER_TITLE_Y  = {HEADER_TITLE_Y:.4f}")
print(f"HEADER_RULE_Y   = {HEADER_RULE_Y:.4f}")
print(f"CONTENT_L       = {CONTENT_L:.4f}")
print(f"CONTENT_R       = {CONTENT_R:.4f}")
print(f"CONTENT_T       = {CONTENT_T:.4f}")
print(f"CONTENT_B       = {CONTENT_B:.4f}")
print(f"left  inset     = {CONTENT_L:.4f}")
print(f"right inset     = {1.0 - CONTENT_R:.4f}")
print(f"top   inset     = {1.0 - HEADER_TITLE_Y:.4f}")
print(f"bot   inset     = {CONTENT_B:.4f}")
print()
print("--- geometry alignment ---")
print(f"DRAW  rect: x=[{DRAW_L:.4f}, {DRAW_L + DRAW_W:.4f}]  w={DRAW_W:.4f}")
print(f"FORM  rect: x=[{FORM_L:.4f}, {FORM_L + FORM_W:.4f}]  w={FORM_W:.4f}")
print(f"aligned? {abs((DRAW_L + DRAW_W) - (FORM_L + FORM_W)) < 1e-12}")
print()
print(f"FORM_CELL_H    = {FORM_CELL_H:.4f}")
print(f"FORM_MAX_CHARS = {FORM_MAX_CHARS}")
print()
print("--- general (wrapped) ---")
print(general)
print("--- solved (wrapped) ---")
print(solved)
print()
print(f"BODY_HALF        = {BODY_HALF:.4f}")
print(f"RULER_GAP        = {RULER_GAP:.4f}")
print(f"RULER_PAD        = {RULER_PAD:.4f}")
print(f"RULER_X_POS      = {RULER_X_POS:.4f}")
print(f"RULER_NAME_SHIFT = {RULER_NAME_SHIFT:.4f} cm")
print(f"RULER_NAME_OFF   = {_name_offset_cm:.4f} cm")
print(f"xlim = ({cx_lo:.3f}, {cx_hi:.3f})")
print(f"ylim = ({cy_lo:.3f}, {cy_hi:.3f})")
print(f"legend text budget = {_avail_in:.2f} in")
print("Wrote nested_squares.pdf")
