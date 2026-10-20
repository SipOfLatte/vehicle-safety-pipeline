import sys
from pathlib import Path

# Airflow puts the DAGs folder on sys.path when it parses DAGs; tests need the same so `import common` works.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))
