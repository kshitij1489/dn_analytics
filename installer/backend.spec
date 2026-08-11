import os

block_cipher = None

# We are in installers/ folder. Root is one level up.
project_root = os.path.abspath(os.path.join(os.getcwd(), '..'))

datas = []
binaries = []
hiddenimports = []
data_payloads = []
for filename in [
    'gp_model.pkl',
]:
    path = os.path.join(project_root, 'data', filename)
    if os.path.exists(path):
        data_payloads.append((path, 'data'))

a = Analysis(
    ['backend_entry.py'],
    pathex=[project_root],
    binaries=binaries,
    datas=datas + data_payloads + [
        (os.path.join(project_root, 'database'), 'database'),
    ],
    hiddenimports=hiddenimports + [
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
        'scripts',
        'scripts.seed_from_backups',
        'services',
        'services.load_orders',
        'services.clustering_service',
        'utils',
        'utils.api_client',
        'utils.id_generator',
        'utils.clean_order_item',
        'utils.menu_utils',
        'src.core.forecast_sync',
        'src.core.central_forecast_cache',
        'src.core.central_forecast_projection',
        'src.core.forecast_actuals',
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
