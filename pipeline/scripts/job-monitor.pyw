"""Launch OpenShelf Studio, owning its dashboard and consumer processes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from openshelf.pipeline.job_monitor_web import main

if __name__ == '__main__':
    main()
