"""Weather display case: parametric 3D model, fit checks, STL files and pictures.

Run:   python build_case.py          (needs: pip install manifold3d numpy matplotlib)
Makes: stl/*.stl (ready to print, already turned the right way up),
       renders/*.png, viewer_data.json (for the 3D viewer page)

Parts:
  shell      front + side walls. Holds the display. The two button switches' stems
             poke out through round holes in its top wall; you press them directly.
  lid        the back. Carries everything else: XIAO, battery, button switches,
             magnetic charging connector. Closes onto the shell like a book and is
             held by 3 small M2 screws in the ends. Two small keys on it fill the
             back halves of the button holes.
  dock       optional desk stand with the other half of the magnetic connector;
             holds the display tilted back 15 degrees.
  fit tests  two small prints to check the fit before printing everything.

All sizes in millimeters. Coordinates: x to the right and y up (looking at the
screen), z from the front face (0) toward the back. Origin = center of the inside.
That is a left-handed system; STL files (and every slicer) are right-handed, so
everything is mirrored in x when it is written out (see physical()). The pictures
are drawn the same way, so they show the real, printed parts.

MEASURE before printing. Values marked "estimate" come from a photo or a typical
part, not from your actual part.
"""
import base64
import json
import math
import sys
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, FillRule, JoinType, Manifold

HERE = Path(__file__).resolve().parent
STL_DIR, IMG_DIR = HERE / "stl", HERE / "renders"
SEG = 40

# =====================================================================
# PARTS (measure these)
# =====================================================================
# Display: Estardyn 2.0" ST7789 (seller's manual: board 58.04 x 37.5, glass
# 47.8 x 34.6 x 2.03, lit area 40.8 x 30.6). Offsets measured from your photo.
PCB_L, PCB_H, PCB_T = 58.04, 37.5, 1.6
GLASS_L, GLASS_H, GLASS_T = 47.8, 34.6, 2.03
ACTIVE_L, ACTIVE_H = 40.8, 30.6
PCB_TO_ACTIVE = 6.8          # estimate (photo): board's left edge to the lit area's left edge
GLASS_TO_ACTIVE = 2.5        # estimate (photo): glass left edge to the lit area
GLASS_TAPE = 0.15            # tape between glass and board
BACK_PARTS_T = 1.0           # estimate: small parts on the back of the display board
HEADER_STUB_T = 2.0          # header pins CUT SHORT to 1.5 mm + solder, on the board's right end
HEADER_ZONE = (6.0, 19.0)    # header area at the right end: width from the edge, height

# Seeed XIAO ESP32-S3 (21 x 17.8 mm; layout from Seeed's pinout pictures). Use the
# board WITHOUT pin headers. Its parts side faces the back cover.
XIAO_L, XIAO_W, XIAO_T = 21.0, 17.8, 1.2
USB_W, USB_H, USB_L, USB_OVERHANG = 8.94, 3.2, 7.35, 1.6   # USB-C socket (overhang: estimate)
CAN = (6.3, 17.0, 2.6, 14.9, 2.2)    # metal can: from/to along the board, from/to across, height
UFL_UV = (19.0, 4.2)                 # antenna socket: along / across the board
R_UV, B_UV = (1.2, 3.0), (1.2, 14.7)           # reset and boot buttons
CHG_LED_UV, USER_LED_UV = (3.3, 3.0), (3.3, 14.7)
BAT_PADS_UV = ((8.4, 4.4), (10.4, 4.4))         # BAT- and BAT+ pads, on the underside
PAD_JOINT_T = 1.2                    # solder joint + wire on the underside pads

# 503035 LiPo: 5 x 30 x 35 nominal; this is the room allowed for it
BATTERY = (35.5, 30.5, 5.5)          # length (x), height (y), thickness incl. swelling
BATTERY_TAPE = 0.4                   # foam tape between battery and back cover

# 6 x 6 tactile switches (2 legs) with a tall stem. The stem pokes out through a hole in
# the top wall, so you press the switch itself and nothing printed has to bend.
# 6 x 6 x 7 = a 3.5 mm body plus a 3.5 mm stem. Heights from 7 to 10 fit.
SWITCH_BODY, SWITCH_BODY_H, STEM_D = 6.0, 3.5, 3.5
SWITCH_H = 7.0                       # total height including the stem (MEASURE)
SWITCH_TRAVEL = 0.25
LEG_W, LEG_T, LEG_L = 0.7, 0.3, 3.5  # each leg: width, thickness, length below the body

# 2-pin magnetic connector (estimate, MEASURE): face width x thickness, and depth
MAG_W, MAG_T, MAG_D = 9.0, 4.0, 6.0

# M2 self-tapping screws, flat (countersunk) head, 5-6 mm long
SCREW_D, SCREW_HEAD_D, SCREW_PILOT_D, SCREW_LEN = 2.0, 3.8, 1.7, 5.0

# =====================================================================
# CASE
# =====================================================================
WALL = 1.8
FRONT = 2.0                # front plate; the glass sits in a 0.8 mm pocket in it
GLASS_POCKET = 0.8
CORNER_R = 4.0
INNER_R = 1.0              # small, so the display board's square corners fit
FRONT_CHAMFER, BACK_CHAMFER = 0.8, 0.5
CLEAR = 0.4                # room around the display board
GLASS_CLEAR = 0.2          # room around the glass in its pocket
WINDOW_MARGIN = 0.4        # window bigger than the lit area by this much
WINDOW_BEVEL = 1.2
LID_T = 1.6
FIT = 0.15                 # gap between the back cover's tabs/lip and the walls
LIP_W = 1.0
PRESS_GAP = 0.4            # back cover tabs stop this far behind the display board (add foam)
STEM_OUT = 1.5             # how far the button stems stand out of the top edge
STEM_HOLE = STEM_D + 0.6   # hole for each stem: 0.3 mm all round
KEY_W = 6.0                # width of the back cover's keys that finish the button holes
BUTTON_X = (11.5, 21.0)
MAG_X = 17.0
DOCK_TILT = 15.0
LABELS = True

# =====================================================================
# LAYOUT (derived)
# =====================================================================
IN_L, IN_H = PCB_L + 2 * CLEAR, PCB_H + 2 * CLEAR
X_IN, Y_IN = IN_L / 2, IN_H / 2
OUT_L, OUT_H = IN_L + 2 * WALL, IN_H + 2 * WALL
X_OUT, Y_OUT = OUT_L / 2, OUT_H / 2

