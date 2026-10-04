"""Fixed eye-exam vocabulary. Each slit-lamp section also has a free-text
"other findings" box per eye for anything not listed."""

EYES = [("right", "Right Eye (OD)"), ("left", "Left Eye (OS)")]

DISTANCE_VA = ["6/5", "6/6", "6/9", "6/12", "6/18", "6/24", "6/36", "6/60", "3/60", "2/60", "1/60", "CF", "HM", "PL", "NPL"]
NEAR_VA = ["N5", "N6", "N8", "N10", "N12", "N14", "N18", "N24", "N36", "N48"]
MOODS = ["Normal", "Anxious", "Depressed", "Agitated", "Flat", "Elevated"]
PUPIL_SHAPES = ["Round", "Oval", "Irregular", "Peaked"]
PUPIL_REACTIONS = ["Brisk", "Sluggish", "Non-reactive"]
APD_GRADES = ["None", "1+", "2+", "3+", "4+"]
REFRACTION_METHODS = [("autorefractor", "Autorefractor"), ("retinoscope", "Retinoscope"), ("subjective", "Subjective")]

# The 13 slit-lamp sections, in examination order.
SLIT_LAMP_SECTIONS = [
    ("tear_film", "Tear Film", ["Normal", "Dry", "Excessive tearing", "Debris", "Reduced break-up time"]),
    ("lid", "Lid", ["Normal", "Blepharitis", "Chalazion", "Stye (hordeolum)", "Ptosis", "Entropion", "Ectropion", "Trichiasis", "Lid mass"]),
    ("sclera", "Sclera", ["White", "Scleritis", "Episcleritis", "Icterus", "Thinning"]),
    ("conjunctiva", "Conjunctiva", ["Clear", "Injected", "Chemosis", "Pterygium", "Pinguecula", "Discharge", "Follicles", "Papillae", "Subconjunctival haemorrhage"]),
    ("cornea", "Cornea", ["Clear", "Ulcer", "Scar", "Edema", "Neovascularisation", "Dendritic ulcer", "Haze", "KPs", "Abscess", "Foreign body", "Arcus"]),
    ("anterior_chamber", "Anterior Chamber", ["Deep and quiet", "Shallow", "Cells", "Flare", "Hypopyon", "Hyphaema"]),
    ("pupil", "Pupil", ["Round and reactive", "Irregular", "Fixed", "Dilated", "Constricted", "RAPD"]),
    ("iris", "Iris", ["Normal", "Rubeosis", "Synechiae", "Atrophy", "Coloboma"]),
    ("lens", "Lens", ["Clear", "Nuclear cataract", "Cortical cataract", "Posterior subcapsular cataract", "Mature cataract", "Pseudophakia (IOL)", "Aphakia", "Subluxated"]),
    ("vitreous", "Vitreous", ["Clear", "Haemorrhage", "Floaters", "Vitritis", "PVD"]),
    ("retina", "Retina", ["Normal", "Haemorrhages", "Exudates", "Detachment", "Diabetic retinopathy", "Hypertensive changes", "Pigmentary changes"]),
    ("macula", "Macula", ["Normal", "Oedema", "Drusen", "Hole", "AMD changes", "Scar"]),
    ("optic_disc", "Optic Disc", ["Normal", "Cupped", "Pale", "Swollen", "Disc haemorrhage"]),
]
