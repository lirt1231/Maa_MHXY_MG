import os


if os.environ.get("MAA_DIRECT_RESOURCE") != "1":
    from .action import *
    from .recognition import *
    from .sink import *