Z_GLASS = FRONT - GLASS_POCKET
Z_PCB = Z_GLASS + GLASS_T + GLASS_TAPE
Z_PCB_BACK = Z_PCB + PCB_T
Z_BACK_PARTS = Z_PCB_BACK + BACK_PARTS_T
Z_HEADER = Z_PCB_BACK + HEADER_STUB_T
XIAO_BACK_MIN = Z_HEADER + PAD_JOINT_T + 0.2
Z_LID = math.ceil((XIAO_BACK_MIN + XIAO_T + USB_H) * 10 - 1e-6) / 10   # USB-C touches the lid
DEPTH = Z_LID + LID_T
Z_XIAO = Z_LID - USB_H - XIAO_T            # XIAO underside
Z_XIAO_TOP = Z_XIAO + XIAO_T
Z_TAB = Z_PCB_BACK + PRESS_GAP              # front face of the tabs that hold the display

ACTIVE_X0 = -PCB_L / 2 + PCB_TO_ACTIVE
ACTIVE_CX = ACTIVE_X0 + ACTIVE_L / 2
GLASS_X0 = ACTIVE_X0 - GLASS_TO_ACTIVE
GLASS_CX = GLASS_X0 + GLASS_L / 2

XIAO_X1 = X_IN - 0.25                       # USB-C end
XIAO_X0 = XIAO_X1 - XIAO_L
XIAO_CY = -1.5
XIAO_Y1 = XIAO_CY + XIAO_W / 2              # top edge: the 5V, GND, 3V3, D10, D9, D8, D7 pads
XIAO_Y0 = XIAO_CY - XIAO_W / 2              # bottom edge: the D0..D6 pads
USB_Z = Z_XIAO_TOP + USB_H / 2


def xiao_xy(u, v):
    """XIAO board position -> case x, y. u: along the board from the USB-C end; v: across
    from the D0..D6 edge (as on Seeed's pinout picture of the parts side). With the parts
    side facing the back cover and the USB-C pointing right, the D0..D6 edge is at the bottom."""
    return XIAO_X1 - u, XIAO_Y0 + v


BAT_X0 = -X_IN + 0.3
BAT_Y0 = -Y_IN + FIT + LIP_W + 0.2
BAT_Z1 = Z_LID - BATTERY_TAPE
BAT_BOX = (BAT_X0, BAT_X0 + BATTERY[0], BAT_Y0, BAT_Y0 + BATTERY[1], BAT_Z1 - BATTERY[2], BAT_Z1)

STEM_H = SWITCH_H - SWITCH_BODY_H
# The switch sits so its stem stands STEM_OUT out of the top edge. Lowered into the case
# (a tall stem), lips on its holder keep it from rising; otherwise the top wall does.
_SWITCH_TOP = Y_OUT + STEM_OUT - STEM_H
SWITCH_LIPS = _SWITCH_TOP <= Y_IN - FIT - 0.5
SWITCH_TOP = _SWITCH_TOP if SWITCH_LIPS else Y_IN - 0.2
SWITCH_BOTTOM = SWITCH_TOP - SWITCH_BODY_H
STEM_TOP = SWITCH_TOP + STEM_H
Z_SWITCH = Z_LID - 0.2 - SWITCH_BODY / 2

TAB_Y = (12.9, Y_IN - FIT)                  # screw tabs span this |y| range
SCREW_Y = (TAB_Y[0] + TAB_Y[1]) / 2
SCREW_Z = (Z_TAB + Z_LID) / 2
SCREWS = [(-1, SCREW_Y), (1, SCREW_Y), (1, -SCREW_Y)]   # (end: -1 left / 1 right, y)
TAB_X = 3.8                                 # tab length along x (screw thread length)

MAG_Z = Z_LID - 0.5 - MAG_T / 2
MAG_Y_IN = -Y_OUT + MAG_D                   # inner end of the connector

ANTENNA_ZONE = (-X_IN + FIT + TAB_X + 0.3, BUTTON_X[0] - 4.6, BAT_BOX[3] + 0.3, Y_IN - 0.3)


# =====================================================================
# SHAPES
# =====================================================================
def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def rbox(length, height, z0, z1, radius, cx=0.0, cy=0.0):
    """Box with rounded vertical edges, centered on (cx, cy)."""
    radius = max(0.05, min(radius, length / 2 - 0.05, height / 2 - 0.05))
    shape = CrossSection.square((length - 2 * radius, height - 2 * radius), True)
    shape = shape.offset(radius, JoinType.Round, 2.0, SEG)
    return Manifold.extrude(shape, z1 - z0).translate((cx, cy, z0))


def outer_shape(z0, z1, chamfer0=0.0, chamfer1=0.0, grow=0.0):
    """The device's outside between z0 and z1, with optional chamfered edges."""
    length, height, radius = OUT_L + 2 * grow, OUT_H + 2 * grow, CORNER_R + grow
    layers = []
    if chamfer0:
        layers.append(rbox(length - 2 * chamfer0, height - 2 * chamfer0, z0, z0 + 0.01, radius - chamfer0))
    layers.append(rbox(length, height, z0 + chamfer0, z1 - chamfer1, radius))
    if chamfer1:
        layers.append(rbox(length - 2 * chamfer1, height - 2 * chamfer1, z1 - 0.01, z1, radius - chamfer1))
    return Manifold.hull(sum(layers[1:], layers[0]))


def cyl(axis, d, x, y, z, length, center=True):
    m = Manifold.cylinder(length, d / 2, -1, SEG, center)
    if axis == "x":
        m = m.rotate((0, 90, 0))
    elif axis == "y":
        m = m.rotate((-90, 0, 0))
    return m.translate((x, y, z))


