"""B210 SAR GUI: single-process FastAPI app + vanilla ES-module JS (no build step).

Run: python -m b210_sar.gui --port 8010. Imports only stdlib, numpy, PyYAML, h5py, fastapi, uvicorn, websockets.
"""

import os as _os

# The live/file views run small matmuls many times a second; BLAS threading costs ~25x the CPU for the same
# wall time (b210-13 Step 4). Pin before numpy is first imported by this process (Controller ruling).
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
           "NUMEXPR_NUM_THREADS"):
    _os.environ[_v] = "1"
