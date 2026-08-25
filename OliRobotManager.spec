# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources/styles/dark_theme.qss', 'resources/styles'),
        ('resources/logo/oli_manager_logo.ico', 'resources/logo'),
        ('resources/logo/oli_manager_logo.svg', 'resources/logo'),
        ('resources/backlash/backlash_install.zip', 'resources/backlash'),
        ('resources/test_cases/cases.json', 'resources/test_cases'),
        ('resources/test_cases/scripts/snapshot.py', 'resources/test_cases/scripts'),
        ('resources/test_cases/scripts/mros_node_health.sh', 'resources/test_cases/scripts'),
    ],
    hiddenimports=[
        'websockets',
        'paramiko',
        'httpx',
        'keyring',
        'keyring.backends.Windows',
        'PyQt6.QtWebSockets',
        'PyQt6.QtNetwork',
        'PyQt6.QtSvg',
        'PyQt6.QtSvgWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OliRobotManager',
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
    icon=['resources\\logo\\oli_manager_logo.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OliRobotManager',
)