def text_shape(text, size, depth, x, y, z0, mirror=False):
    """Raised letters (for debossing labels), centered on (x, y)."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    path = TextPath((0, 0), text, size=size, prop=FontProperties(family="DejaVu Sans", weight="bold"))
    polys = [np.asarray(p)[:-1] for p in path.to_polygons() if len(p) > 3]
    allpts = np.concatenate(polys)
    mid = (allpts.min(0) + allpts.max(0)) / 2
    polys = [p - mid for p in polys]
    if mirror:
        polys = [p * np.array([-1, 1]) for p in polys]
    shape = CrossSection(polys, FillRule.EvenOdd)
    return Manifold.extrude(shape, depth).translate((x, y, z0))


# =====================================================================
# THE PARTS
# =====================================================================
def stem_hole(x):
    """Round hole through the top wall for the switch stem at x."""
    return cyl("y", STEM_HOLE, x, (Y_IN + Y_OUT) / 2, Z_SWITCH, WALL + 1)


def shell():
    s = outer_shape(0, Z_LID, FRONT_CHAMFER, 0)
    s -= rbox(IN_L, IN_H, FRONT, Z_LID + 1, INNER_R)
    # The glass sits in a pocket in the front plate, so the window lines up with the screen
    s -= box(GLASS_CX - GLASS_L / 2 - GLASS_CLEAR, GLASS_CX + GLASS_L / 2 + GLASS_CLEAR,
             -GLASS_H / 2 - GLASS_CLEAR, GLASS_H / 2 + GLASS_CLEAR, Z_GLASS, FRONT + 0.1)
    wl, wh = ACTIVE_L + 2 * WINDOW_MARGIN, ACTIVE_H + 2 * WINDOW_MARGIN
    window = Manifold.hull(rbox(wl, wh, Z_GLASS - 0.01, Z_GLASS, 0.6, ACTIVE_CX)
                           + rbox(wl + 2 * WINDOW_BEVEL, wh + 2 * WINDOW_BEVEL, -0.01, 0.0,
                                  0.6 + WINDOW_BEVEL, ACTIVE_CX))
    s -= window + rbox(wl, wh, -0.5, Z_GLASS + 0.1, 0.6, ACTIVE_CX)

    # USB-C: a slot open to the back edge (the XIAO comes in with the back cover),
    # with a wider recess outside for the plug's rubber end
    usb_hw = USB_W / 2 + 0.45
    s -= box(X_IN - 0.5, X_OUT + 1, XIAO_CY - usb_hw, XIAO_CY + usb_hw, USB_Z - USB_H / 2 - 0.4, Z_LID + 1)
    s -= usb_recess()
    # Magnetic connector: slot in the bottom wall, open to the back edge
    s -= box(MAG_X - MAG_W / 2 - 0.25, MAG_X + MAG_W / 2 + 0.25, -Y_OUT - 1, -Y_IN + 0.5,
             MAG_Z - MAG_T / 2 - 0.25, Z_LID + 1)
    # Screw holes (countersunk) in the end walls
    for end, y in SCREWS:
        x_out = end * X_OUT
        s -= cyl("x", SCREW_D + 0.3, end * (X_IN + WALL / 2), y, SCREW_Z, WALL + 2)
        cone = Manifold.cylinder(1.1, (SCREW_HEAD_D + 0.4) / 2, (SCREW_D + 0.3) / 2, SEG)
        cone = cone.rotate((0, 90, 0)) if end > 0 else cone.rotate((0, -90, 0))
        s -= cone.translate((x_out + end * 0.01, y, SCREW_Z))
    # Button holes: each switch's stem pokes out through the top wall. The switches come in
    # with the back cover, so the back half of each hole is a slot open to the back edge;
    # a key on the cover fills the slot and finishes the hole.
    for x in BUTTON_X:
        s -= box(x - KEY_W / 2, x + KEY_W / 2, Y_IN - 0.5, Y_OUT + 0.5, Z_SWITCH, Z_LID + 1)
        s -= stem_hole(x)
    # Fingernail notch to lift the back cover once the screws are out
    s -= box(-22, -15, -Y_OUT - 1, -Y_IN + 0.3, Z_LID - 0.7, Z_LID + 1)
    return s


def usb_recess():
    """Outside recess around the USB-C slot for the plug's rubber end (cut in shell and lid):
    a rounded rectangle 12.6 (y) x 7.0 (z), 0.6 mm deep into the end face."""
    shape = CrossSection.square((12.6 - 3.0, 7.0 - 3.0), True).offset(1.5, JoinType.Round, 2.0, SEG)
    solid = Manifold.extrude(shape, 1.6)                       # in (y, z), extruded along +x
    solid = solid.transform(np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float))
    return solid.translate((X_OUT - 0.6, XIAO_CY, USB_Z))


def lid():
    c = lid_inside() + outer_shape(Z_LID, DEPTH, 0, BACK_CHAMFER)
    # Keys: fill the button slots in the shell's top wall and finish the stem holes
    for x in BUTTON_X:
        key = box(x - KEY_W / 2 + FIT, x + KEY_W / 2 - FIT, Y_IN, Y_OUT, Z_SWITCH + FIT, Z_LID + 0.01)
        c += key - stem_hole(x)
    # Openings: USB-C, pinholes for the XIAO's R and B buttons, windows for its two lights
    c -= usb_recess()
    for uv, d in ((R_UV, 1.6), (B_UV, 1.6), (CHG_LED_UV, 1.4), (USER_LED_UV, 1.4)):
        x, y = xiao_xy(*uv)
        c -= cyl("z", d, x, y, DEPTH - 0.5, LID_T + 2.0)   # through the cover
    # Vents above the XIAO (warm air out)
    for i in range(6):
        x = 10.0 + 2.5 * i
        c -= rbox(1.2, 3.0, Z_LID - 0.5, DEPTH + 0.5, 0.55, x, XIAO_Y1 + 3.3)
    # Labels on the outside (debossed 0.4 mm)
    if LABELS:
        for text, uv in (("R", R_UV), ("B", B_UV)):
            x, y = xiao_xy(*uv)
            dy = 2.6 if y > XIAO_CY else -2.6   # beside the pinhole, away from the other one
            # mirrored, because it is read from the back (see the note at the top)
            c -= text_shape(text, 2.2, 0.41, x, y + dy, DEPTH - 0.4, mirror=True)
    return c


def lid_inside():
    """Everything on the inside of the back cover, kept clear of the shell's walls."""
    c = Manifold()
    tab_z = (Z_TAB, Z_LID + 0.01)
    x_lip_in = X_IN - FIT
    # Lip along the bottom, under the battery: lines up the cover and holds the display's edge
    c += box(-x_lip_in, BAT_BOX[1] + 0.6, -(Y_IN - FIT), -(Y_IN - FIT - LIP_W), *tab_z)
    # Screw tabs in three corners; they also press the display's corners
    for end, y in SCREWS:
        x0, x1 = sorted((end * x_lip_in, end * (x_lip_in - TAB_X)))
        y0, y1 = (TAB_Y[0], TAB_Y[1]) if y > 0 else (-TAB_Y[1], -TAB_Y[0])
        tab = box(x0, x1, y0, y1, *tab_z)
        tab -= cyl("x", SCREW_PILOT_D, end * (x_lip_in - SCREW_LEN / 2 + 0.5), y, SCREW_Z, SCREW_LEN + 1)
        c += tab
    # Battery: a low rim on the right and top (the walls do the other sides)
    bx0, bx1, by0, by1, bz0, bz1 = BAT_BOX
    rim_z = (Z_LID - 1.6, Z_LID + 0.01)
    c += box(bx1 + 0.3, bx1 + 1.1, by0, by0 + 9, *rim_z) + box(bx1 + 0.3, bx1 + 1.1, by1 - 9, by1, *rim_z)
    c += box(bx0 + TAB_X + 0.5, bx1 + 1.1, by1 + 0.3, by1 + 0.9, *rim_z)
    # XIAO: two ridges beside the USB-C socket and two posts at the far corners
    ridge_x = (XIAO_X1 - 5.8, XIAO_X1 - 2.0)
    for side in (-1, 1):
        y_in = XIAO_CY + side * (USB_W / 2 + 0.15)
        c += box(*ridge_x, *sorted((y_in, y_in + side * 0.6)), Z_LID - 1.0, Z_LID + 0.01)
    for y0, y1 in ((XIAO_Y1 - 1.8, XIAO_Y1 - 0.2), (XIAO_Y0 + 0.2, XIAO_Y0 + 1.8)):
        c += box(XIAO_X0 - 1.2, XIAO_X0 - 0.2, y0, y1, Z_XIAO + 0.6, Z_LID + 0.01)
    # Switch holders. Each switch slides in toward the cover (stem into its key's half-hole)
    # and is a snug fit between two side walls. The thick ledge under it takes the push.
    # The side walls stop above the legs, so the legs slide in underneath them.
    for x in BUTTON_X:
        half = SWITCH_BODY / 2 + 0.1
        z0 = Z_SWITCH - SWITCH_BODY / 2
        holder = box(x - 2.0, x + 2.0, SWITCH_BOTTOM - 2.0, SWITCH_BOTTOM - 0.1, z0, Z_LID + 0.01)
        for side in (-1, 1):
            holder += box(*sorted((x + side * half, x + side * (half + 1.2))), SWITCH_BOTTOM + 0.7,
                          Y_IN - 0.55, z0, Z_LID + 0.01)
            if SWITCH_LIPS:   # over the body's top edges, clear of the stem
                holder += box(*sorted((x + side * (half - 0.6), x + side * (half + 1.2))), SWITCH_TOP + 0.05,
                              min(SWITCH_TOP + 0.65, Y_IN - FIT - 0.05), z0, Z_LID + 0.01)
        c += holder
    # Magnetic connector holder (glue the connector in; wires out the back)
    mx0, mx1 = MAG_X - MAG_W / 2 - 0.25, MAG_X + MAG_W / 2 + 0.25
    mz0, mz1 = MAG_Z - MAG_T / 2 - 0.25, MAG_Z + MAG_T / 2 + 0.25
    holder = box(mx0 - 1.0, mx1 + 1.0, -(Y_IN - FIT), MAG_Y_IN + 1.2, mz0 - 1.0, Z_LID + 0.01)
    holder -= box(mx0, mx1, -Y_IN - 1, MAG_Y_IN + 0.2, mz0, mz1)
    holder -= cyl("y", 3.0, MAG_X, MAG_Y_IN + 1.0, MAG_Z, 4)
    c += holder
    # Keep FIT away from the shell's walls, including its rounded inside corners
    return c ^ rbox(IN_L - 2 * FIT, IN_H - 2 * FIT, Z_TAB - 1, Z_LID + 0.02, INNER_R - FIT)


