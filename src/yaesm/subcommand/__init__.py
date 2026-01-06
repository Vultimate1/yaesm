# Eagerly import all modules in this package so that any SubcommandBase
# subclasses they define are registered. We need these modules loaded for
# main.py to be able to find all the program subcommands by asking for the
# subclasses of the SubcommandBase class. Python does not automatically import
# package submodules, and SubcommandBase.__subclasses__() only reports classes
# from modules that have already been imported.

import pkgutil
import importlib

for m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{m.name}")
