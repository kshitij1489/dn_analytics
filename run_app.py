import sys
from pathlib import Path
import os

if __name__ == '__main__':
    # Ensure the root directory is in sys.path
    root_dir = Path(__file__).parent.resolve()
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))
    
    # Run Streamlit
    # We use os.system or subprocess to launch streamlit pointing to the new app location
    # This is often more reliable than importing stcli for environment inheritance
    app_path = root_dir / "src" / "ui_streamlit" / "app.py"
    print(f"🚀 Launching Analytics App from {app_path}...")
    
    os.system(f"streamlit run {app_path}")