# =====================================================================
# STAND-INS FOR THE ELECTRONICS (for checks and pictures)
# =====================================================================
def parts():
    p = {}
    p["glass"] = box(GLASS_X0, GLASS_X0 + GLASS_L, -GLASS_H / 2, GLASS_H / 2, Z_GLASS, Z_GLASS + GLASS_T)
    p["screen"] = box(ACTIVE_X0, ACTIVE_X0 + ACTIVE_L, -ACTIVE_H / 2, ACTIVE_H / 2, Z_GLASS - 0.02, Z_GLASS)
    p["display board"] = box(-PCB_L / 2, PCB_L / 2, -PCB_H / 2, PCB_H / 2, Z_PCB, Z_PCB_BACK)
    p["display back parts"] = box(-PCB_L / 2 + 5, PCB_L / 2 - 5, -PCB_H / 2 + 5, PCB_H / 2 - 5,
                                  Z_PCB_BACK, Z_BACK_PARTS)
    hw, hh = HEADER_ZONE
    p["display header"] = box(PCB_L / 2 - hw, PCB_L / 2 - 0.3, -hh / 2, hh / 2, Z_PCB_BACK, Z_HEADER)
    p["XIAO"] = box(XIAO_X0, XIAO_X1, XIAO_Y0, XIAO_Y1, Z_XIAO, Z_XIAO_TOP)
    x_end = XIAO_X1 + USB_OVERHANG
    p["USB-C"] = box(x_end - USB_L, x_end, XIAO_CY - USB_W / 2, XIAO_CY + USB_W / 2, Z_XIAO_TOP, Z_XIAO_TOP + USB_H)
    u0, u1, v0, v1, h = CAN
    (xa, ya), (xb, yb) = xiao_xy(u0, v0), xiao_xy(u1, v1)
    p["XIAO chip"] = box(min(xa, xb), max(xa, xb), min(ya, yb), max(ya, yb), Z_XIAO_TOP, Z_XIAO_TOP + h)
    ux, uy = xiao_xy(*UFL_UV)
    p["antenna plug"] = box(ux - 1.5, ux + 1.5, uy - 1.5, uy + 1.5, Z_XIAO_TOP, Z_XIAO_TOP + 2.4)
    for name, uv in zip(("BAT-", "BAT+"), BAT_PADS_UV):
        x, y = xiao_xy(*uv)
        p[f"{name} wire"] = box(x - 0.9, x + 0.9, y - 0.8, y + 0.8, Z_XIAO - PAD_JOINT_T, Z_XIAO)
    p["battery"] = box(*BAT_BOX)
    for i, x in enumerate(BUTTON_X):
        h = SWITCH_BODY / 2
        sw = box(x - h, x + h, SWITCH_BOTTOM, SWITCH_TOP, Z_SWITCH - h, Z_SWITCH + h)
        sw += cyl("y", STEM_D, x, SWITCH_TOP + STEM_H / 2, Z_SWITCH, STEM_H)
        for side in (-1, 1):   # legs 6.5 mm apart, flat side against the body
            sw += box(x + side * 3.25 - LEG_T / 2, x + side * 3.25 + LEG_T / 2, SWITCH_BOTTOM - LEG_L,
                      SWITCH_BOTTOM + 0.5, Z_SWITCH - LEG_W / 2, Z_SWITCH + LEG_W / 2)
        p[f"switch {'AB'[i]}"] = sw
    p["magnet connector"] = box(MAG_X - MAG_W / 2, MAG_X + MAG_W / 2, -Y_OUT, MAG_Y_IN,
                                MAG_Z - MAG_T / 2, MAG_Z + MAG_T / 2)
    for end, y in SCREWS:
        x_out = end * X_OUT
        head = Manifold.cylinder(1.0, SCREW_HEAD_D / 2, SCREW_D / 2, SEG)
        head = head.rotate((0, 90, 0)) if end > 0 else head.rotate((0, -90, 0))
        shank = cyl("x", SCREW_D, x_out - end * SCREW_LEN / 2, y, SCREW_Z, SCREW_LEN)
        p[f"screw {end:+d} {y:+.0f}"] = head.translate((x_out, y, SCREW_Z)) + shank
    return p


PART_COLORS = {
    "shell": "#e9e4da", "lid": "#d9822b", "dock": "#5b6b7f", "glass": "#15171c", "screen": "#2a63b8",
    "display board": "#1d4fa8", "display back parts": "#3a6fd0", "display header": "#c9a227",
    "XIAO": "#20242a", "USB-C": "#b8bec6", "XIAO chip": "#c8ccd2", "antenna plug": "#d6b45a",
    "BAT- wire": "#222222", "BAT+ wire": "#cc2222", "battery": "#9aa4b0", "switch A": "#3b3b3b",
    "switch B": "#3b3b3b", "magnet connector": "#7c7c84", "dock connector": "#7c7c84",
}


