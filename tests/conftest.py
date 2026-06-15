import sys
from pathlib import Path

# The plugin is a flat module directory (loaded via context["load_sibling"]
# at runtime); make mxml2notation importable from tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
