"""role_scout — Phase 2 agentic job search pipeline."""

import sys
from pathlib import Path

# Ensure the Phase 1 sibling package is importable even when the editable
# install .pth is not processed (observed with Anaconda Python + uv).
_P1 = Path(__file__).parents[4] / "auto_jobsearch"
_p1_str = str(_P1.resolve())
if _p1_str not in sys.path:
    sys.path.insert(0, _p1_str)
