
import os


if os.environ.get("MAA_DIRECT_RESOURCE") != "1":
    from .searchAnswer import SearchQuestions
    from .reco_sjqy import *
    from .searchAnswer import *
    from .ocrNum import *
    from .AIAnswer import *
    from .keju_hybrid import *
