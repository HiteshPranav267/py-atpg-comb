import subprocess
import sys
import os

def build():
    print("🚀 Starting PyATPG Standalone Build Process...")
    
    # 1. Clean previous builds
    if os.path.exists('dist'):
        print("🧹 Cleaning old dist folder...")
    
    # 2. Run PyInstaller
    # Using the existing spec file
    try:
        subprocess.check_call(['pyinstaller', '--noconfirm', 'pyatpg.spec'])
        print("\n✅ Build Successful!")
        print(f"📦 Executable located in: {os.path.abspath('dist/pyatpg')}")
        if sys.platform == 'win32':
            print("📁 You can find 'pyatpg.exe' inside that folder.")
        else:
            print("📁 You can find the 'pyatpg' binary inside that folder.")
            
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Build failed with error: {e}")

if __name__ == '__main__':
    build()
