import sys
sys.path.append(r'D:\Python\Prismatica-UI')
from app.core.utils import qconfig, cfg

# 标记为首次启动，下一次运行主程序会弹出 GuideWindow
qconfig.set(cfg.FirstLaunch, True)
print("设置完成: cfg.FirstLaunch = True")