def color_of(name):
    return PART_COLORS.get(name, "#8d8d8d" if name.startswith("screw") else "#777777")


# On the back cover, in the order they're installed; the rest stays in the shell
LID_PARTS = ("XIAO", "USB-C", "XIAO chip", "antenna plug", "BAT- wire", "BAT+ wire", "battery",
             "switch A", "switch B", "magnet connector")
DISPLAY_PARTS = ("glass", "screen", "display board", "display back parts", "display header")


# =====================================================================
# DOCK
# =====================================================================
DOCK_FLOOR = 12.0       # height of the device's bottom front edge above the table
DOCK_LIP_H, DOCK_BACK_H, DOCK_END_H = 4.0, 24.0, 7.0
DOCK_T = 2.4            # wall thickness
DOCK_CLEAR = 0.4


def to_dock(m):
    """Device coordinates -> dock coordinates (bottom front edge at the dock's lip, tilted back)."""
    return m.translate((0, Y_OUT, 0)).rotate((DOCK_TILT, 0, 0)).translate((0, DOCK_FLOOR, DOCK_T))


def dock():
    c = DOCK_CLEAR
    # The cradle, built in device coordinates, then tilted
    x_end = X_OUT + c + DOCK_T
    y_floor = -Y_OUT - 3.0
    cradle = box(-x_end, x_end, y_floor, -Y_OUT + DOCK_LIP_H, -DOCK_T - c, 0.5)                    # front lip
    cradle += box(-x_end, x_end, y_floor, -Y_OUT + DOCK_BACK_H, DEPTH + c, DEPTH + c + 3.0)          # back rest
    for side in (-1, 1):
        cradle += box(*sorted((side * (X_OUT + c), side * x_end)), y_floor, -Y_OUT + DOCK_END_H,
                      -DOCK_T - c, DEPTH + c + 3.0)                                                 # ends
    cradle += box(-x_end, x_end, y_floor, -Y_OUT, -DOCK_T - c, DEPTH + c + 3.0)                       # floor
    tilted = to_dock(cradle)
    # Fill down to the table
    bottom = Manifold.hull(tilted + box(-x_end, x_end, 0, 0.01, -2.0, DEPTH + 16)) ^ box(
        -x_end - 5, x_end + 5, 0, DOCK_FLOOR + 1.0, -10, 60)
    solid = tilted + bottom
    # The pocket the device sits in (swept up, so it slides in from above)
    pocket = outer_shape(-c, DEPTH + c, 0, 0, grow=c) ^ box(-50, 50, -Y_OUT - c, 60, -10, 30)
    pocket = Manifold.hull(pocket + pocket.translate((0, 40, 0)))
    solid -= to_dock(pocket)
    # The other half of the magnetic connector, flush with the floor, pointing up into the device
    mate = box(MAG_X - MAG_W / 2 - 0.25, MAG_X + MAG_W / 2 + 0.25, -Y_OUT - MAG_D - 0.3, -Y_OUT + 0.1,
               MAG_Z - MAG_T / 2 - 0.25, MAG_Z + MAG_T / 2 + 0.25)
    wire = box(MAG_X - 2.6, MAG_X + 2.6, -Y_OUT - 40, -Y_OUT - MAG_D, MAG_Z - 2.2, MAG_Z + 2.2)
    solid -= to_dock(mate + wire)
    # Cable groove underneath, out the back
    solid -= box(MAG_X - 2.6, MAG_X + 2.6, -1, 4.6, 0, 80)
    # Coin pocket underneath (a stack of US quarters adds weight), and rubber feet
    solid -= coin_pocket()
    for fx in (-x_end + 6, x_end - 6):
        for fz in (2.0, DEPTH + 11):
            solid -= Manifold.cylinder(1.0, 4.25, -1, SEG).rotate((-90, 0, 0)).translate((fx, -0.01, fz))
    return solid


def coin_pocket():
    # Vertical pocket from the underside: 24.9 mm across (US quarter), 5.6 deep
    return Manifold.cylinder(5.6, 24.9 / 2, -1, SEG * 2).rotate((-90, 0, 0)).translate((-16.0, -0.01, 15.0))


def dock_parts():
    mate = box(MAG_X - MAG_W / 2, MAG_X + MAG_W / 2, -Y_OUT - MAG_D, -Y_OUT,
               MAG_Z - MAG_T / 2, MAG_Z + MAG_T / 2)
    return {"dock connector": to_dock(mate)}


# =====================================================================
# FIT TESTS
# =====================================================================
def fit_test_front():
    """The front plate and the first few mm of wall: drop the display in to check it."""
    return shell() ^ box(-50, 50, -50, 50, -1, Z_PCB_BACK + 0.6)


def button_test_region():
    x = BUTTON_X[0]
    return box(x - 5.1, x + 5.1, SWITCH_BOTTOM - 2.6, Y_OUT + 0.1, -1, DEPTH + 1)


def fit_test_button_wall():
    """A strip of the shell's top wall with button A's hole (prints face down, like the shell)."""
    return shell() ^ button_test_region()


def fit_test_button_holder():
    """The matching strip of back cover: button A's switch holder and key (prints flipped)."""
    return lid() ^ button_test_region()


# =====================================================================
# CHECKS
# =====================================================================
def overlap(a, b):
    return (a ^ b).volume()


