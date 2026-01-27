# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# We are in installers/ folder. Root is one level up.
project_root = os.path.abspath(os.path.join(os.getcwd(), '..'))

a = Analysis(
    ['backend_entry.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        # Data files only (non-Python)
        (os.path.join(project_root, 'data'), 'data'),
        (os.path.join(project_root, 'database'), 'database'),
    ],
    hiddenimports=[
        # Uvicorn internals
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # Top-level project modules (ensure they are compiled into bundle)
        'scripts',
        'scripts.seed_from_backups',
        'services',
        'services.load_orders',
        'services.clustering_service',
        'services.ai_service',
        'utils',
        'utils.api_client',
        'utils.id_generator',
        'utils.clean_order_item',
        'utils.menu_utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='analytics-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='analytics-backend',
)
