"""Run the flat-layout BeiDou-3 pipeline using a fixed, timestamped TLE snapshot."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import argparse,json,subprocess,sys,os
from tle_sync import ROOT,sync,latest_tle,CN

def prepare(offline=False,period='earliest_today'):
    if not offline:sync()
    downloaded,tle,records,meta=latest_tle()
    now=datetime.now(timezone.utc)
    local_day=now.astimezone(CN).date()
    midnight=datetime.combine(local_day,datetime.min.time(),tzinfo=CN)
    if period=='earliest_today':
        start=midnight.astimezone(timezone.utc)
        stop=(midnight+timedelta(days=1)).astimezone(timezone.utc)-timedelta(milliseconds=1)
        policy='earliest_today_replay'
    elif period=='earliest_available':
        start=min(r['epoch'] for r in records)
        start=start.replace(microsecond=0)+timedelta(seconds=1)
        stop=start+timedelta(hours=48)
        policy='earliest_available_replay'
        local_day=start.astimezone(CN).date()
    elif period=='morning':
        start=(midnight+timedelta(hours=9)).astimezone(timezone.utc)
        stop=(midnight+timedelta(hours=12)).astimezone(timezone.utc)-timedelta(milliseconds=1)
        policy='today_morning_replay'
    else:
        start=max(now,max(r['epoch'] for r in records)).replace(microsecond=0)
        stop=(midnight+timedelta(days=1)).astimezone(timezone.utc)-timedelta(milliseconds=1)
        policy='remaining_today_only'
    if start>=stop:raise ValueError('No remaining same-day search interval; refuse to move to a future day')
    months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    def stk(t):return f'{t.day:02d} {months[t.month-1]} {t.year} {t:%H:%M:%S}.{t.microsecond//1000:03d}'
    cfg=json.loads((ROOT/'stk/config.json').read_text('utf-8'))
    rid='beidou3_'+now.strftime('%Y%m%d_%H%M%S_%f')
    frozen=tle.resolve()  # immutable downloaded snapshot; no duplicate copy
    cfg.update(run_id=rid,source_tle_path=str(frozen),satellite_ids=[r['id'] for r in records],
               search_start_utc=stk(start),search_stop_utc=stk(stop),tle_catalog_path=str(tle),
               tle_downloaded_at_utc=downloaded.isoformat(),constellation='BeiDou-3',tle_offline=offline,
               simulation_local_date=local_day.isoformat(),simulation_timezone='UTC+08:00',search_policy=policy,
               experiment_mode='propagation' if period=='remaining_today' else 'retrospective_replay',
               selection_policy='earliest_valid' if period in ('earliest_available','earliest_today') else 'longest_interval_center',
               tle_epochs_after_search_start=sum(r['epoch']>start for r in records))
    path=ROOT/'stk'/(rid+'_config.json')
    with path.open('x',encoding='utf-8') as f:
        json.dump(cfg,f,ensure_ascii=False,indent=2)
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true',help='Write a run configuration without starting STK/MATLAB')
    parser.add_argument('--offline',action='store_true',help='Explicitly permit newest local snapshot without today synchronization')
    parser.add_argument('--period',choices=['earliest_today','earliest_available','morning','remaining_today'],default='earliest_today',help='Earliest coverage within today in Beijing time (default); other periods require explicit selection')
    args=parser.parse_args()
    config=prepare(args.offline,args.period)
    print('Prepared config:',config,flush=True)
    if args.prepare_only:return
    env=dict(os.environ,NTN_CONFIG=str(config),PYTHONDONTWRITEBYTECODE='1')
    matlab=os.environ.get('MATLAB_EXE','D:/MATLAB/R2024b/bin/matlab.exe')
    stage=str(ROOT/'matlab').replace("'","''")
    commands=[
        [sys.executable,'-B',str(ROOT/'python/generate_covered_scene.py')],
        [matlab,'-batch',f"addpath('{stage}'); run_channel_stage"],
        [sys.executable,'-B',str(ROOT/'python/plot_results.py')],
    ]
    for command in commands:
        subprocess.run(command,env=env,cwd=ROOT,check=True)
    print('Pipeline completed; see csv, matlab and stk for this run_id.')


if __name__=='__main__':main()
