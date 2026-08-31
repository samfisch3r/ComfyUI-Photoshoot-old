"""
Photoshoot - a whole series of images from one click.

The person stays, everything else moves on: framing, posture, expression and
aspect ratio. The node hands out a different sentence per run and drives the
queue itself, from a button in its own interface (js/shooting.mjs).

Why not a batch: the conditioning is computed once and brought up to the batch
size with repeat_to_batch_size (comfy/utils.py:859) - all images in a batch
necessarily share the same prompt. 50 different images need 50 runs.

Why not random: across 50 photos some combination would come up three times and
another never. Instead the seed counts upwards and serves as the run index.
"""

import json

from . import expression_builder as EB
from . import person_builder as PeB
from . import pose_builder as PB

NONE = "—"
ALLE = "alle"

# ─────────────────────────────────────────────────────────────────────────────
# Framing - the one list the kit did not have yet. On a real shoot this is the
# biggest difference between any two images.
#
# Wide framings have to demand the room explicitly (negative space, frame
# share). "wide shot, figure small" on its own loses against a long person
# block - hence the sharper wordings from medium shot outwards, and on top of
# that the photoshoot shortens the person block via detail_fuer_kamera().
# ─────────────────────────────────────────────────────────────────────────────
KAMERA = [
    ("Detail", "extreme close-up shot"),
    ("Nahaufnahme", "close-up shot"),
    ("Porträt", "portrait shot, head and shoulders"),
    # From here on there is more than the head in frame, and from here on the
    # note about proportions is needed: the Person Builder still weighs towards
    # the head - twelve fields for the face against eight for the body - and the
    # model hands out frame area roughly by that weighting, so without a
    # counterweight the head comes out too large. For the three tight framings
    # above the note would be pointless to actively harmful.
    ("Halbtotale", "medium shot, waist up, natural head-to-body proportions, "
                   "the figure does not fill the entire frame, visible space "
                   "around the subject"),
    ("Amerikanisch", "cowboy shot, from mid-thigh up, "
                     "realistic head-to-body proportions, clear space around "
                     "the figure"),
    ("Ganzkörper", "full body shot, the entire figure visible head to toe, "
                   "figure fills at most half the frame height, clear space "
                   "above the head and below the feet, realistic head-to-body "
                   "proportions with a proportionally small head"),
    ("Totale", "environmental wide establishing shot, the figure small in the "
               "middle ground of a large space, subject occupies less than a "
               "quarter of the frame, large surrounding environment and ample "
               "negative space, realistic head-to-body proportions"),
]

# Focus of the image. Appended to the framing rather than carried on its own
# output - both describe the same thing (what the image shows), and another
# placeholder in the prompt would buy nothing but wiring.
FOKUS = [
    ("Gesicht", "with the focus on the face"),
    ("Augen", "with the focus on the eyes"),
    ("Lippen", "with the focus on the lips"),
    ("Oberkörper", "with the focus on the upper body"),
    ("Dekolleté", "with the focus on the neckline"),
    ("Hände", "with the focus on the hands"),
    ("Taille", "with the focus on the waist and hips"),
    ("Beine", "with the focus on the legs"),
    ("Füße", "with the focus on the feet and shoes"),
    ("Rücken", "with the focus on the back"),
    ("Ganze Figur", "with the focus on the whole figure"),
    # On a wide or full-body shot: the room is the subject and the figure is
    # secondary - otherwise "focus on the whole figure" pulls the attention back
    # onto the person.
    ("Raum", "with the environment as the primary subject, the figure secondary"),
]

# Which focus fits which framing. A wide shot focused on the lips is a
# contradiction - as is a close-up of the feet, which would no longer be a
# close-up but a detail shot.
KAMERA_FOKUS = {
    "Detail":       ["Augen", "Lippen", "Hände", "Füße"],
    "Nahaufnahme":  ["Gesicht", "Augen", "Lippen", "Dekolleté"],
    "Porträt":      ["Gesicht", "Augen", "Dekolleté"],
    "Halbtotale":   ["Oberkörper", "Dekolleté", "Hände", "Taille", "Rücken", "Raum"],
    "Amerikanisch": ["Taille", "Beine", "Oberkörper", "Rücken", "Raum"],
    "Ganzkörper":   ["Raum", "Ganze Figur", "Beine", "Füße", "Rücken"],
    # "Room" first: on a wide shot the setting is the image, not the figure.
    # "Back" stays: a wide shot of a figure seen from behind is common. "Feet"
    # stays out - a wide shot focused on the feet contradicts itself.
    "Totale":       ["Raum", "Ganze Figur", "Beine", "Rücken"],
}

# Which placement fits which framing.
#
# The same coupling as for the focus, for the same reason - but here its absence
# does more damage: camera and placement both say something about distance.
# "portrait shot, head and shoulders" together with "farther back in the
# background" is not a skewed image but a contradiction, and the model resolves
# it by painting the person twice - once close in the portrait, once small in
# the background.
#
# For the three tight framings, therefore, only placements without a distance
# claim remain: "in the foreground" confirms the closeness, "by the window" is a
# location and not a distance. "Deep in the room" drops out everywhere except
# the wide shot, since it explicitly says "a small figure".
_RAUM_NAH = ["Vordergrund", "Am Fenster"]
_RAUM_MITTE = _RAUM_NAH + ["Bildmitte", "Im Türrahmen", "An der Raumkante",
                           "Zwischen Möbeln", "Gehend durch den Raum"]
_RAUM_WEIT = _RAUM_MITTE + ["Hintergrund", "An der Wand"]

KAMERA_RAUM = {
    "Detail":       list(_RAUM_NAH),
    "Nahaufnahme":  list(_RAUM_NAH),
    "Porträt":      list(_RAUM_NAH),
    "Halbtotale":   list(_RAUM_MITTE),
    "Amerikanisch": list(_RAUM_MITTE),
    "Ganzkörper":   list(_RAUM_WEIT),
    "Totale":       _RAUM_WEIT + ["Tief im Raum"],
}

# Aspect ratios. The concrete dimensions are computed rather than tabulated -
# that way the size can be chosen freely without maintaining nine pairs of
# numbers for every step.
RATIOS = {
    "1:1":  (1, 1),
    "4:5":  (4, 5),
    "5:4":  (5, 4),
    "3:4":  (3, 4),
    "4:3":  (4, 3),
    "2:3":  (2, 3),
    "3:2":  (3, 2),
    "9:16": (9, 16),
    "16:9": (16, 9),
}

# Selectable sizes, named after the edge length of the equivalent square. 1328
# corresponds to 1.76 MP and gives exactly 1088x1632 at 2:3 - the format used so
# far.
KANTEN = [1024, 1152, 1280, 1328, 1440, 1536]
KANTE_STANDARD = 1328


