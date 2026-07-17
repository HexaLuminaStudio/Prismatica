import os
from pathlib import Path
from shutil import copy, copytree
from distutils.sysconfig import get_python_lib

# 1. activate virtual environment
#    $ conda activate YOUR_ENV_NAME
#
# 2. run deploy script
#    $ python deploy.py

args = [
    'nuitka',
    '--standalone',
    '--assume-yes-for-downloads',
    '--mingw64',
    '--windows-icon-from-ico=C:/Users/dell/AppData/Local/Temp/icon.ico',
    '--enable-plugins=pyside6',
    '--clean-cache=all',
    '--show-progress',
    '--show-memory',
    "--include-module=ctypes",
    '--output-dir=E:/Prismatica/build',
    'E:/Prismatica/main.py'
]

dist_folder = Path("E:/Prismatica/build/main.dist")

copied_site_packages = [
    "urllib3",
    "Crypto"
]

copied_standard_packages = [
    "ctypes",
    "secrets.py",
    "hashlib.py",
    "uuid.py",
    "queue.py",
    "random.py",
    "hmac.py"
]

# run nuitka
# https://blog.csdn.net/qq_25262697/article/details/129302819
# https://www.cnblogs.com/happylee666/articles/16158458.html
os.system(" ".join(args))

# copy site-packages to dist folder
site_packages = Path(get_python_lib())

for src in copied_site_packages:
    src = site_packages / src
    dist = dist_folder / src.name

    print(f"Coping site-packages `{src}` to `{dist}`")

    try:
        if src.is_file():
            copy(src, dist)
        else:
            copytree(src, dist)
    except:
        pass


# copy standard library
for file in copied_standard_packages:
    src = site_packages.parent / file
    dist = dist_folder / src.name

    print(f"Coping stand library `{src}` to `{dist}`")

    try:
        if src.is_file():
            copy(src, dist)
        else:
            copytree(src, dist)
    except:
        pass