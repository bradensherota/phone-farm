"""API-only launcher usable from any working directory."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.argv.append("--server")
from farm.app import main
main()