def masse_fuer(ratio, kante):
    """Dimensions of an aspect ratio at a given square edge length.

    The pixel count stays the same across all ratios, so that compute time and
    memory use are constant over the series - otherwise a single format aborts
    with OOM in the middle of a 50-image run. Rounded to multiples of 16,
    because the VAE works in steps of 8 and 16 is on the safe side.
    """
    rw, rh = RATIOS.get(ratio, (1, 1))
    flaeche = float(kante) * float(kante)
    w = (flaeche * rw / rh) ** 0.5
    h = (flaeche * rh / rw) ** 0.5
    runde = lambda x: max(256, int(round(x / 16.0)) * 16)
    return (runde(w), runde(h))

# Which aspect ratio fits which framing. Without this coupling the full-body
# shot eventually lands in 16:9 landscape and the image is unusable.
KAMERA_FORMATE = {
    "Detail":       ["1:1", "3:2", "16:9"],
    "Nahaufnahme":  ["1:1", "4:5"],
    "Porträt":      ["4:5", "1:1", "3:4"],
    "Halbtotale":   ["2:3", "3:4", "4:5"],
    "Amerikanisch": ["2:3", "3:4"],
    "Ganzkörper":   ["2:3", "3:4", "9:16"],
    "Totale":       ["3:2", "16:9", "1:1"],
}

# Fields the series steps through, with where they come from.
#   quelle: "kamera" | "pose" | "ausdruck"
FELDER = (
    [("kamera", "kamera")]
    + [(cat, "pose") for cat in PB.FOLGE]
    + [(cat, "ausdruck") for cat in EB.FOLGE]
)

# Step sizes for the enumeration.
#
# The obvious approach would be (run * factor) % length with integer factors.
# That was measured and failed: fields with related list lengths march in
# lockstep. Of 66 field pairs, 18 were rigidly coupled - "koerper" and "mund"
# reached only 6 of 36 possible pairings, so the same body turn always arrived
# with the same mouth. Over a series that reads as mechanical.
#
# A Kronecker sequence instead: the step size is the fractional part of a square
# root, and therefore irrational. Irrational steps cannot, by definition, fall
# into a common beat. The arithmetic is integer over a fixed denominator, so
# that even large counter values stay exact - in floating point the result
# eventually drifts away and the series would no longer be reproducible.
_NENNER = 1 << 32
_WURZELN = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53]

DEFAULT_STATE = {
    "anzahl": 12,
    "aktiv": {"kamera": True, "pose": True, "ausdruck": True, "format": True,
              "fokus": True, "rausch": True},
    # Applies when "rausch" is off: every photo in the series then gets this
    # seed instead of one of its own.
    "serienSeed": 0,
    # Pool per field: a family name, ALLE, or NONE for "do not use this field".
    "pools": {},
    "kameras": [lbl for lbl, _ in KAMERA],  # Mehrfachauswahl
    "fokusse": [lbl for lbl, _ in FOKUS],  # Mehrfachauswahl
    "groesse": KANTE_STANDARD,
    "festesFormat": "2:3",  # applies when "format" is off and nothing is wired in
}


def _lora_mode_index(seed=0):
    """Map a 0-based series counter to a valid 45-slot LoRA preset index."""
    return int(seed) % 45


