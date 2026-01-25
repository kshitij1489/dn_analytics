import sys
import os

def get_base_path():
    """
    Get the base path for resources.
    - If frozen (PyInstaller), returns sys._MEIPASS
    - If dev, returns the project root (assumed to be 3 levels up from this file: src/core/utils/path_helper.py -> root)
    """
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    else:
        # src/core/utils/path_helper.py -> src/core/utils -> src/core -> src -> PROJECT_ROOT
        # actually, this file is in src/core/utils/
        # so dirname is src/core/utils
        # .. -> src/core
        # .. -> src
        # .. -> root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
        return project_root

def get_resource_path(relative_path):
    """
    Get absolute path to a bundled resource (read-only).
    Use this for icons, static config, etc.
    """
    base = get_base_path()
    return os.path.join(base, relative_path)

def get_data_path(relative_path):
    """
    Get absolute path to a writable data file.
    In frozen/release mode, it uses the directory of DB_URL (which Electron sets to userData).
    """
    if getattr(sys, 'frozen', False):
        db_url = os.environ.get("DB_URL")
        if db_url:
            # db_url is absolute path to analytics.db
            base_dir = os.path.dirname(db_url)
            return os.path.join(base_dir, relative_path)
    
    # Fallback to project root in dev
    return get_resource_path(relative_path)
