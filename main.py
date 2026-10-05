"""
Compatibility entrypoint for environments configured with 'python main.py' (such as default Pterodactyl Python eggs).
Delegates execution to start.py.
"""
import runpy

if __name__ == "__main__":
    runpy.run_path("start.py", run_name="__main__")