def _lora_mode_plan(seed=0):
    """Hardcoded 45-shot LoRA preset keyed by the run index."""
    idx = _lora_mode_index(seed)
    
    # Initialize using your exact internal matrix token values
    daten = {
        "kamera": "Porträt",
        "fokus": "Gesicht",
        "pose": {},
        "ausdruck": {},
        "scene": "isolated on a seamless pure white backdrop, professional studio lighting, shadowless, clean minimalist background",
        "style": "professional studio photography, high-end commercial fashion portfolio, crisp sharp details, 85mm lens",
        "person_bits": [],
    }

    # =====================================================================
    # BLOCK 1 (00-03): WHITE STUDIO - FRONTAL FACIAL ANCHORS
    # =====================================================================
    if 0 <= idx <= 3:
        if idx == 0:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Gelassen"}
            daten["scene"] = "isolated on a seamless pure white backdrop, professional studio lighting configuration, clean setup"
            daten["pose"] = {
                "haltung": "standing",
                "raum": "the subject centered in the middle ground",
                "koerper": "facing the camera directly",
                "arme": "arms hanging relaxed at the sides",
                "beine": "legs closed together",
                "spannung": "with an upright posture",
            }
        elif idx == 1:
            daten["kamera"] = "Detail"
            daten["fokus"] = "Augen"
            daten["ausdruck"] = {"stimmung": "Sanftes Lächeln"}
            daten["scene"] = "set against a pristine solid white studio backdrop, bright wrapping softbox panel illumination"
            daten["style"] = "premium editorial fashion headshot portraiture, sharp focal clarity, clean diffuse studio light pass"
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Leicht zur Seite gedreht",
                "arme": "Eine Hand am Gesicht",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
        elif idx == 2:
            daten["kamera"] = "Porträt"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Ernst"}
            daten["scene"] = "positioned on a seamless minimalist white backdrop, high-end commercial flash setup, shadowless"
            daten["style"] = "commercial beauty portfolio photography, crisp high-end textures, flawless zero-shadow rendering"
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Vordergrund",
                "koerper": "Dreiviertelansicht",
                "arme": "Hände auf den Hüften",
                "beine": "Leicht geöffnet",
                "spannung": "Schultern zurück",
            }
        elif idx == 3:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Augen"
            daten["ausdruck"] = {"stimmung": "Konzentriert"}
            daten["scene"] = "isolated on a clean uniform pure white backdrop, perfectly calibrated studio key lights"
            daten["style"] = "magazine headshot studio portfolio, crisp sharp optical separation, clean commercial beauty setup"
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Dreiviertelansicht",
                "arme": "Hände auf den Hüften",
                "beine": "Geschlossen",
                "spannung": "Aufrecht",
            }

    # =====================================================================
    # BLOCK 2 (04-08): WHITE STUDIO - PROFILE ANCHORS
    # =====================================================================
    elif 4 <= idx <= 8:
        daten["fokus"] = "Gesicht"
        if idx == 4:
            daten["kamera"] = "Nahaufnahme"
            daten["ausdruck"] = {"stimmung": "Stoisch"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Am Fenster",
                "koerper": "Im Profil",
                "arme": "Vor der Brust verschränkt",
                "beine": "Geschlossen",
                "spannung": "Aufrecht",
            }
            daten["scene"] = "isolated on a seamless pure white backdrop, left side profile viewpoint orientation, clean studio layout"
        elif idx == 5:
            daten["kamera"] = "Porträt"
            daten["ausdruck"] = {"stimmung": "Gelassen"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Im Profil",
                "arme": "Eine Hand am Gesicht",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
            daten["scene"] = "set against a pristine white studio backdrop, left profile angle showing the right side of her face"
        elif idx == 6:
            daten["kamera"] = "Nahaufnahme"
            daten["ausdruck"] = {"stimmung": "Ernst"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Am Fenster",
                "koerper": "Im Profil",
                "arme": "Seitlich hängend",
                "beine": "Geschlossen",
                "spannung": "Schultern zurück",
            }
            daten["scene"] = "isolated on a seamless pure white backdrop, right side profile viewpoint orientation, sharp setup"
        elif idx == 7:
            daten["kamera"] = "Porträt"
            daten["ausdruck"] = {"stimmung": "Entspannt"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Im Profil",
                "arme": "Hände auf den Hüften",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
            daten["scene"] = "set against a clean uniform white backdrop, elegant profile angled viewpoint tracking configuration"
        elif idx == 8:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["ausdruck"] = {"stimmung": "Unbeeindruckt"}
            daten["pose"] = {
                "haltung": "Sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände im Schoß",
                "beine": "Übereinandergeschlagen",
                "spannung": "Entspannt",
            }
            daten["scene"] = "isolated on a seamless pure white backdrop, clean upper body mid-shot framing setup, studio illumination"

    # =====================================================================
    # BLOCK 3 (09-13): WHITE STUDIO - POSTURE & BODY ANCHORS
    # =====================================================================
    elif 9 <= idx <= 13:
        daten["scene"] = "isolated on a seamless pure white backdrop, professional studio lighting, shadowless, clean minimalist background"
        daten["style"] = "professional studio photography, high-end commercial fashion portfolio, crisp sharp details, 85mm lens"
        if idx == 9:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["ausdruck"] = {"stimmung": "Entspannt"}
            daten["pose"] = {
                "haltung": "Auf einem Hocker sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände auf den Knien",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }
        elif idx == 10:
            daten["kamera"] = "Ganzkörper"
            daten["fokus"] = "Ganze Figur"
            daten["ausdruck"] = {"stimmung": "Selbstbewusst"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Leicht zur Seite gedreht",
                "arme": "Eine Hand am Gesicht",
                "beine": "Leicht geöffnet",
                "spannung": "Schultern zurück",
            }
        elif idx == 11:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["ausdruck"] = {"stimmung": "Zufrieden"}
            daten["pose"] = {
                "haltung": "Sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände auf den Knien",
                "beine": "Übereinandergeschlagen",
                "spannung": "Entspannt",
            }
        elif idx == 12:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["ausdruck"] = {"stimmung": "Neutral"}
            daten["pose"] = {
                "haltung": "Angelehnt",
                "raum": "An der Wand",
                "koerper": "Dreiviertelansicht",
                "arme": "Vor der Brust verschränkt",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
        elif idx == 13:
            daten["kamera"] = "Ganzkörper"
            daten["fokus"] = "Ganze Figur"
            daten["ausdruck"] = {"stimmung": "Selbstbewusst"}
            daten["pose"] = {
                "haltung": "Auf dem Boden sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hinter sich abgestützt",
                "beine": "Ausgestreckt",
                "spannung": "Rücken durchgedrückt",
            }
               
    # =====================================================================
    # BLOCK 4 (14-18): ORGANIC NATURE - ENVIRONMENT CONTEXT BREAKERS
    # =====================================================================
    elif 14 <= idx <= 18:
        if idx == 14:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["ausdruck"] = {"stimmung": "Gelassen"}
            daten["scene"] = "standing deep inside a lush green pine forest, volumetric sunbeams filtering through the tree canopy"
            daten["style"] = "natural light outdoor photography, golden hour rim lighting, beautiful organic lens flare, soft shallow depth of field"
            daten["person_bits"] = ["wearing a basic soft gray crewneck cotton t-shirt with loose fabric folds"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Zwischen Möbeln",
                "koerper": "Frontal zur Kamera",
                "arme": "Seitlich hängend",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
            
        elif idx == 15:
            daten["kamera"] = "Porträt"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Entspannt"}
            daten["scene"] = "positioned in a dense forest clearing, surrounded by massive tall evergreen trees and wild ferns"
            daten["style"] = "vibrant outdoor editorial portraiture, soft diffused morning daylight, crisp sharp leaf detail textures"
            daten["person_bits"] = ["wearing a classic plain gray short-sleeve cotton t-shirt with slight shadows on the chest"]
            daten["pose"] = {
                "haltung": "Angelehnt",
                "raum": "Am Fenster",
                "koerper": "Dreiviertelansicht",
                "arme": "Eine Hand am Gesicht",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
            
        elif idx == 16:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["ausdruck"] = {"stimmung": "Zufrieden"}
            daten["scene"] = "standing on an earthy dirt path winding through a deep woodland area with scattered autumn leaves"
            daten["style"] = "moody lifestyle nature portfolio, late afternoon sun flares, warm golden wash tones, cinematic framing"
            daten["person_bits"] = ["wearing a casual everyday gray crewneck t-shirt, lightweight organic cotton material"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Gehend durch den Raum",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände in den Hosentaschen",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
            
        elif idx == 17:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Augen"
            daten["ausdruck"] = {"stimmung": "Verträumt"}
            daten["scene"] = "low-angle perspective in a dense forest environment, thick forest floor covered in green moss"
            daten["style"] = "high-end commercial outdoor capture, crisp environmental depth of field, sharp background bokeh circles"
            daten["person_bits"] = ["wearing a simple soft gray knit cotton t-shirt with natural creases along the waist"]
            daten["pose"] = {
                "haltung": "Hockend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Arme umschlingen die Knie",
                "beine": "Angewinkelt",
                "spannung": "Zusammengekauert",
            }
            
        else: # Index 18
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Hände"
            daten["ausdruck"] = {"stimmung": "Sanftes Lächeln"}
            daten["scene"] = "resting in a sun-drenched wooded grove, soft sunbeams breaking through high branches"
            daten["style"] = "airy cinematic outdoor photography, brilliant specular sun flare artifacts, rich organic color rendering"
            daten["person_bits"] = ["wearing a minimalist short-sleeve plain gray t-shirt with clean shoulder seams"]
            daten["pose"] = {
                "haltung": "Auf dem Boden sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände im Schoß",
                "beine": "Übereinandergeschlagen",
                "spannung": "Entspannt",
            }

    # =====================================================================
    # BLOCK 5 (19-23): NIGHT CYBER CITY - CHROMATIC CONTEXT BREAKERS
    # =====================================================================
    elif 19 <= idx <= 23:
        if idx == 19:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["ausdruck"] = {"stimmung": "Selbstbewusst"}
            daten["scene"] = "walking along a wet asphalt sidewalk in a dark neon-lit city street environment at night, colorful pink light reflections on puddles"
            daten["style"] = "cinematic nighttime photography, moody anamorphic lighting, striking pink and vibrant cyan blue ambient bokeh color tones"
            daten["person_bits"] = ["wearing a heavy leather motorcycle zip-up jacket with a structured collar, silver asymmetric zipper tracks"]
            daten["pose"] = {
                "haltung": "Gehend",
                "raum": "Gehend durch den Raum",
                "koerper": "Leicht zur Seite gedreht",
                "arme": "Seitlich hängend",
                "beine": "Leicht geöffnet",
                "spannung": "Angespannt",
            }
            
        elif idx == 20:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Konzentriert"}
            daten["scene"] = "positioned in a dark urban alleyway setting at night, vibrant cyan blue glowing neon shop signs in blurred background"
            daten["style"] = "high-contrast cyberpunk aesthetic portraiture, intense dual-tone neon color grading, sharp specular skin reflections"
            daten["person_bits"] = ["wearing a premium thick leather jacket with metallic zippers and dual collar snaps, open front design"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Vor der Brust verschränkt",
                "beine": "Geschlossen",
                "spannung": "Schultern zurück",
            }
            
        elif idx == 21:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["ausdruck"] = {"stimmung": "Herausfordernd"}
            daten["scene"] = "standing in front of a futuristic storefront window displaying glowing pink neon geometric light tube installations"
            daten["style"] = "editorial fashion nighttime shoot, deep high-contrast shadows, rich saturated ambient magenta color wash, 50mm lens look"
            daten["person_bits"] = ["wearing an edgy leather biker jacket with prominent shoulder padding and silver metallic hardware details"]
            daten["pose"] = {
                "haltung": "Angelehnt",
                "raum": "Am Fenster",
                "koerper": "Dreiviertelansicht",
                "arme": "Hände auf den Hüften",
                "beine": "Leicht geöffnet",
                "spannung": "Entspannt",
            }
            
        elif idx == 22:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["ausdruck"] = {"stimmung": "Ernst"}
            daten["scene"] = "on a wet metropolitan street pavement at night, surrounded by towering city buildings and soft glowing ambient street lamps"
            daten["style"] = "moody urban cinematic photography, sharp out-of-focus background traffic bokeh lights, strong directional side illumination"
            daten["person_bits"] = ["wearing a structured heavy leather zip-up coat, thick textured leather material with visible matte grain folds"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Seitlich hängend",
                "beine": "Leicht geöffnet",
                "spannung": "Aufrecht",
            }
            
        else: # Index 23
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Angespannt"}
            daten["scene"] = "under a dark industrial scaffolding overhang on a city sidewalk, intense pink and purple neon signs reflecting on the wet ground"
            daten["style"] = "avant-garde street photography, high-intensity color saturation, brilliant reflection detailing, anamorphic lens flares"
            daten["person_bits"] = ["wearing a sharp modern leather motorcycle jacket with sleek lapels and buttoned wrist cuff adjustments"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Leicht zur Seite gedreht",
                "arme": "Eine Hand am Gesicht",
                "beine": "Leicht geöffnet",
                "spannung": "Angespannt",
            }

    # =====================================================================
    # BLOCK 6 (24-28): LOW-LIGHT CHIAROSCURO - HAIR & SHADOW BREAKERS
    # =====================================================================
    elif 24 <= idx <= 28:
        if idx == 24:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Rücken"
            daten["ausdruck"] = {"stimmung": "Nachdenklich"}
            daten["scene"] = "positioned inside a cozy dim room near a roaring brick fireplace, dark moody indoor scene background"
            daten["style"] = "high-contrast chiaroscuro photography, dynamic directional lighting from a warm amber flame source, deep dramatic shadows"
            daten["person_bits"] = ["hair completely styled up into a tight clean high bun, exposing the neck and ears, wearing a simple top with thin spaghetti shoulder straps"]
            daten["pose"] = {
                "haltung": "Auf einem Hocker sitzend",
                "raum": "Bildmitte",
                "koerper": "Von hinten",
                "arme": "Hände auf den Knien",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }
            
        elif idx == 25:
            daten["kamera"] = "Porträt"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Wehmütig"}
            daten["scene"] = "resting in a shadowy rustic living space, illuminated by the bright amber glow of an open hearth"
            daten["style"] = "fine art low-light portraiture, intimate warm side-lighting, high-contrast deep black shadow values"
            daten["person_bits"] = ["hair styled into a neat high bun exposing the bare shoulders, wearing a minimalist camisole with thin straps"]
            daten["pose"] = {
                "haltung": "Sitzend",
                "raum": "Bildmitte",
                "koerper": "Im Profil",
                "arme": "Hände auf den Knien",
                "beine": "Übereinandergeschlagen",
                "spannung": "Entspannt",
            }
            
        elif idx == 26:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Dekolleté"
            daten["ausdruck"] = {"stimmung": "Melancholisch"}
            daten["scene"] = "sitting in a low-lit historic library room, warm light from a crackling stone fireplace nearby"
            daten["style"] = "cinematic indoor photography, intense directional amber illumination, rich shadow textures, soft focus backdrop"
            daten["person_bits"] = ["hair pulled up tightly into a sleek top knot bun, wearing a plain everyday spaghetti strap top"]
            daten["pose"] = {
                "haltung": "Auf einem Knie",
                "raum": "Bildmitte",
                "koerper": "Dreiviertelansicht",
                "arme": "Hände auf den Knien",
                "beine": "Ein Knie angewinkelt",
                "spannung": "Entspannt",
            }
            
        elif idx == 27:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Rücken"
            daten["ausdruck"] = {"stimmung": "Grüblerisch"}
            daten["scene"] = "in a dark minimalist den environment, a single active fireplace casting long deep shadows"
            daten["style"] = "moody artistic portfolio capture, extreme directional lighting scheme, dramatic drop-shadows, high contrast"
            daten["person_bits"] = ["hair bound neatly into a clean tight high bun layout, wearing a lightweight strappy top with soft fabric creases"]
            daten["pose"] = {
                "haltung": "Auf einem Stuhl sitzend",
                "raum": "Bildmitte",
                "koerper": "Von hinten",
                "arme": "Hände auf den Knien",
                "beine": "Geschlossen",
                "spannung": "Zusammengesunken",
            }
            
        else: # Index 28
            daten["kamera"] = "Porträt"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Lustvoll"}
            daten["scene"] = "positioned on the floor of a dim cabin interior, rich dark background with soft flickering firelight"
            daten["style"] = "atmospheric interior fashion photography, warm flame-lit highlights, deep rich ambient shadow grading"
            daten["person_bits"] = ["hair securely styled up into a flawless high bun revealing the neckline, wearing a simple tank top with delicate shoulder straps"]
            daten["pose"] = {
                "haltung": "Zurückgelehnt",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände im Schoß",
                "beine": "Ausgestreckt",
                "spannung": "Entspannt",
            }

    # =====================================================================
    # BLOCK 7 (29-31): BRUTALIST PLAZA - OVERHEAD HARSH LIGHT BREAKERS
    # =====================================================================
    elif 29 <= idx <= 31:
        daten["style"] = "stark high-dynamic-range architectural photography, harsh overhead midday sunlight casting sharp deep black geometric shadows, high contrast"
        daten["person_bits"] = ["hair pulled back tightly into a sleek long low ponytail, clear view of facial frame anatomy, wearing a crisp tailored dark charcoal gray business blazer suit"]
        if idx == 29:
            daten["kamera"] = "Ganzkörper"
            daten["fokus"] = "Ganze Figur"
            daten["ausdruck"] = {"stimmung": "Kühl"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Vor der Brust verschränkt",
                "beine": "Leicht geöffnet",
                "spannung": "Aufrecht",
            }
            daten["scene"] = "standing in an expansive minimalist concrete brutalist architectural plaza courtyard, large surrounding gray concrete stone pavement, sharp stairs visible in background"
        elif idx == 30:
            daten["kamera"] = "Ganzkörper"
            daten["fokus"] = "Ganze Figur"
            daten["ausdruck"] = {"stimmung": "Ernst"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Seitlich hängend",
                "beine": "Geschlossen",
                "spannung": "Aufrecht",
            }
            daten["scene"] = "positioned in a massive gray stone architectural plaza, clean straight lines and tall modular raw concrete brutalist structures towering in background"
        elif idx == 31:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["ausdruck"] = {"stimmung": "Herausfordernd"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Im Profil",
                "arme": "Hände auf den Hüften",
                "beine": "Leicht geöffnet",
                "spannung": "Schultern zurück",
            }
            daten["scene"] = "positioned against a colossal raw beige plaster and concrete architectural wall installation in a sunlit open courtyard"

    # =====================================================================
    # BLOCK 8 (32-38): FINALE CURATION & DATASET ENRICHMENT
    # =====================================================================
    elif 32 <= idx <= 38:
        if idx == 32:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Gelassen"}
            daten["scene"] = "studio setting, clean uniform solid mid-grey backdrop, perfectly calibrated studio key lights"
            daten["style"] = "magazine editorial headshot portraiture, soft even commercial softbox diffuse panel lighting, zero shadows"
            daten["person_bits"] = ["wearing a thick high-neck olive green wool knit turtleneck sweater, hair falling naturally down and tucked behind the shoulders"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Seitlich hängend",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }
        elif idx == 33:
            daten["kamera"] = "Nahaufnahme"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Konzentriert"}
            daten["scene"] = "studio setting, clean neutral solid mid-grey backdrop, bright diffused ambient studio environment look"
            daten["style"] = "high-end studio lifestyle portraiture, crisp sharp eye detailing, professional micro-contrast rendering"
            daten["person_bits"] = ["wearing a thick high-neck olive green wool knit turtleneck sweater, long hair layout draped forward over both shoulders in front"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände auf den Hüften",
                "beine": "Leicht geöffnet",
                "spannung": "Schultern zurück",
            }
        elif idx == 34:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["ausdruck"] = {"stimmung": "Entspannt"}
            daten["scene"] = "indoors, sitting casually perched on the edge of a rustic distressed dark wooden table, clean white interior room walls, large windows in soft-focus background letting in bright natural daylight"
            daten["style"] = "natural modern lifestyle photography, bright open exposure, shallow depth of field bokeh, organic warm atmosphere"
            daten["person_bits"] = ["hair loose and hanging naturally framing the profile, wearing an unbuttoned casual white long-sleeve cotton button-down shirt with a collar, wearing light blue denim jean shorts"]
            daten["pose"] = {
                "haltung": "Auf einem Stuhl sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hände im Schoß",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }
        elif idx == 35:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["ausdruck"] = {"stimmung": "Konzentriert"}
            daten["scene"] = "nighttime city street background, heavy pouring rain, vibrant neon sign reflections reflecting off wet asphalt pavement, cyberpunk atmosphere"
            daten["style"] = "cinematic action film photography, dramatic low-key lighting with high-saturation pink and teal accents, sharp focus with realistic motion blur elements"
            daten["person_bits"] = ["wet hair strands flying dynamically in the wind, wearing a slick glossy black vinyl rain jacket, highly detailed water droplets on jacket texture"]
            daten["pose"] = {
                "haltung": "Gehend",
                "raum": "Gehend durch den Raum",
                "koerper": "Leicht zur Seite gedreht",
                "arme": "Seitlich hängend",
                "beine": "Leicht geöffnet",
                "spannung": "Angespannt",
            }
        elif idx == 36:
            daten["kamera"] = "Detail"
            daten["fokus"] = "Augen"
            daten["ausdruck"] = {"stimmung": "Intensiv"}
            daten["scene"] = "ultra-close macro framing layout, blurred abstract dark studio background, minimalist lighting setup"
            daten["style"] = "high-magnification commercial macro photography, extreme focal sharpness capturing hyper-detailed iris patterns, flawless realistic skin textures, zero filtering"
            daten["person_bits"] = ["macro focus on stunning sharp eyes, detailed eyelashes, natural eyebrows, subtle hair strands framing the side of the temple"]
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Eine Hand am Gesicht",
                "beine": "Geschlossen",
                "spannung": "Aufrecht",
            }
        elif idx == 37:
            daten["kamera"] = "Totale"
            daten["fokus"] = "Raum"
            daten["ausdruck"] = {"stimmung": "Kühl"}
            daten["scene"] = "high-fashion minimalist architectural interior, sitting on a glossy polished dark marble floor with geometric tile patterns running diagonally"
            daten["style"] = "avant-garde editorial fashion photography, high camera angle pointing straight down, bird's-eye perspective, graphic compositional layout"
            daten["person_bits"] = ["hair fanned out symmetrically on the floor behind the head, wearing a crisp oversized structural white designer blazer dress"]
            daten["pose"] = {
                "haltung": "Auf dem Boden sitzend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Hinter sich abgestützt",
                "beine": "Ausgestreckt",
                "spannung": "Rücken durchgedrückt",
            }
        elif idx == 38:
            daten["kamera"] = "Porträt"
            daten["fokus"] = "Gesicht"
            daten["ausdruck"] = {"stimmung": "Gelassen"}
            daten["scene"] = "cozy warm interior coffee shop background, soft blurred plants, large industrial window pane positioned directly behind her"
            daten["style"] = "warm filmic lifestyle portraiture, heavy golden-hour backlighting, soft volumetric god-rays creating a warm atmospheric glow, high dynamic range"
            daten["person_bits"] = ["hair illuminated from behind creating a brilliant golden glowing rim-light effect along the silhouette, wearing a cozy oversized beige knit sweater"]
            daten["pose"] = {
                "haltung": "Auf einem Stuhl sitzend",
                "raum": "Bildmitte",
                "koerper": "Dreiviertelansicht",
                "arme": "Hände auf den Knien",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }
     
    # =====================================================================
    # BLOCK 9 (39-44): RUSTIC FINALE, DIVERSITY & SILHOUETTE ANCHORS
    # =====================================================================
    else:
        if idx == 39:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["scene"] = "leaning back slightly against an old rustic oak wood table, a massive white window frame pane dominates the background landscape"
            daten["style"] = "airy lifestyle portrait photography, bright ambient window daylight, clean minimalist room interior"
            daten["person_bits"] = ["long hair falling straight behind the shoulders, wearing a clean white button-down long-sleeve oxford shirt, light blue denim shorts"]
            daten["ausdruck"] = {"stimmung": "Gelassen"}
            daten["pose"] = {
                "haltung": "Angelehnt",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Vor der Brust verschränkt",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }
        elif idx == 40:
            daten["kamera"] = "Halbtotale"
            daten["fokus"] = "Oberkörper"
            daten["scene"] = "sitting directly perched on a white window sill pane, a dark rustic oak wood table surface is visible to her side"
            daten["style"] = "cinematic interior lifestyle photography, soft directional side-lit window illumination, shallow depth of field bokeh"
            daten["person_bits"] = ["hair loose and hanging naturally framing the posture geometry, wearing a white button-down long-sleeve shirt, denim shorts"]
            daten["ausdruck"] = {"stimmung": "Nachdenklich"}
            daten["pose"] = {
                "haltung": "Auf einem Stuhl sitzend",
                "raum": "An der Raumkante",
                "koerper": "Dreiviertelansicht",
                "arme": "Eine Hand am Gesicht",
                "beine": "Übereinandergeschlagen",
                "spannung": "Entspannt",
            }
        elif idx == 41:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["scene"] = "nighttime dark city street background, heavy pouring rain, vibrant neon sign reflections glaring off wet asphalt pavement"
            daten["style"] = "cinematic action film photography, dramatic low-key lighting with high-saturation pink and teal accents, sharp focus, realistic motion blur"
            daten["person_bits"] = ["wet hair strands flying dynamically in the wind, wearing a slick glossy black vinyl rain jacket with visible water droplets"]
            daten["ausdruck"] = {"stimmung": "Neugierig"}
            daten["pose"] = {
                "haltung": "Gehend",
                "raum": "Gehend durch den Raum",
                "koerper": "Dreiviertelansicht",
                "arme": "Seitlich hängend",
                "beine": "Leicht geöffnet",
                "spannung": "Angespannt",
            }
        elif idx == 42:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["scene"] = "standing outdoors in an expansive minimalist concrete brutalist architectural plaza, raw gray concrete walls in the background"
            daten["style"] = "stark urban editorial fashion photography, direct overhead midday sunlight casting sharp shadows, crisp high dynamic range textures"
            daten["person_bits"] = ["back view portrait, rear perspective looking at the subject from behind, hair bound neatly into a sleek long low ponytail trailing down the upper back, wearing a structured dark charcoal gray business blazer suit jacket seen from behind"]
            daten["ausdruck"] = {"stimmung": "Stoisch"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Von hinten",
                "arme": "Seitlich hängend",
                "beine": "Leicht geöffnet",
                "spannung": "Aufrecht",
            }
        elif idx == 43:
            daten["kamera"] = "Detail"
            daten["fokus"] = "Gesicht"
            daten["scene"] = "isolated completely on a clean seamless pure white studio backdrop, bright wrap-around softbox illumination, shadowless"
            daten["style"] = "extreme high-magnification commercial beauty portraiture, macro focal clarity, hyper-detailed skin textures, zero filtering"
            daten["person_bits"] = ["bare shoulders, unclothed upper chest framing"]
            daten["ausdruck"] = {"stimmung": "Selbstbewusst"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Seitlich hängend",
                "beine": "Geschlossen",
                "spannung": "Aufrecht",
            }
        elif idx == 44:
            daten["kamera"] = "Amerikanisch"
            daten["fokus"] = "Taille"
            daten["scene"] = "resting next to an old rustic oak wood table in front of a massive white window frame pane"
            daten["style"] = "airy lifestyle portrait photography, soft directional ambient window daylight illuminating the subject, clean minimalist interior"
            daten["person_bits"] = ["wearing a clean white button-down long-sleeve oxford shirt and light blue denim shorts"]
            daten["ausdruck"] = {"stimmung": "Selbstbewusst"}
            daten["pose"] = {
                "haltung": "Stehend",
                "raum": "Bildmitte",
                "koerper": "Frontal zur Kamera",
                "arme": "Seitlich hängend",
                "beine": "Geschlossen",
                "spannung": "Entspannt",
            }

    return daten


def _schritt(platz):
    """This field's step size as a fraction of _NENNER.

    sqrt of a prime is irrational, and the fractional part is distributed evenly
    over (0,1). Every field gets a different prime and therefore a beat of its
    own, commensurable with no other.
    """
    wurzel = _WURZELN[platz % len(_WURZELN)] ** 0.5
    return int((wurzel % 1.0) * _NENNER) | 1  # ungerade: nie ein Teiler von 2^32


def _waehle(labels, lauf, platz):
    """Draw one entry for this run."""
    n = len(labels)
    if not n:
        return None
    return labels[((lauf * _schritt(platz)) % _NENNER) * n // _NENNER]


def _pool(quelle, cat, state, kamera_label=None, haltung_label=None,
          stimmung_label=None):
    """The labels this field is allowed to draw from."""
    modul = PB if quelle == "pose" else EB
    gruppen = (PB.HALTUNG_GRUPPEN if quelle == "pose" else EB.STIMMUNG_GRUPPEN)
    gruppenfeld = "haltung" if quelle == "pose" else "stimmung"

    wahl = (state.get("pools") or {}).get(cat, ALLE)
    if wahl == NONE:
        return []
    alle = [lbl for lbl, _ in modul.PRESETS[cat]]
    if cat == gruppenfeld and wahl != ALLE:
        return [l for l in alle if l in gruppen.get(wahl, [])]
    # Two constraints can apply to the same field, and the placement carries
    # both: the framing says how far away the figure may be (KAMERA_RAUM), the
    # posture says whether it can cross the room at all (HALTUNG_RAUM). They
    # have to intersect - answering with whichever comes first would let the
    # other one through.
    if cat == "raum" and kamera_label:
        erlaubt = KAMERA_RAUM.get(kamera_label)
        if erlaubt is not None:
            alle = [l for l in alle if l in erlaubt]
    # Placement, tension, arms and legs all depend on the base posture.
    # "haltung" comes first in FOLGE, so it is settled by the time these are
    # drawn.
    HALTUNG_KOPPLUNG = {"spannung": PB.HALTUNG_SPANNUNG,
                        "arme": PB.HALTUNG_ARME,
                        "beine": PB.HALTUNG_BEINE,
                        "raum": PB.HALTUNG_RAUM}
    tabelle = HALTUNG_KOPPLUNG.get(cat)
    if tabelle is not None and haltung_label:
        erlaubt = tabelle.get(haltung_label)
        if erlaubt is not None:
            return [l for l in alle if l in erlaubt] or alle
    # Eyes, mouth and brows against the mood - see EB.STIMMUNG_NUR_FUER.
    # "stimmung" is first in EB.FOLGE, so the family is settled here.
    if quelle == "ausdruck" and stimmung_label:
        familie = EB.familie_von(stimmung_label)
        gefiltert = [l for l in alle
                     if EB.passt_zur_stimmung(cat, l, familie)]
        return gefiltert or alle
    return alle


def plane(state, lauf):
    """What this photo shows. A pure function - same run, same result."""
    aktiv = state.get("aktiv") or {}
    kamera_label = None
    pose_labels, ausdruck_labels = {}, {}

    for platz, (cat, quelle) in enumerate(FELDER):
        if not aktiv.get(quelle, True):
            continue
        if quelle == "kamera":
            erlaubt = state.get("kameras") or [lbl for lbl, _ in KAMERA]
            kamera_label = _waehle(erlaubt, lauf, platz)
        else:
            # kamera_label is already settled here: the camera is the first
            # field in FELDER and the pose comes after it. The same holds for
            # haltung against spannung - FELDER follows PB.FOLGE, which puts the
            # base posture first and the body tension last.
            label = _waehle(_pool(quelle, cat, state, kamera_label,
                                  pose_labels.get("haltung"),
                                  ausdruck_labels.get("stimmung")), lauf, platz)
            (pose_labels if quelle == "pose" else ausdruck_labels)[cat] = label

    # The focus is drawn after the camera, because it depends on it: a wide
    # shot cannot focus on the lips.
    fokus_label = None
    if aktiv.get("fokus", True) and kamera_label:
        erlaubt = [f for f in KAMERA_FOKUS.get(kamera_label, [])
                   if not state.get("fokusse") or f in state["fokusse"]]
        # If nothing is left after filtering, this photo simply gets no focus
        # at all. Falling back to the full camera list would be more convenient
        # but would defeat the selection: someone who sets "legs and feet only"
        # would still get a neckline on the portrait.
        if pose_labels.get("koerper") == ABGEWANDT:
            erlaubt = [f for f in erlaubt if f not in FOKUS_GESICHT]
        fokus_label = _waehle(erlaubt, lauf, len(FELDER) + 2)

    return {"kamera": kamera_label, "fokus": fokus_label,
            "pose": pose_labels, "ausdruck": ausdruck_labels}


def format_fuer(kamera_label, lauf, state):
    """Which aspect ratio this photo gets."""
    if not state.get("aktiv", {}).get("format", True):
        return state.get("festesFormat") or "2:3"
    erlaubt = KAMERA_FORMATE.get(kamera_label) or list(RATIOS)
    # Its own slot in the step-size list, so the ratio does not move in
    # lockstep with the camera.
    return _waehle(erlaubt, lauf, len(FELDER) + 1)


def masse(kamera_label, lauf, state):
    """Image dimensions for this photo - coupled to the framing."""
    kante = int(state.get("groesse") or KANTE_STANDARD)
    return masse_fuer(format_fuer(kamera_label, lauf, state), kante)


def _wert(modul, cat, label):
    for lbl, wert in modul.PRESETS[cat]:
        if lbl == label:
            return wert
    return None


def bildseed(lauf, state=None):
    """Noise seed for this photo, derived from the run counter.

    Not the counter itself: 0, 1, 2 sit close together in noise space and give
    images that resemble each other. The factor is Knuth's scattering constant
    for 32 bits (2^32 / the golden ratio); it pulls consecutive numbers far
    apart and is still unique - photo 7 always gets the same seed, so the series
    stays recoverable.

    When the "rausch" axis is switched off, the whole series gets the same seed.
    That holds the setting together: the scene text describes "a gothic bed of
    dark wood", not *this* bed - everything it leaves open the model invents out
    of the noise, and with new noise it comes out differently. Pose, expression
    and camera keep varying, since those come from the prompt and not from the
    seed.
    """
    state = state or {}
    if not (state.get("aktiv") or {}).get("rausch", True):
        return int(state.get("serienSeed") or 0) % (1 << 31)
    return (int(lauf) * 2654435761) % (1 << 31)


def _ausdruck_fuer_detail(werte, detail):
    """Couple the expression to how wide the framing is.

    On a wide shot, lashes and brow shape are invisible, yet they still load the
    prompt with face tokens and pull the composition back onto the head.
    Identity = mood only; figure = mood plus coarse expression; full =
    everything.
    """
    if detail == PeB.DETAIL_IDENTITAET:
        keep = {"stimmung"}
    elif detail == PeB.DETAIL_FIGUR:
        keep = {"stimmung", "blick", "mund", "kopf"}
    else:
        return werte
    return {cat: (werte.get(cat) if cat in keep else None) for cat in EB.FOLGE}


# A face that is turned away cannot carry an expression. The mood may still
# read through posture and shoulders, so it stays; eyes, gaze, mouth and brows
# go, and the focus may not sit on the face either.
ABGEWANDT = "Von hinten"
FOKUS_GESICHT = {"Gesicht", "Augen", "Lippen"}
GESICHTSWOERTER = ("smile", "smiling", "grin", "teeth", "eyes", "gaze", "lips",
                   "mouth", "brow", "frown", "pout", "stare", "wink")


def _ausdruck_fuer_koerper(werte, koerper_label):
    """Drop the facial detail when the subject is seen from behind.

    Same mechanism as _ausdruck_fuer_detail, different trigger. Without it, one
    run in six asked for a back view wearing a half-smile and looking into the
    camera - the model answers by twisting the body far enough to show both,
    which is where the spare limbs came from.
    """
    if koerper_label != ABGEWANDT:
        return werte
    # The mood may stay when it reads through posture and shoulders. It may not
    # when its own wording names something on the face - "a beaming, radiant
    # smile" behind a back view is the same contradiction one level up. Decided
    # on the text rather than by taste, so new moods are covered automatically.
    stimmung = werte.get("stimmung") or ""
    if any(w in stimmung.lower() for w in GESICHTSWOERTER):
        stimmung = None
    return {cat: (stimmung if cat == "stimmung" else None) for cat in EB.FOLGE}


KAMERA_DETAIL_FOKUS = {
    "Füße": "extreme close-up macro shot of feet and shoes, low angle floor-level perspective, camera focused tightly on the footwear and ankles",
    "Hände": "extreme close-up macro shot of hands, camera focused tightly on the hands and fingers",
    "Augen": "extreme close-up macro portrait shot, camera focused tightly on the eyes",
    "Lippen": "extreme close-up macro shot of the lips and mouth",
}


def _person_fuer_kamera(person_data, kamera_label, fokus_label=None):
    """Shorten full person_data (JSON) to the camera and focus detail level."""
    if not person_data:
        return ""
    try:
        p = json.loads(person_data) if isinstance(person_data, str) else person_data
    except (TypeError, ValueError):
        print("[Photoshoot] person_data unreadable, person left out.")
        return ""
    if not isinstance(p, dict):
        return ""
    if kamera_label in ("Detail", "Nahaufnahme"):
        if fokus_label == "Füße":
            return PeB.compose_person(p, detail=PeB.DETAIL_FUESSE)
        if fokus_label == "Hände":
            return PeB.compose_person(p, detail=PeB.DETAIL_HAENDE)
    detail = PeB.detail_fuer_kamera(kamera_label)
    return PeB.compose_person(p, detail=detail)


class Krea2Photoshooting:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # A run counter, not a random value: the button in the
                # interface sets it to 0 and puts control_after_generate on
                # increment.
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True}),
            },
            "optional": {
                # Dimensions from outside, e.g. from "Resolution Pixaroma".
                # They apply only when varying the ratio is switched off -
                # otherwise the framing decides the ratio, and a fixed value
                # from outside would contradict it.
                "width_in": ("INT", {"forceInput": True}),
                "height_in": ("INT", {"forceInput": True}),
                # Raw values from the Person Builder (person_data output).
                # Reassembled per framing - a wide shot without eyeshadow, a
                # portrait with everything. Wiring the finished person string in
                # here buys nothing: that one cannot be shortened.
                "person_data": ("STRING", {"forceInput": True}),
                "noise_seed": ("INT", {"forceInput": True}),
                "lora_mode": ("BOOLEAN", {"default": False,
                                          "label_on": "Lora", "label_off": "aus"}),
            },
            "hidden": {
                "ShootingState": ("STRING", {"default": json.dumps(DEFAULT_STATE)}),
            },
        }

    # person / person_data / kamera_label / scene / style at the end: existing
    # workflows keep wiring pose/ausdruck/kamera/width/height/bildseed at the
    # same indices. person_data, kamera_label, scene and style are there for
    # optional packs that replace fields or carry text without changing the main
    # series outputs.
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT", "INT", "INT",
                    "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("pose", "ausdruck", "kamera", "width", "height", "bildseed",
                    "person", "person_data", "kamera_label", "scene", "style")
    FUNCTION = "shoot"
    CATEGORY = "Photoshoot"
    DESCRIPTION = ("Variiert Kamera, Pose, Mimik und Bildformat über eine ganze "
                   "Serie. Person optional per person_data - wird je Einstellung "
                   "gekürzt. person_data und kamera_label am Ende für optionale "
                   "Packs. Der Seed ist der Laufzähler, nicht die Zufallsquelle.")

    def shoot(self, seed=0, ShootingState=None, width_in=None, height_in=None,
              person_data=None, noise_seed=None, lora_mode=False):
        try:
            state = json.loads(ShootingState) if ShootingState else dict(DEFAULT_STATE)
        except (TypeError, ValueError):
            print("[Photoshoot] State unreadable, using defaults.")
            state = dict(DEFAULT_STATE)

        if lora_mode:
            idx = _lora_mode_index(seed)
            preset = _lora_mode_plan(idx)
            
            # 1. Safely resolve your system's mapped dictionary strings
            kamera_token = preset.get("kamera", "Porträt")
            fokus_token = preset.get("fokus", "Gesicht")
            
            # Pull raw tracking text from your system arrays using generator iteration loops
            raw_kamera_text = next((val for key, val in KAMERA if key == kamera_token), "portrait shot, head and shoulders")
            raw_fokus_text = next((val for key, val in FOKUS if key == fokus_token), "with the focus on the face")
            
            plan = {
                "kamera": raw_kamera_text,
                "fokus": raw_fokus_text,
                "pose": preset.get("pose", {}),
                "ausdruck": preset.get("ausdruck", {}),
            }
            
            pose = PB.compose_pose(plan["pose"], "")
            ausdruck = EB.compose_expression(plan["ausdruck"], "")
            
            # 2. Build camera and lighting track layout strings cleanly
            kamera = plan["kamera"]
            if plan["fokus"]:
                kamera = f"{kamera}, {plan['fokus']}" if kamera else plan["fokus"]

            style = preset.get("style") or ""
            if style:
                kamera = f"{kamera}, {style}" if kamera else style

            scene = preset.get("scene") or ""

            # 3. Apply the custom camera-distance person layout helper method
            person = _person_fuer_kamera(person_data, plan["kamera"], plan.get("fokus"))

            bits = []
            for teil in preset.get("person_bits") or []:
                if teil and teil not in bits:
                    bits.append(teil)
            if bits:
                person = person + (", " + ", ".join(bits) if person else ", ".join(bits))

            # Keep manual custom width and height parameters locked completely
            w, h = masse(plan["kamera"], seed, state)

            final_run_seed = int(noise_seed) if noise_seed is not None else 0

            return (pose, ausdruck, kamera, w, h, final_run_seed,
                    person, (person_data if isinstance(person_data, str) else json.dumps(person_data, ensure_ascii=False) if person_data else ""),
                    plan["kamera"], scene, style)

        plan = plane(state, seed)
        detail = PeB.detail_fuer_kamera(plan["kamera"])

        pose_werte = {cat: _wert(PB, cat, plan["pose"].get(cat)) for cat in PB.FOLGE}
        ausdruck_werte = {cat: _wert(EB, cat, plan["ausdruck"].get(cat)) for cat in EB.FOLGE}
        ausdruck_werte = _ausdruck_fuer_detail(ausdruck_werte, detail)
        ausdruck_werte = _ausdruck_fuer_koerper(ausdruck_werte,
                                                plan["pose"].get("koerper"))

        pose = PB.compose_pose(pose_werte, "")
        ausdruck = EB.compose_expression(ausdruck_werte, "")

        fokus_lbl = plan.get("fokus")
        if plan.get("kamera") == "Detail" and fokus_lbl in KAMERA_DETAIL_FOKUS:
            kamera = KAMERA_DETAIL_FOKUS[fokus_lbl]
        else:
            kamera = dict(KAMERA).get(plan["kamera"], "") if plan["kamera"] else ""
            fokus = dict(FOKUS).get(fokus_lbl, "") if fokus_lbl else ""
            if kamera and fokus:
                kamera = kamera + ", " + fokus
            elif fokus:
                kamera = fokus

        person = _person_fuer_kamera(person_data, plan["kamera"], plan.get("fokus"))
        scene = PB.compose_scene(plan["pose"])
        style = ""

        # Pass the raw data through unchanged - a pack downstream can replace
        # fields and re-compose, using kamera_label for the same detail level as
        # here. scene and style stay plain text for optional downstream use and
        # are not wired into prompt assembly yet.
        if person_data is None or person_data == "":
            pd_out = ""
        elif isinstance(person_data, str):
            pd_out = person_data
        else:
            pd_out = json.dumps(person_data, ensure_ascii=False)
        kamera_label = plan["kamera"] or ""

        # When dimensions arrive from outside and the ratio is not being
        # varied, they win - the upstream node then decides the resolution.
        wuerfelt_format = state.get("aktiv", {}).get("format", True)
        if not wuerfelt_format and width_in and height_in:
            w, h = int(width_in), int(height_in)
        else:
            w, h = masse(plan["kamera"], seed, state)
            
        if noise_seed is not None:
            final_run_seed = int(noise_seed)
        else:
            final_run_seed = bildseed(seed, state)

        return (pose, ausdruck, kamera, w, h, final_run_seed,
                person, pd_out, kamera_label, scene, style)


NODE_CLASS_MAPPINGS = {"Krea2Photoshooting": Krea2Photoshooting}
NODE_DISPLAY_NAME_MAPPINGS = {"Krea2Photoshooting": "Photoshoot Serie"}