def check_all(shell_m, lid_m, dock_m, p, dp):
    problems, notes = [], []
    printed = {"shell": shell_m, "lid": lid_m}
    names = list(p)
    # 1. Nothing overlaps anything in the finished device
    for name in names:
        for pname, part in printed.items():
            if name.startswith("screw") and pname == "lid":
                continue  # self-tapping screws cut their own thread in the 1.7 mm pilot holes
            v = overlap(p[name], part)
            if v > 0.02:
                problems.append(f"{name} overlaps the {pname} ({v:.2f} mm3)")
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a.startswith("screw") or b.startswith("screw"):
                continue
            v = overlap(p[a], p[b])
            same_part = ({"glass", "screen"}, {"display back parts", "display header"})
            if v > 0.02 and {a, b} not in same_part:
                problems.append(f"{a} overlaps {b} ({v:.2f} mm3)")
    if overlap(shell_m, lid_m) > 0.05:
        problems.append(f"shell and lid overlap ({overlap(shell_m, lid_m):.2f} mm3)")
    # 2. The display drops into the empty shell from the back
    disp = sum((p[n] for n in DISPLAY_PARTS[1:]), p[DISPLAY_PARTS[0]])
    worst = max(overlap(disp.translate((0, 0, dz)), shell_m) for dz in np.arange(0.25, 16, 0.25))
    (problems if worst > 0.02 else notes).append(f"display slides in from the back: worst overlap {worst:.3f} mm3")
    # 3. The back cover, with everything on it, closes straight onto the shell
    loaded = sum((p[n] for n in LID_PARTS), lid_m)
    fixed = shell_m + disp
    worst = max(overlap(loaded.translate((0, 0, dz)), fixed) for dz in np.arange(0.25, 16, 0.25))
    (problems if worst > 0.05 else notes).append(f"back cover closes straight in: worst overlap {worst:.3f} mm3")
    # 4. Buttons: the stems stand out of the top edge, clear of their holes, and each switch
    # slides into its holder on the back cover
    out = STEM_TOP - Y_OUT
    (problems if out < 0.8 else notes).append(
        f"button stems stand {out:.2f} mm out of the top edge ({out - SWITCH_TRAVEL:.2f} mm while clicked)"
        + ("" if out >= 0.8 else ": too little to press, use taller switches (SWITCH_H)"))
    stem = cyl("y", STEM_D, BUTTON_X[0], SWITCH_TOP + STEM_H / 2, Z_SWITCH, STEM_H)
    notes.append(f"stem clearance in its hole: {stem.min_gap(shell_m, 2.0):.2f} mm (shell half), "
                 f"{stem.min_gap(lid_m, 2.0):.2f} mm (key half)")
    play = 0.1 + (0.05 if SWITCH_LIPS else Y_IN - SWITCH_TOP)
    notes.append(f"switch can shift {play:.2f} mm up or down in its holder "
                 f"({'lips on the holder stop' if SWITCH_LIPS else 'the top wall stops'} it rising)")
    for name in ("switch A", "switch B"):
        worst = max(overlap(p[name].translate((0, 0, -dz)), lid_m) for dz in np.arange(0.25, 9, 0.25))
        (problems if worst > 0.02 else notes).append(f"{name} slides into its holder: worst overlap {worst:.3f} mm3")
    gap = p["display board"].min_gap(lid_m, 5.0)
    notes.append(f"display board to the back cover's tabs: {gap:.2f} mm (fill with 0.5 mm foam)")
    for a, b in (("battery", "display back parts"), ("battery", "XIAO"), ("XIAO", "display header"),
                 ("BAT+ wire", "display header"), ("switch A", "XIAO"), ("magnet connector", "XIAO")):
        notes.append(f"gap {a} - {b}: {p[a].min_gap(p[b], 10.0):.2f} mm")
    # 5. Dock
    dev = to_dock(shell_m + lid_m)
    v = overlap(dev, dock_m)
    (problems if v > 0.05 else notes).append(f"device in the dock: overlap {v:.3f} mm3")
    v = overlap(to_dock(p["magnet connector"]), dp["dock connector"])
    notes.append(f"connectors meet face to face (overlap {v:.3f} mm3, should be 0)")
    gap = to_dock(p["magnet connector"]).min_gap(dp["dock connector"], 5.0)
    notes.append(f"connector faces: {gap:.2f} mm apart (magnets close this)")
    return problems, notes


def overhang_report(m, name):
    """Downward-facing area steeper than 45 degrees, in the print orientation (bed at z=0)."""
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    t = v[np.asarray(mesh.tri_verts)]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    nz = n[:, 2] / (2 * area + 1e-12)
    low = t[:, :, 2].min(1)
    steep = (nz < -0.71) & (low > 0.3)
    flat = (nz < -0.99) & (low > 0.3)
    return f"{name}: {area[steep].sum():.0f} mm2 facing down (of which flat bridges {area[flat].sum():.0f} mm2)"


# =====================================================================
# OUTPUT
# =====================================================================
def physical(m):
    """Design coordinates (left-handed) -> real-world, right-handed coordinates."""
    return m.mirror((1, 0, 0))


def write_stl(m, path):
    mesh = physical(m).to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    t = np.asarray(mesh.tri_verts)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, np.stack([a, b, c], 1)
    with open(path, "wb") as f:
        f.write(b"weather display".ljust(80, b" "))
        f.write(np.uint32(len(t)).tobytes())
        f.write(rec.tobytes())


def print_pose(m, flip):
    """Turn a part so it sits on the print bed (z = 0)."""
    if flip:
        m = m.mirror((0, 0, 1))
    lo = np.asarray(m.bounding_box()[:3])
    return m.translate((0, 0, -lo[2]))


def tris(m):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    return v[np.asarray(mesh.tri_verts)]


def render(items, path, elev, azim, title, size=(1000, 700), zoom=0.86, ss=2):
    """Shaded picture with outlines. azim 0 = looking at the front, 90 = from the left end,
    180 = from the back; elev > 0 = from above, < 0 = from below."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgb

    a, e = np.radians(azim), np.radians(-elev)
    turn = np.array([[np.cos(a), 0, -np.sin(a)], [0, 1, 0], [np.sin(a), 0, np.cos(a)]])
    tilt = np.array([[1, 0, 0], [0, np.cos(e), -np.sin(e)], [0, np.sin(e), np.cos(e)]])
    view = tilt @ turn
    light = np.array([-0.4, 0.6, -0.7])
    light /= np.linalg.norm(light)
    scenes = [(tris(m) @ view.T, np.array(to_rgb(c)), k) for k, (m, c) in enumerate(items)]
    pts = np.concatenate([t.reshape(-1, 3) for t, _, _ in scenes])
    lo, hi = pts.min(0), pts.max(0)
    W, H = size[0] * ss, size[1] * ss
    scale = zoom * min(W / (hi[0] - lo[0]), H / (hi[1] - lo[1]))
    mid = (lo + hi) / 2
    img = np.ones((H, W, 3)) * np.array([0.965, 0.97, 0.975])
    depth = np.full((H, W), np.inf)
    ident = np.full((H, W), -1, dtype=np.int32)
    normal = np.zeros((H, W, 3))
    for tri, color, k in scenes:
        nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
        shade = 0.42 + 0.5 * np.clip(-(nrm @ light), 0, 1) + 0.08 * np.clip(-nrm[:, 2], 0, 1)
        px = (tri[..., 0] - mid[0]) * scale + W / 2
        py = H / 2 - (tri[..., 1] - mid[1]) * scale
        for (x0, x1, x2), (y0, y1, y2), z, s, n in zip(px, py, tri[..., 2], shade, nrm):
            area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
            if abs(area) < 1e-9:
                continue
            l, r = max(int(min(x0, x1, x2)), 0), min(int(max(x0, x1, x2)) + 1, W)
            t_, b = max(int(min(y0, y1, y2)), 0), min(int(max(y0, y1, y2)) + 1, H)
            if l >= r or t_ >= b:
                continue
            gx, gy = np.meshgrid(np.arange(l, r) + 0.5, np.arange(t_, b) + 0.5)
            w0 = ((x1 - gx) * (y2 - gy) - (x2 - gx) * (y1 - gy)) / area
            w1 = ((x2 - gx) * (y0 - gy) - (x0 - gx) * (y2 - gy)) / area
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
            reg = depth[t_:b, l:r]
            near = inside & (zz < reg)
            reg[near] = zz[near]
            img[t_:b, l:r][near] = np.clip(color * s, 0, 1)
            ident[t_:b, l:r][near] = k
            normal[t_:b, l:r][near] = n
    # Outlines: where the part changes, the depth jumps, or the surface bends sharply
    edge = np.zeros((H, W), bool)
    for dy, dx in ((0, 1), (1, 0)):
        a_id, b_id = ident[:H - dy, :W - dx], ident[dy:, dx:]
        a_d, b_d = depth[:H - dy, :W - dx], depth[dy:, dx:]
        a_n, b_n = normal[:H - dy, :W - dx], normal[dy:, dx:]
        with np.errstate(invalid="ignore"):
            jump = np.abs(np.nan_to_num(a_d - b_d, posinf=1e3, neginf=-1e3)) > 0.6
        bend = (a_n * b_n).sum(-1) < 0.85
        e_ = (a_id != b_id) | jump | ((a_id >= 0) & bend)
        edge[:H - dy, :W - dx] |= e_
    img[edge] = img[edge] * 0.35
    img = img.reshape(size[1], ss, size[0], ss, 3).mean((1, 3))
    fig = plt.figure(figsize=(size[0] / 100, size[1] / 100 + 0.45), dpi=100)
    plt.imshow(img)
    plt.axis("off")
    plt.title(title, fontsize=11)
    fig.savefig(path, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def section(items, axis, value, keep_positive=True, region=None):
    """Cut everything with a plane (axis = 'x', 'y' or 'z' at value); keep one side,
    and optionally only what's inside `region` (a box) for a close-up."""
    normal = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
    if not keep_positive:
        normal = tuple(-c for c in normal)
        value = -value
    out = []
    for m, c in items:
        cut = m.trim_by_plane(normal, value)
        if region is not None:
            cut = cut ^ region
        if not cut.is_empty():
            out.append((cut, c))
    return out


