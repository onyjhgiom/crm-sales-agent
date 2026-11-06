"""统一启动入口，避免使用者去折腾 PYTHONPATH。

    python run.py --demo
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from crm_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
