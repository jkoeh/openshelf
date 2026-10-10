"""Launch OpenShelf's authenticated local browser dashboard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from openshelf.pipeline.job_monitor_web import main

if __name__ == '__main__':
    main()