PART_INFO = {   # name shown in the viewer, and one line about it
    "Shell": ("Front shell", "Window with a beveled edge, glass pocket, two button holes, USB-C and connector slots."),
    "Back cover": ("Back cover", "Carries the XIAO, battery, switches and magnet connector. Two keys finish the "
                                 "button holes. Three M2 screws hold it."),
    "glass": ("Display glass", "47.8 × 34.6 × 2.03 mm. Sits in a 0.8 mm pocket, so the screen lines up with the window."),
    "screen": ("Screen", "The lit area, 40.8 × 30.6 mm, 240 × 320 pixels."),
    "display board": ("Display board", "58.04 × 37.5 mm. The back cover's tabs press its corners."),
    "display back parts": ("Room for parts on the display's back", "1 mm kept clear behind the display board."),
    "display header": ("Pin header area", "Pins cut to about 1.5 mm, with wires soldered on: 2 mm kept clear."),
    "XIAO": ("XIAO ESP32-S3", "Parts side facing the back cover, USB-C through the right end."),
    "USB-C": ("USB-C socket", "For programming and charging. It touches the back cover, which holds it in place."),
    "XIAO chip": ("XIAO metal can", "Foam tape on it holds the XIAO to the back cover."),
    "antenna plug": ("Antenna plug (U.FL)", "The antenna goes in the strip along the top edge, above the battery."),
    "BAT- wire": ("Battery − wire", "On the XIAO's underside pad nearer the USB-C."),
    "BAT+ wire": ("Battery + wire", "On the XIAO's underside pad farther from the USB-C."),
    "battery": ("503035 battery", "500 mAh, 5 × 30 × 35 mm (room allowed: 5.5 × 30.5 × 35.5). Foam-taped to the back cover."),
    "switch A": ("Button A switch", f"6 × 6 × {SWITCH_H:g} mm, in a holder on the back cover. Its stem pokes "
                                    "out of the top edge and you press it directly. Wired to D2."),
    "switch B": ("Button B switch", f"6 × 6 × {SWITCH_H:g} mm, in a holder on the back cover. Its stem pokes "
                                    "out of the top edge and you press it directly. Wired to D4."),
    "magnet connector": ("Magnetic connector", "Device half, glued into its holder, face flush with the bottom."),
    "dock": ("Dock", "Holds the display tilted back 15°. A coin pocket and rubber feet underneath."),
    "dock connector": ("Dock connector", "The other half of the magnetic connector, flush with the dock floor."),
}


def viewer_export(groups, path):
    """Meshes for the 3D viewer page: positions as base64 float32, grouped by part."""
    data = []
    for key, m, color, group, explode in groups:
        mesh = physical(m).to_mesh()
        v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
        t = np.asarray(mesh.tri_verts).astype(np.uint32)
        if key.startswith("screw"):
            name, about = "M2 × 5 screw", "Flat head, self-tapping, into a tab on the back cover."
        else:
            name, about = PART_INFO.get(key, (key, ""))
        data.append({"name": name, "about": about, "color": color, "group": group, "explode": explode,
                     "positions": base64.b64encode(v.tobytes()).decode(),
                     "indices": base64.b64encode(t.tobytes()).decode()})
    active = physical(box(ACTIVE_X0, ACTIVE_X0 + ACTIVE_L, -ACTIVE_H / 2, ACTIVE_H / 2, 0, 1)).bounding_box()
    path.write_text(json.dumps({
        "parts": data, "size": [OUT_L, OUT_H, DEPTH],
        "screen": {"cx": (active[0] + active[3]) / 2, "cy": 0, "z": Z_GLASS - 0.03, "w": ACTIVE_L, "h": ACTIVE_H},
        "dock": {"tilt": DOCK_TILT, "floor": DOCK_FLOOR, "front": DOCK_T, "yOut": Y_OUT},
    }), encoding="utf-8")


