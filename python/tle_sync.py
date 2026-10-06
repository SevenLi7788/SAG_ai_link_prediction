"""Daily validated BeiDou-3 snapshots; Python 3.10+, standard library only."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TLE_DIR = ROOT / 'tle'
URL = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=beidou&FORMAT=tle'
ORIGINAL_URL = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=beidou-3&FORMAT=tle'
CN = timezone(timedelta(hours=8))

def parse_tle(text):
    lines = [x.rstrip() for x in text.splitlines() if x.strip()]
    if not lines or len(lines) % 3: raise ValueError('Response is not a named 3-line TLE set')
    records=[]; seen=set()
    for i in range(0,len(lines),3):
        name,l1,l2=lines[i:i+3]
        if name.startswith('0 '):name=name[2:]
        for n,line in ((1,l1),(2,l2)):
            if len(line)!=69 or not line.startswith(str(n)+' '):raise ValueError('Invalid TLE line length/type')
            checksum=sum(int(c) if c.isdigit() else 1 if c=='-' else 0 for c in line[:68])%10
            if not line[68].isdigit() or checksum!=int(line[68]):raise ValueError('TLE checksum mismatch')
        if l1[2:7]!=l2[2:7] or not l1[2:7].isdigit():raise ValueError('Satellite ID mismatch or unsupported ID')
        sid=int(l1[2:7])
        if sid in seen:raise ValueError('Duplicate satellite ID')
        seen.add(sid)
        yy=int(l1[18:20]);year=2000+yy if yy<57 else 1900+yy
        day=float(l1[20:32]);days=(datetime(year+1,1,1)-datetime(year,1,1)).days
        if not 1<=day<days+1:raise ValueError('Invalid TLE epoch')
        epoch=datetime(year,1,1,tzinfo=timezone.utc)+timedelta(days=day-1)
        if not 0<=float(l2[8:16])<=180 or float(l2[52:63])<=0:raise ValueError('Invalid orbit elements')
        records.append({'name':name,'id':sid,'epoch':epoch,'lines':(name,l1,l2)})
    return records

def is_beidou3(record):
    # Deliberately excludes the BEIDOU-3S experimental group and BEIDOU-2.
    return bool(re.match(r'^BEIDOU-3\s',record['name'],re.I))

def atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.download_',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def latest_tle(folder=TLE_DIR):
    """Newest successfully downloaded, checksum-verified snapshot, not file mtime."""
    candidates=[]
    for path in Path(folder).glob('beidou3_*.tle'):
        try:
            meta=json.loads(path.with_suffix('.json').read_text('utf-8'))
            data=path.read_bytes();records=parse_tle(data.decode('ascii'))
            assert all(is_beidou3(r) for r in records)
            assert meta['sha256']==hashlib.sha256(data).hexdigest()
            assert meta['source_url']==URL and meta['satellite_count']==len(records)
            downloaded=datetime.fromisoformat(meta['downloaded_at_utc'])
            assert downloaded.tzinfo is not None
            candidates.append((downloaded,path,records,meta))
        except (ValueError,KeyError,AssertionError,OSError,UnicodeError):continue
    if not candidates:raise FileNotFoundError('No validated BeiDou-3 snapshot in '+str(folder))
    return max(candidates,key=lambda x:(x[0],x[1].name))

def sync(folder=TLE_DIR,force=False):
    now=datetime.now(timezone.utc)
    if not force:
        try:
            previous=latest_tle(folder)
            if previous[0].astimezone(CN).date()==now.astimezone(CN).date():return previous[1]
        except FileNotFoundError:pass
    request=urllib.request.Request(URL,headers={'User-Agent':'BeiDou3-Research-TLE-Sync/1.0','Accept':'text/plain'})
    with urllib.request.urlopen(request,timeout=45) as response:
        if response.status!=200:raise RuntimeError('HTTP '+str(response.status))
        raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise ValueError('Unexpected response size')
    records=[r for r in parse_tle(raw.decode('ascii')) if is_beidou3(r)]
    if not records:raise ValueError('No BEIDOU-3 satellites in response')
    now=datetime.now(timezone.utc)
    if any(r['epoch']>now+timedelta(days=2) for r in records):raise ValueError('TLE epochs unexpectedly in future')
    data=('\n'.join('\n'.join(r['lines']) for r in records)+'\n').encode('ascii')
    path=Path(folder)/('beidou3_'+now.astimezone(CN).strftime('%Y-%m-%d_%H%M%S_%f')+'_UTC8.tle')
    meta={'source_url':URL,'original_requested_url':ORIGINAL_URL,'filter':'^BEIDOU-3\\s; excludes BEIDOU-3S and BEIDOU-2',
          'downloaded_at_utc':now.isoformat(),'filename_timezone':'UTC+08:00','satellite_count':len(records),
          'satellite_ids':[r['id'] for r in records],'epoch_min_utc':min(r['epoch'] for r in records).isoformat(),
          'epoch_max_utc':max(r['epoch'] for r in records).isoformat(),'sha256':hashlib.sha256(data).hexdigest()}
    atomic_write(path,data)
    atomic_write(path.with_suffix('.json'),json.dumps(meta,ensure_ascii=False,indent=2).encode('utf-8'))
    return path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force',action='store_true',help='Fetch again even if today is already synchronized')
    parser.add_argument('--latest',action='store_true',help='Only print newest valid local snapshot')
    args=parser.parse_args()
    try:
        path=latest_tle()[1] if args.latest else sync(force=args.force)
        print(path)
    except Exception as exc:
        TLE_DIR.mkdir(exist_ok=True)
        with (TLE_DIR/'sync_errors.log').open('a',encoding='utf-8') as f:f.write(datetime.now(timezone.utc).isoformat()+' '+str(exc)+'\n')
        parser.exit(1,'TLE synchronization failed: '+str(exc)+'\n')

if __name__=='__main__':main()
