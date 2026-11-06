"""The "how the buttons work" picture (renders/13_how_the_buttons_work.png), cut straight
from the case model at true scale. build_case.py calls draw() after building the parts."""
import numpy as np

INK, MUTED, ACCENT = "#1f2328", "#5b6470", "#d9480f"
C_SHELL, C_SHELL_DARK = "#e9e4da", "#b9b0a2"
C_LID, C_LID_DARK = "#d9822b", "#a85f1a"
C_SWITCH, C_BATTERY = "#3b3b3b", "#9aa4b0"
C_GLASS, C_BOARD, C_BACKPARTS = "#15171c", "#1d4fa8", "#3a6fd0"
EDGE = "#4a4540"
ROT_X = np.array([[0, 0, 1, 0], [0, 1, 0, 0], [-1, 0, 0, 0]], float)   # (x, y, z) -> (z, y, -x)
ROT_Y = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0]], float)   # (x, y, z) -> (x, -z, y)


def _polys(cs):
    return [np.asarray(p, float) for p in cs.to_polygons() if len(p) >= 3]


def cut_z(m, z):
    """Cut parallel to the screen, seen from the front: (x, y)."""
    return _polys(m.slice(z))


def cut_x(m, x):
    """Cut across the device, seen from the right end: (z, y), front on the left."""
    return _polys(m.transform(ROT_X).slice(-x))


def cut_y(m, y):
    """Cut parallel to the top edge, seen from above: (x, z), front at the bottom."""
    return [p * np.array([1.0, -1.0]) for p in _polys(m.transform(ROT_Y).slice(y))]


def from_above(m):
    """Outline seen from above: (x, z), front at the bottom."""
    return [p * np.array([1.0, -1.0]) for p in _polys(m.transform(ROT_Y).project())]


