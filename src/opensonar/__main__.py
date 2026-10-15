"""`python -m opensonar …` is the same as the `opensonar` command."""
import sys

from .cli import main

sys.exit(main())
