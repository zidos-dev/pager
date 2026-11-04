"""Legacy entry point: units installed as cc-tgbot still start this file; it runs zidos_pager.py."""
import os
import runpy
import sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zidos_pager.py")
runpy.run_path(sys.argv[0], run_name="__main__")
