"""让 `python api/server.py` 也能直接跑起来（此时 api/ 不在包路径里）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
