import os
import sys
import threading
import webview
from pyatpg_app import app, socketio

# Suppress console for the webview window on Windows
def hide_console():
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32')
        user32 = ctypes.WinDLL('user32')
        hWnd = kernel32.GetConsoleWindow()
        if hWnd != 0:
            user32.ShowWindow(hWnd, 0)

def start_server():
    """Run the Flask-SocketIO server in a background thread."""
    # Use 127.0.0.1 to stay local
    socketio.run(app, host='127.0.0.1', port=5005, debug=False, use_reloader=False)

if __name__ == '__main__':
    # hide_console()

    # 1. Start the Flask server in a daemon thread
    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()

    # 2. Wait a moment for server to initialize
    import time
    time.sleep(1)
    
    # 3. Try to launch the native window
    try:
        print("Launching PyATPG Desktop Application...")
        window = webview.create_window(
            'PyATPG - AI Hardware Trojan Detector',
            'http://127.0.0.1:5005',
            width=1280,
            height=850,
            min_size=(1000, 700),
            background_color='#1a1a1a'
        )
        webview.start()
    except Exception as e:
        print(f"ERROR: Could not launch native window: {e}")
        print("Fallback: Opening PyATPG in your default web browser instead.")
        import webbrowser
        webbrowser.open('http://127.0.0.1:5005')
        
        # Keep the main thread alive for the daemon server thread
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down PyATPG...")
