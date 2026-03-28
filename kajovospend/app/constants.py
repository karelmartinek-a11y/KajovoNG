from pathlib import Path

APP_NAME = 'KájovoSpend'
APP_SLUG = 'kajovospend'
APP_VERSION = '0.1.0'
SCHEMA_VERSION = 1
PROJECT_MARKER = 'kajovospend-project.json'
PROJECT_INPUT_DIR = 'IN'
WORK_DB_NAME = 'work.sqlite3'
PROD_DB_NAME = 'production.sqlite3'
DEFAULT_WORK_DB_PATH = 'db/work.sqlite3'
DEFAULT_PROD_DB_PATH = 'db/production.sqlite3'
REQUIRED_DIRS = [
    PROJECT_INPUT_DIR,
    'documents/originals',
    'documents/quarantine',
    'documents/unrecognized',
    'patterns',
    'logs',
    'backups',
    'exports',
]
BRAND_ROOT = Path(__file__).resolve().parents[2] / 'brand'
