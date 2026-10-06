"""整理后目录的本地路径与配置；不引用原项目目录。"""
from pathlib import Path
import json, os, re
ROOT = Path(__file__).resolve().parents[1]
def load_config():
    path = Path(os.environ.get('NTN_CONFIG', str(ROOT/'stk/config.json')))
    if not path.is_absolute(): path = ROOT/path
    cfg = json.loads(path.read_text('utf-8'))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', cfg['run_id']):
        raise ValueError('Invalid run_id')
    source = Path(cfg['source_tle_path'])
    if not source.is_absolute(): source = ROOT/source
    cfg['source_tle_path'] = str(source.resolve())
    return cfg
