"""Real named HOME + shared-cache volume probe using the existing inspected image.
All downloads come from local fixtures. Both private HOME volumes and the shared
cache volume are uniquely named, labeled, recorded and removed after validation.
"""
import os
from pathlib import Path
import runpy

os.environ['SHARED_CACHE_STORAGE'] = 'named'
runpy.run_path(str(Path(__file__).with_name('shared_cache_smoke.py')), run_name='__main__')
