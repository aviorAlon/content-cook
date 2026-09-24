"""坏插件：导入时调了 sys.exit——加载器照样兜住，程序不退出。"""

import sys

STORYSTUDIO_API = 1

sys.exit(3)
