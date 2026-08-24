#!/usr/bin/env python3
import json
import os
import platform


print(json.dumps({
    "accid": os.environ.get("OLI_ROBOT_ACCID", ""),
    "profile": os.environ.get("OLI_PROFILE_KEY", ""),
    "target": os.environ.get("OLI_TARGET_ROLE", ""),
    "hostname": platform.node(),
}, ensure_ascii=True))