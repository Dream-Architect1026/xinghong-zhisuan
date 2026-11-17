# -*- mode: python ; coding: utf-8 -*-
import os

# 后端源码一律取自仓库 backend/（SPECPATH 由 PyInstaller 注入）。
_BACKEND = os.path.join(SPECPATH, 'backend')
_ASSETS = os.path.join(SPECPATH, 'assets')


a = Analysis(
    ['xinghong_launcher.py'],
    pathex=[],
    datas=[(os.path.join(_ASSETS, 'fonts', 'NotoSansSC-VF.ttf'), 'fonts'),
           # 四份 legal 正文由 UI 直接读取，必须随包分发到 legal\（源码旁两份同源）。
           (os.path.join(SPECPATH, 'legal'), 'legal'),
           (os.path.join(_BACKEND, 'panel-server.py'), 'backend'),
           # 本机路径配置模块：后端以独立脚本运行，需要与 panel-server.py 同级可导入
           (os.path.join(SPECPATH, 'pathcfg.py'), '.'),
           (os.path.join(_ASSETS, 'bg-starbow.jpg'), 'assets'),
           (os.path.join(_BACKEND, 'web-profiles.json'), 'backend'),
           # R07：后端启动 ComfyUI 依赖同目录的 comfy-paths.yaml
           (os.path.join(_BACKEND, 'comfy-paths.yaml'), 'backend'),
           (os.path.join(_ASSETS, 'icons', 'xinghong.ico'), 'icons')],
    hiddenimports=['ctypes.wintypes', 'http.client', 'http.server', 'importlib.util', 'urllib.parse', 'urllib.request', 'webbrowser', 'csv', 'datetime', 'io', 'shutil', 'tempfile'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio', 'triton', 'functorch',
        'numba', 'llvmlite',
        'matplotlib',
        'pandas', 'pyarrow',
        'scipy', 'sklearn',
        'onnxruntime', 'onnx',
        'IPython', 'jupyter', 'notebook', 'nbformat', 'nbconvert', 'jupyter_client',
        'sympy', 'lxml', 'bs4',
        'PIL', 'zmq', 'tornado',
        'regex', 'jsonschema', 'cryptography',
        'pytest', 'sphinx', 'numexpr',
    ],
    noarchive=False,
    optimize=0,
)

# R38-19 包体裁剪：
#  1) 字体用子集（界面用字覆盖 99%+，缺字走系统回退）；
#  2) 去掉 PySide6/translations：全源无 QTranslator/installTranslator。
#  avcodec-63.dll 不裁：QtMultimedia 的视频页/首帧抓取在用 QMediaPlayer。
a.datas = [d for d in a.datas
           if 'PySide6/translations' not in d[0].replace('\\', '/')]
a.binaries = [b for b in a.binaries
              if 'PySide6/translations' not in b[0].replace('\\', '/')]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='XingHongPanel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # PE 版本资源（由 build_xinghong.py 按 ux_core.APP_VERSION 生成）
    version=os.path.join(SPECPATH, 'version_info_xinghong.txt'),
    icon=os.path.join(_ASSETS, 'icons', 'xinghong.ico'),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='XingHongPanel',
)