def draw(bc, shell, lid, parts, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyArrowPatch, PathPatch, Rectangle
    from matplotlib.path import Path as MPath

    def fill(ax, ps, color, edge=EDGE, lw=0.7, ls="-", z=2, face=True):
        if not ps:
            return
        verts, codes = [], []
        for p in ps:
            verts.extend(p.tolist())
            verts.append(p[0].tolist())
            codes.extend([MPath.MOVETO] + [MPath.LINETO] * (len(p) - 1) + [MPath.CLOSEPOLY])
        ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color if face else "none", edgecolor=edge,
                               lw=lw, linestyle=ls, zorder=z, joinstyle="round"))

    def label(ax, text, xy, xytext, ha="left", size=10.5, dot=True):
        ax.annotate(text, xy=xy, xytext=xytext, ha=ha, va="center", fontsize=size, color=INK, zorder=20,
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.9, shrinkA=3, shrinkB=0))
        if dot:
            ax.plot(*xy, "o", ms=3.2, color=MUTED, zorder=21)

    def note(ax, text, xy, ha="left", size=10.5, color=INK, weight="normal"):
        ax.text(*xy, text, ha=ha, va="center", fontsize=size, color=color, fontweight=weight, zorder=20)

    def scale_bar(ax, x, y, length=1.0):
        ax.plot([x, x + length], [y, y], color=INK, lw=2, solid_capstyle="butt", zorder=20)
        ax.text(x + length / 2, y - 0.3, f"{length:g} mm", ha="center", va="top", fontsize=9, color=MUTED)

    def setup(ax, title, xlim, ylim):
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(title, loc="left", fontsize=13.5, fontweight="bold", color=INK, pad=6)

    xa = bc.BUTTON_X[0]
    r_stem, r_hole = bc.STEM_D / 2, bc.STEM_HOLE / 2
    stem_out = bc.STEM_TOP - bc.Y_OUT
    wall_mid = (bc.Y_IN + bc.Y_OUT) / 2
    pressed_top = bc.STEM_TOP - bc.SWITCH_TRAVEL

    fig = plt.figure(figsize=(12, 13.2), dpi=130, facecolor="white")
    gs = fig.add_gridspec(3, 2, height_ratios=[1.0, 1.0, 0.74], hspace=0.12, wspace=0.04,
                          left=0.015, right=0.985, top=0.9, bottom=0.01)
    fig.text(0.015, 0.988, "How the buttons work", fontsize=21, fontweight="bold", color=INK, va="top")
    fig.text(0.015, 0.956, f"Cut straight from the case model, at true scale, with 6 × 6 × {bc.SWITCH_H:g} mm "
             "switches. Button A is shown; B is the same.\n"
             "Beige = front shell,  orange = back cover,  dark gray = switch.",
             fontsize=12, color=MUTED, va="top", linespacing=1.5)

    # 1. The top edge, from above
    ax = fig.add_subplot(gs[0, :])
    setup(ax, "1.  The top edge, seen from above", (-33, 33), (-4.6, 18.6))
    fill(ax, from_above(shell ^ bc.box(-40, 40, 10.0, 40, -5, 25)), C_SHELL_DARK, lw=0.9)
    fill(ax, from_above(lid ^ bc.box(-40, 40, bc.Y_IN - 0.01, 40, -5, 25)), C_LID_DARK, lw=0.9)
    y_top = bc.Y_OUT - 0.15
    fill(ax, cut_y(shell, y_top), C_SHELL, edge="none", z=3)
    fill(ax, cut_y(lid, y_top), C_LID, edge="none", z=3)
    for i, x in enumerate(bc.BUTTON_X):
        stem = parts[f"switch {'AB'[i]}"] ^ bc.box(-40, 40, bc.Y_OUT, 40, -5, 25)
        fill(ax, from_above(stem), C_SWITCH, z=4)
        label(ax, f"{'AB'[i]}: press here", (x, bc.Z_SWITCH - r_stem), (x, -3.2), ha="center", size=11, dot=False)
    key_x = xa + bc.KEY_W / 2 - 0.9
    label(ax, "key (back cover)", (key_x, (bc.Z_SWITCH + bc.Z_LID) / 2 + 0.6), (key_x, 17.0), ha="center",
          size=10)
    label(ax, "back cover", (-20, (bc.Z_LID + bc.DEPTH) / 2), (-20, 17.0), ha="center", size=10)
    note(ax, "front (screen side)", (-31.5, -3.2), color=MUTED)
    note(ax, "USB-C end", (31.5, -3.2), ha="right", color=MUTED)

    # 2 and 3. Cut along the top edge through button A, just in front of the key
    zc = bc.Z_SWITCH - 0.25
    shell_z, lid_z = cut_z(shell, zc), cut_z(lid, zc)
    switch_z, battery_z = cut_z(parts["switch A"], zc), cut_z(parts["battery"], zc)
    y0, y1 = bc.SWITCH_BOTTOM - 4.6, bc.STEM_TOP + 1.9
    xlim = (xa - 14.9, xa + 5.1)
    tx, top_row = xlim[0] + 0.2, bc.STEM_TOP + 1.1
    bat_x0, bat_x1, _, bat_y1 = bc.BAT_BOX[:4]

    ax = fig.add_subplot(gs[1, 0])
    setup(ax, "2.  At rest (cut along the top edge, seen from the front)", xlim, (y0, y1))
    fill(ax, battery_z, C_BATTERY)
    fill(ax, lid_z, C_LID)
    fill(ax, switch_z, C_SWITCH)
    fill(ax, shell_z, C_SHELL)
    if battery_z and bat_y1 > y0:
        note(ax, "battery", ((max(bat_x0, xlim[0]) + bat_x1) / 2, (y0 + bat_y1) / 2), ha="center", size=9.5)
    note(ax, "top wall (shell)", (xa - 9.0, wall_mid), ha="center", size=10)
    label(ax, f"{(bc.STEM_HOLE - bc.STEM_D) / 2:.1f} mm gap round the stem", (xa - r_stem - 0.15, wall_mid),
          (tx, top_row + 0.2))
    label(ax, f"stem: {stem_out:.1f} mm out", (xa + r_stem, bc.STEM_TOP - 0.3), (xa + 2.6, top_row + 0.2),
          ha="center")
    label(ax, "holder wall", (xa - bc.SWITCH_BODY / 2 - 0.7, (bc.SWITCH_BOTTOM + bc.Y_IN) / 2 + 0.3),
          (tx, bc.SWITCH_TOP - 0.5))
    label(ax, "6 x 6 switch body", (xa - 2.2, bc.SWITCH_BOTTOM + 1.6), (tx, bc.SWITCH_BOTTOM + 1.6))
    label(ax, "ledge takes the push", (xa - 1.2, bc.SWITCH_BOTTOM - 1.0), (tx, bc.SWITCH_BOTTOM - 0.8))
    label(ax, "legs: wires to D2 and GND", (xa - 3.25, bc.SWITCH_BOTTOM - 2.6), (tx, bc.SWITCH_BOTTOM - 2.3))
    scale_bar(ax, xa + 4.0, y0 + 1.2)

    ax = fig.add_subplot(gs[1, 1])
    setup(ax, "3.  Pressed", xlim, (y0, y1))
    fill(ax, battery_z, C_BATTERY)
    fill(ax, lid_z, C_LID)
    pushed = []
    for p in switch_z:
        q = p.copy()
        q[q[:, 1] > bc.STEM_TOP - 0.01, 1] -= bc.SWITCH_TRAVEL
        pushed.append(q)
    fill(ax, pushed, C_SWITCH)
    fill(ax, shell_z, C_SHELL)
    chord = float(np.sqrt(max(r_stem ** 2 - (bc.Z_SWITCH - zc) ** 2, 0.0)))   # stem width in this cut
    ax.add_patch(Rectangle((xa - chord, pressed_top), 2 * chord, bc.SWITCH_TRAVEL, fc="none", ec=MUTED,
                           lw=0.9, ls=(0, (3, 2)), zorder=6))
    ax.add_patch(FancyArrowPatch((xa, y1 - 0.15), (xa, pressed_top + 0.08), arrowstyle="-|>",
                                 mutation_scale=22, lw=3, color=ACCENT, zorder=15))
    note(ax, "push", (xa + 0.45, y1 - 0.6), size=12, color=ACCENT, weight="bold")
    label(ax, f"the stem goes in {bc.SWITCH_TRAVEL:g} mm: click\n(dashed line = at rest)",
          (xa - r_stem, pressed_top - 0.3), (tx, bc.STEM_TOP + 0.2))
    note(ax, "Nothing printed bends: only the\nswitch's own stem moves.", (tx, bc.SWITCH_TOP - 1.3))
    label(ax, "the ledge holds the switch", (xa - 1.2, bc.SWITCH_BOTTOM - 1.0), (tx, bc.SWITCH_BOTTOM - 0.5))
    note(ax, "Let go: the switch's spring\npushes the stem back out.", (tx, bc.SWITCH_BOTTOM - 1.9))
    scale_bar(ax, xa + 4.0, y0 + 1.2)

    # 4. Cut across button A, seen from the right end
    sub = gs[2, :].subgridspec(1, 2, width_ratios=[1.32, 1], wspace=0.04)
    ax = fig.add_subplot(sub[0, 0])
    ya, yb = bc.SWITCH_BOTTOM - 2.8, bc.STEM_TOP + 1.9
    setup(ax, "4.  Cut across button A, seen from the right end", (-6.6, 21.0), (ya, yb))
    for name, color in (("glass", C_GLASS), ("display board", C_BOARD), ("display back parts", C_BACKPARTS)):
        fill(ax, cut_x(parts[name], xa), color)
    fill(ax, cut_x(lid, xa), C_LID)
    fill(ax, cut_x(parts["switch A"], xa), C_SWITCH)
    fill(ax, cut_x(shell, xa), C_SHELL)
    zs, top_row = bc.Z_SWITCH, bc.STEM_TOP + 1.1
    label(ax, "front half of the hole (shell)", (zs - r_hole - 0.6, wall_mid), (3.4, top_row), ha="center")
    label(ax, "stem", (zs + 0.8, bc.STEM_TOP - 0.5), (15.6, bc.STEM_TOP - 0.3))
    label(ax, "back half: key on the\nback cover", (zs + r_hole + 0.45, wall_mid), (15.6, wall_mid))
    label(ax, "switch", (zs + 1.5, bc.SWITCH_TOP - 1.3), (15.6, bc.SWITCH_TOP - 1.3))
    label(ax, "back cover", ((bc.Z_LID + bc.DEPTH) / 2, bc.SWITCH_BOTTOM + 0.4), (15.6, bc.SWITCH_BOTTOM + 0.4))
    label(ax, "ledge", (zs + 1.5, bc.SWITCH_BOTTOM - 1.0), (15.6, bc.SWITCH_BOTTOM - 1.4))
    label(ax, "screen", (bc.Z_GLASS + bc.GLASS_T / 2, bc.SWITCH_TOP - 2.6), (-0.6, bc.SWITCH_TOP - 1.4),
          ha="right")
    label(ax, "display\nboard", (bc.Z_PCB + bc.PCB_T / 2, bc.SWITCH_BOTTOM + 0.2), (-0.6, bc.SWITCH_BOTTOM - 0.4),
          ha="right")
    note(ax, "front", (-6.4, ya + 0.5), color=MUTED)
    note(ax, "back", (20.8, ya + 0.5), ha="right", color=MUTED)

    # 5. Wiring
    ax = fig.add_subplot(sub[0, 1])
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.4)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("5.  Wiring (button B is the same on D4)", loc="left", fontsize=13.5, fontweight="bold",
                 color=INK, pad=6)
    ax.add_patch(Rectangle((0.3, 2.2), 3.0, 3.4, fc="#eef0f3", ec=MUTED, lw=1.2))
    ax.text(0.5, 5.35, "XIAO", fontsize=11, fontweight="bold", color=INK, va="top")
    wire = dict(color=INK, lw=1.8, solid_capstyle="round", zorder=5)
    ax.plot([2.3, 2.3], [5.0, 4.6], **wire)
    zig_y = np.linspace(4.6, 3.4, 9)
    zig_x = 2.3 + np.array([0, .18, -.18, .18, -.18, .18, -.18, .18, 0])
    ax.plot(zig_x, zig_y, **wire)
    ax.plot([2.3, 2.3, 3.3], [3.4, 3.0, 3.0], **wire)
    ax.text(2.3, 5.12, "3.3 V", ha="center", va="bottom", fontsize=9.5, color=MUTED)
    ax.text(1.95, 4.0, "built-in\npull-up", ha="right", va="center", fontsize=9, color=MUTED)
    ax.text(3.15, 3.12, "D2", ha="right", va="bottom", fontsize=10.5, fontweight="bold", color=INK)
    ax.text(3.15, 2.62, "GND", ha="right", va="bottom", fontsize=10.5, fontweight="bold", color=INK)
    ax.plot([3.3, 5.2], [3.0, 3.0], **wire)
    ax.plot([3.3, 8.6, 8.6, 7.4], [2.55, 2.55, 3.0, 3.0], **wire)
    for x in (5.3, 7.3):
        ax.add_patch(Circle((x, 3.0), 0.1, fc="white", ec=INK, lw=1.6, zorder=6))
    ax.plot([5.15, 7.45], [3.55, 3.55], color=INK, lw=2.2, zorder=6)
    ax.plot([6.3, 6.3], [3.55, 4.15], color=INK, lw=1.8, zorder=6)
    ax.plot([6.0, 6.6], [4.15, 4.15], color=INK, lw=1.8, zorder=6)
    ax.text(6.3, 4.35, "switch A", ha="center", va="bottom", fontsize=10.5, color=INK)
    ax.text(0.3, 1.75, "Not pressed: the pull-up holds D2 at 3.3 V (HIGH).\n"
                       "Pressed: the switch joins D2 to GND (LOW).\n"
                       "The firmware watches for LOW. No resistors needed.",
            fontsize=10.2, color=INK, va="top", linespacing=1.5)

    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