def main():
    STL_DIR.mkdir(exist_ok=True)
    IMG_DIR.mkdir(exist_ok=True)
    shell_m, lid_m, dock_m = shell(), lid(), dock()
    p, dp = parts(), dock_parts()

    problems, notes = check_all(shell_m, lid_m, dock_m, p, dp)
    print(f"Case: {OUT_L:.1f} x {OUT_H:.1f} x {DEPTH:.1f} mm  (inside {IN_L:.2f} x {IN_H:.2f})")
    print(f"Stack: glass {Z_GLASS:.2f}-{Z_GLASS + GLASS_T:.2f}, display board {Z_PCB:.2f}-{Z_PCB_BACK:.2f}, "
          f"XIAO {Z_XIAO:.2f}-{Z_XIAO_TOP:.2f}, USB-C center {USB_Z:.2f}, back cover {Z_LID:.2f}-{DEPTH:.2f}")
    for n in notes:
        print("  ok  " + n)
    for n in problems:
        print("  !!  " + n)
    fit_front, fit_wall, fit_holder = fit_test_front(), fit_test_button_wall(), fit_test_button_holder()
    poses = {"case_shell": (shell_m, False), "case_back_cover": (lid_m, True),
             "dock": (dock_m.rotate((90, 0, 0)), False),   # the dock's "up" (y) becomes the printer's z
             "fit_test_front": (fit_front, False),
             "fit_test_button_wall": (fit_wall, False), "fit_test_button_holder": (fit_holder, True)}
    for name, (m, flip) in poses.items():
        posed = print_pose(m, flip)
        write_stl(posed, STL_DIR / f"{name}.stl")
        lo, hi = np.asarray(posed.bounding_box()[:3]), np.asarray(posed.bounding_box()[3:])
        grams = posed.volume() / 1000 * 1.24
        print(f"  {name}.stl: {hi[0] - lo[0]:.1f} x {hi[1] - lo[1]:.1f} x {hi[2] - lo[2]:.1f} mm, "
              f"{posed.volume() / 1000:.1f} cm3 (about {grams:.0f} g of PLA if solid). "
              + overhang_report(posed, "overhangs"))

    # ---------- pictures ----------
    sc, lc = color_of("shell"), color_of("lid")
    elec = [(m, color_of(n)) for n, m in p.items()]
    screws = [(m, color_of(n)) for n, m in p.items() if n.startswith("screw")]
    render([(shell_m, sc), (lid_m, lc), (p["glass"], color_of("glass")), (p["screen"], color_of("screen"))]
           + screws, IMG_DIR / "1_front.png", 18, -28,
           f"Front: {OUT_L:.1f} x {OUT_H:.1f} x {DEPTH:.1f} mm (buttons on top, USB-C on the right)")
    render([(shell_m, sc), (lid_m, lc)] + screws, IMG_DIR / "2_back.png", 22, 205,
           "Back: R and B pinholes, two light windows, vents, USB-C")
    top = box(-X_OUT - 1, X_OUT + 1, 8, STEM_TOP + 1, -1, DEPTH + 1)
    render([(shell_m ^ top, sc), (lid_m ^ top, lc)] + [(p[n] ^ top, color_of(n)) for n in ("switch A", "switch B")],
           IMG_DIR / "2b_top_buttons.png", 62, -12,
           "Top edge: the switch stems poke out through round holes", zoom=0.9)
    spread = {"shell": 0, "display": 14, "lid parts": 30, "lid": 46}
    exploded = [(shell_m, sc)]
    exploded += [(p[n].translate((0, 0, spread["display"])), color_of(n)) for n in DISPLAY_PARTS]
    exploded += [(p[n].translate((0, 0, spread["lid parts"])), color_of(n)) for n in LID_PARTS]
    exploded += [(lid_m.translate((0, 0, spread["lid"])), lc)]
    render(exploded, IMG_DIR / "3_exploded.png", 24, 140, "Exploded: shell, display, parts on the back cover, back cover")
    render([(shell_m, sc)] + [(p[n], color_of(n)) for n in DISPLAY_PARTS] +
           [(p[n], color_of(n)) for n in LID_PARTS], IMG_DIR / "4_inside.png", 34, 165,
           "Inside, back cover off (back cover's parts shown in place)")
    render([(lid_m, lc)] + [(p[n], color_of(n)) for n in LID_PARTS], IMG_DIR / "5_back_cover_parts.png",
           40, -25, "Back cover from the inside, with its parts: battery, XIAO, switches, magnet connector")
    everything = [(shell_m, sc), (lid_m, lc)] + elec
    render(section(everything, "x", BUTTON_X[0]), IMG_DIR / "6_section_button.png", 0, 90,
           f"Cut through button A, seen from the left (front of the device on the right)", zoom=0.9)
    close = box(-50, 50, SWITCH_BOTTOM - 4.5, STEM_TOP + 0.5, Z_PCB - 1.5, DEPTH + 0.5)
    render(section(everything, "x", BUTTON_X[0], region=close), IMG_DIR / "6b_section_button_closeup.png",
           0, 90, "Close-up: stem through the top wall (key behind it), switch on its ledge", zoom=0.92)
    render(section(everything, "y", XIAO_CY, keep_positive=False), IMG_DIR / "7_section_usb.png", 90, 0,
           "Cut through the USB-C, seen from above (front of the device at the bottom)", zoom=0.95)
    render(section(everything, "x", MAG_X), IMG_DIR / "8_section_magnet.png", 0, 90,
           "Cut through the magnetic connector, seen from the left", zoom=0.9)
    dock_items = [(dock_m, color_of("dock")), (to_dock(shell_m), sc), (to_dock(lid_m), lc),
                  (to_dock(p["glass"]), color_of("glass")), (to_dock(p["screen"]), color_of("screen")),
                  (dp["dock connector"], color_of("dock connector"))]
    render(dock_items, IMG_DIR / "9_dock.png", 20, -32, f"On the dock (tilted back {DOCK_TILT:.0f} degrees)")
    render([(dock_m, color_of("dock")), (dp["dock connector"], color_of("dock connector"))],
           IMG_DIR / "10_dock_alone.png", 38, -30, "Dock: connector in the floor (cable runs underneath)")
    render([(dock_m, color_of("dock"))], IMG_DIR / "10b_dock_underside.png", -40, -30,
           "Dock underside: coin pocket (US quarters), rubber feet, cable groove")
    render([(fit_front, sc)], IMG_DIR / "11_fit_test_front.png", 35, -20, "Fit test 1: front plate (drop the display in)")
    render([(fit_wall, sc), (fit_holder, lc), (p["switch A"], color_of("switch A"))],
           IMG_DIR / "12_fit_test_button.png", 25, 35,
           "Fit test 2: top wall with the button hole (shell piece) + switch holder and key (cover piece)")
    import button_picture
    button_picture.draw(sys.modules[__name__], shell_m, lid_m, p, IMG_DIR / "13_how_the_buttons_work.png")

    # ---------- data for the 3D viewer ----------
    groups = [("Shell", shell_m, sc, "shell", [0, 0, 0]), ("Back cover", lid_m, lc, "cover", [0, 0, 46])]
    groups += [(n, p[n], color_of(n), "display", [0, 0, 14]) for n in DISPLAY_PARTS]
    groups += [(n, p[n], color_of(n), "electronics", [0, 0, 30]) for n in LID_PARTS]
    groups += [(n, m, color_of(n), "screws", [0, 0, 0]) for n, m in p.items() if n.startswith("screw")]
    groups += [("dock", dock_m, color_of("dock"), "dock", [0, 0, 0]),
               ("dock connector", dp["dock connector"], color_of("dock connector"), "dock", [0, 0, 0])]
    viewer_export(groups, HERE / "viewer_data.json")
    print("Done:", ", ".join(sorted(f.name for f in STL_DIR.glob("*.stl"))))
    return problems


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
