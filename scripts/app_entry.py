"""PyInstaller entry script (absolute import, so the package resolves when frozen)."""
import sys

from strip_metadata.__main__ import main

sys.exit(main())
