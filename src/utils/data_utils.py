import json
import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from src.utils.logger import setup_logger

logger = setup_logger("data_utils")

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data')
METADATA_FILE = 'run_metadata.json'


def _data_dir() -> str:
    path = os.path.abspath(DATA_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def save_simulation_data(df: pd.DataFrame, filename: str = 'simulation_results.csv') -> str:
    try:
        filepath = os.path.join(_data_dir(), filename)
        df.to_csv(filepath, index=False)
        logger.info(f"Simulation data saved to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save simulation data: {str(e)}")
        raise


def save_run_metadata(metadata: Dict[str, Any], filename: str = METADATA_FILE) -> str:
    """Persist what produced the current results: data source, seeds, rule."""
    try:
        filepath = os.path.join(_data_dir(), filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"Run metadata saved to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save run metadata: {str(e)}")
        raise


def load_run_metadata(filename: str = METADATA_FILE) -> Optional[Dict[str, Any]]:
    try:
        filepath = os.path.join(_data_dir(), filename)
        if not os.path.exists(filepath):
            return None
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load run metadata: {str(e)}")
        return None


def load_simulation_data(filename: str = 'simulation_results.csv') -> Optional[pd.DataFrame]:
    try:
        filepath = os.path.join(_data_dir(), filename)
        if os.path.exists(filepath):
            logger.info(f"Loading simulation data from {filepath}")
            return pd.read_csv(filepath)
        logger.warning(f"Simulation data file not found: {filepath}")
        return None
    except Exception as e:
        logger.error(f"Failed to load simulation data: {str(e)}")
        raise


def generate_synthetic_data(hours: int = 24, num_servers: int = 10) -> pd.DataFrame:
    try:
        time = np.arange(hours)
        ambient_temp = 15 + 10 * np.sin((time - 6) * np.pi / 12)

        data = []
        for t in time:
            for server_id in range(num_servers):
                load = np.random.randint(0, 100)
                temp = 25 + load * 0.3 + np.random.normal(0, 2)
                power = 100 + load * 2

                data.append({
                    'time': t,
                    'server_id': server_id,
                    'load': load,
                    'temperature': temp,
                    'power': power,
                    'ambient_temp': ambient_temp[t]
                })

        logger.info(f"Generated synthetic data with {len(data)} records")
        return pd.DataFrame(data)
    except Exception as e:
        logger.error(f"Failed to generate synthetic data: {str(e)}")
        raise


def export_results_to_excel(df: pd.DataFrame, filename: str = 'simulation_results.xlsx') -> str:
    try:
        filepath = os.path.join(_data_dir(), filename)

        from src.simulation.metrics import summarize

        summary_rows = []
        for algo, metrics in summarize(df).items():
            summary_rows.append({'algorithm': algo, **metrics})
        summary = pd.DataFrame(summary_rows)

        with pd.ExcelWriter(filepath) as writer:
            df.to_excel(writer, sheet_name='Results', index=False)
            if not summary.empty:
                summary.to_excel(writer, sheet_name='Summary', index=False)

        logger.info(f"Results exported to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to export results to Excel: {str(e)}")
        raise
