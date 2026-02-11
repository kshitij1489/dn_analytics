import os
import glob
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# We are in installers/ folder. Root is one level up.
project_root = os.path.abspath(os.path.join(os.getcwd(), '..'))

# Collect Prophet & CmdStanPy
datas = []
binaries = []
hiddenimports = []

for pkg in ['prophet', 'cmdstanpy', 'statsmodels', 'sklearn', 'joblib', 'lightgbm', 'xgboost']:
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except Exception:
        print(f"Warning: could not collect '{pkg}' — skipping (may not be installed)")

# Bundle libomp (OpenMP runtime) — required by LightGBM and XGBoost.
# It's a system-level C library, so collect_all() won't find it.
# Check .venv/lib first (dev setup), then Homebrew default location.
_libomp_candidates = [
    os.path.join(project_root, '.venv', 'lib', 'libomp.dylib'),
    '/opt/homebrew/opt/libomp/lib/libomp.dylib',
    '/opt/local/lib/libomp/libomp.dylib',
]
_libomp_found = False
for _libomp_path in _libomp_candidates:
    if os.path.isfile(_libomp_path):
        # Bundle into top-level dir — PyInstaller puts all binaries there,
        # and both LightGBM/XGBoost dylibs will find it via loader search paths.
        binaries.append((_libomp_path, '.'))
        print(f"Bundling libomp from: {_libomp_path}")
        _libomp_found = True
        break
if not _libomp_found:
    print("Warning: libomp.dylib not found — LightGBM/XGBoost may fail at runtime in .dmg")

a = Analysis(
    ['backend_entry.py'],
    pathex=[project_root],
    binaries=binaries,
    datas=datas + [
        # Data files only (non-Python)
        (os.path.join(project_root, 'data'), 'data'),
        (os.path.join(project_root, 'database'), 'database'),
    ],
    hiddenimports=hiddenimports + [
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
        # Item demand ML modules (imported behind try/except — PyInstaller can't trace)
        'src.core.learning.revenue_forecasting.item_demand_ml',
        'src.core.learning.revenue_forecasting.item_demand_ml.dataset',
        'src.core.learning.revenue_forecasting.item_demand_ml.features',
        'src.core.learning.revenue_forecasting.item_demand_ml.model_io',
        'src.core.learning.revenue_forecasting.item_demand_ml.predict',
        'src.core.learning.revenue_forecasting.item_demand_ml.train',
        'src.core.learning.revenue_forecasting.item_demand_ml.backtest_point_in_time',
        'src.core.learning.revenue_forecasting.backtest_point_in_time',
        'src.core.forecast_bootstrap',
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
