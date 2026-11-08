"""Isolate tests from the developer's real ~/.config/profile-atlas and ~/.local/share/profile-atlas.

Runs before test modules import profile_atlas (several modules read the config at import time).
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="profile-atlas-test-")
os.environ["PROFILE_ATLAS_CONFIG"] = os.path.join(_tmp, "config")
os.environ["PROFILE_ATLAS_DATA"] = os.path.join(_tmp, "data")
for _k in ("PROFILE_ATLAS_ORG", "PROFILE_ATLAS_TEMPLATE", "PROFILE_ATLAS_WORKSPACE", "PROFILE_ATLAS_KMAP"):
    os.environ.pop(_k, None)
