"""STK 12.6 official API: search fixed 5-degree nadir coverage and export a real normal link.
Run: D:/Python/python.exe -u generate_covered_scene.py
Requires installed/licensed STK Engine and agi.stk12, numpy. Original sources are read-only.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import csv
import hashlib
import json
import math
import sys
import traceback
import tempfile
import shutil
import numpy as np
from agi.stk12.stkengine import STKEngine
from agi.stk12.stkdesktop import STKDesktop
from agi.stk12.stkobjects import (AgScenario, AgSatellite, AgSensor, AgFacility, AgAircraft,
    AgVePropagatorGreatArc, AgDataProviderGroup, AgDataPrvTimeVar)

from project_paths import ROOT, load_config
CONFIG = load_config()
C=299792458.0


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def intersection(xs,ys):
    return [(max(a,c),min(b,d)) for a,b in xs for c,d in ys if min(b,d)>max(a,c)]


def intervals(source,target):
    access=source.GetAccessToObject(target)
    access.ComputeAccess()
    result=access.ComputedAccessIntervalTimes
    return [(float(a),float(b)) for a,b in result.ToArray(0,-1)] if result.Count else []


def flags(t,windows):
    v=np.zeros(len(t),dtype=int)
    for a,b in windows:v[(t>=a)&(t<=b)]=1
    return v


def series(obj,provider,group,start,stop,step,indices):
    dp=AgDataProviderGroup(obj.DataProviders.Item(provider))
    result=AgDataPrvTimeVar(dp.Group.Item(group)).Exec(str(start),str(stop),step)
    t=np.array(result.DataSets.GetDataSetByName('Time').GetValues(),dtype=float)
    values=np.column_stack([np.array(result.DataSets.Item(i).GetValues(),dtype=float) for i in indices])
    if not np.isfinite(values).all():raise ValueError(f'Nonfinite {provider}/{group}')
    return t,values


def prepare_inputs():
    folder=ROOT/'tle';folder.mkdir(exist_ok=True)
    original=Path(CONFIG['source_tle_path'])
    if not original.is_file():raise FileNotFoundError(original)
    lines=original.read_text('utf-8').splitlines()
    blocks=[]
    for i,line in enumerate(lines):
        if line.startswith('1 ') and int(line[2:7]) in CONFIG['satellite_ids']:
            if i+1>=len(lines) or not lines[i+1].startswith('2 '):raise ValueError('Malformed TLE')
            blocks.append((int(line[2:7]), '\n'.join([lines[i-1].strip() if i else 'SAT-'+line[2:7],line,lines[i+1]])))
    if sorted(x[0] for x in blocks)!=sorted(CONFIG['satellite_ids']):raise ValueError('Expected exactly one TLE per selected satellite')
    text='\n'.join(x[1] for x in blocks)+'\n'
    selected=folder/(CONFIG['run_id']+'_selected_satellites.tle')
    if selected.exists() and selected.read_text('utf-8')!=text:raise RuntimeError('Input snapshot changed; use a new stage directory')
    selected.write_text(text,encoding='utf-8')
    save(ROOT/'stk'/(CONFIG['run_id']+'_source_manifest.json'),
         {'original_tle':str(original),'original_tle_sha256':digest(original),
          'selected_tle':str(selected),'selected_tle_sha256':digest(selected)})
    return selected


def main():
    out=ROOT/'csv';out.mkdir(parents=True,exist_ok=True)
    if (out/(CONFIG['run_id']+'_stk_link.csv')).exists():raise RuntimeError('Completed geometry output exists: change run_id to avoid overwriting')
    tle=prepare_inputs()
    runtime=CONFIG.get('stk_runtime','engine')
    if runtime not in ('engine','desktop'):raise ValueError('stk_runtime must be engine or desktop')
    if CONFIG['beam_mode']!='fixed_geocentric_nadir':raise ValueError('Unsupported beam mode')
    if CONFIG['sample_interval_s']<=0 or CONFIG['coverage_guard_s']<0:raise ValueError('Invalid sampling/guard')
    print(f'Starting licensed STK runtime: {runtime}',flush=True)
    engine=(STKEngine.StartApplication(noGraphics=True) if runtime=='engine'
            else STKDesktop.StartApplication(visible=False,userControl=False))
    try:
        root=engine.NewObjectRoot() if runtime=='engine' else engine.Root
        root.UnitPreferences.SetCurrentUnit('DistanceUnit','km')
        root.UnitPreferences.SetCurrentUnit('TimeUnit','sec')
        root.UnitPreferences.SetCurrentUnit('AngleUnit','deg')
        root.UnitPreferences.SetCurrentUnit('DateFormat','UTCG')
        root.NewScenario('Stage0924_'+CONFIG['run_id'])
        scene=AgScenario(root.CurrentScenario)
        scene.Epoch=CONFIG['search_start_utc']
        scene.SetTimePeriod(CONFIG['search_start_utc'],CONFIG['search_stop_utc'])
        search_start=float(root.ConversionUtility.ConvertDate('UTCG','EpSec',CONFIG['search_start_utc']))
        search_stop=float(root.ConversionUtility.ConvertDate('UTCG','EpSec',CONFIG['search_stop_utc']))
        root.UnitPreferences.SetCurrentUnit('DateFormat','EpSec')
        # STK Connect's file importer requires an ASCII path on this installation.
        with tempfile.TemporaryDirectory(prefix='stk0924_') as tempdir:
            import_tle=Path(tempdir)/'selected.tle'
            shutil.copyfile(tle,import_tle)
            for ssc in CONFIG['satellite_ids']:
                root.ExecuteCommand(f'ImportTLEFile * "{import_tle}" SSCNumber {ssc} AutoPropagate On')
        sats=[AgSatellite(scene.Children.Item(i)) for i in range(scene.Children.Count)
              if scene.Children.Item(i).ClassName=='Satellite']
        if len(sats)!=len(CONFIG['satellite_ids']):raise RuntimeError('Satellite import count mismatch')
        point=AgFacility(scene.Children.New(8,'CoverageSearchPoint'))
        point.Position.AssignGeodetic(CONFIG['latitude_deg'],CONFIG['longitude_deg'],CONFIG['altitude_km'])
        sensors={};search=[];candidates=[]
        for sat in sats:
            # Explicit attitude, avoiding reliance on user defaults. +Z is geocentric nadir.
            root.ExecuteCommand(f'SetAttitude */Satellite/{sat.InstanceName} Profile NadirECIVel Offset 0')
            sensor=AgSensor(sat.Children.New(20,'Beam'))
            sensor.CommonTasks.SetPatternSimpleConic(CONFIG['beam_half_angle_deg'],.1)
            sensor.CommonTasks.SetPointingFixedAzEl(0.,90.,0)
            sensors[sat.InstanceName]=sensor
            earth=intervals(sat,point);beam=intervals(sensor,point);joint=intersection(earth,beam)
            search.append({'satellite':sat.InstanceName,'earth_intervals_epsec':earth,'beam_intervals_epsec':beam,'joint_intervals_epsec':joint})
            for a,b in joint:candidates.append((b-a,a,b,sat.InstanceName))
            print(f'{sat.InstanceName}: earth={len(earth)} intervals, beam={len(beam)}, joint={len(joint)}',flush=True)
        save(out/(CONFIG['run_id']+'_search_access.json'),{'search_start_utc':CONFIG['search_start_utc'],'search_stop_utc':CONFIG['search_stop_utc'],
                                     'prefilter':'fixed facility at 20 km; every candidate is rechecked with moving aircraft',
                                     'satellites':search})
        if not candidates:raise RuntimeError('No fixed nadir coverage found. Do not fabricate flags; review search period or separate tracking scenario.')
        selected=None;trials=[]
        earliest=CONFIG.get('selection_policy')=='earliest_valid'
        ordered=sorted(candidates,key=lambda x:x[1]) if earliest else sorted(candidates,reverse=True)
        for trial,(_,a,b,name) in enumerate(ordered):
            center=(a+b)/2
            route_start=max(search_start,(a if earliest else center)-30)
            if earliest and selected is not None and route_start>selected[4]:break
            uav=AgAircraft(scene.Children.New(1,f'HAPS_{trial:02d}'))
            route=AgVePropagatorGreatArc(uav.Route)
            route.Method=0
            epoch=route.EphemerisInterval.GetStartEpoch();epoch.SetExplicitTime(str(route_start));route.EphemerisInterval.SetStartEpoch(epoch)
            route.Waypoints.RemoveAll()
            # One continuous northbound GreatArc leg at constant commanded speed and altitude.
            for lat in [CONFIG['latitude_deg'],CONFIG['latitude_deg']+.10]:
                wp=route.Waypoints.Add();wp.Latitude=lat;wp.Longitude=CONFIG['longitude_deg']
                wp.Altitude=CONFIG['altitude_km'];wp.Speed=CONFIG['speed_mps']/1000
            route.Propagate()
            route_stop=float(route.Waypoints.Item(route.Waypoints.Count-1).Time)
            sat=next(x for x in sats if x.InstanceName==name)
            earth=intervals(sat,uav);beam=intervals(sensors[name],uav)
            joint=intersection(intersection(earth,beam),[(route_start,route_stop)])
            trials.append({'satellite':name,'aircraft':uav.InstanceName,'route_start_epsec':route_start,'route_stop_epsec':route_stop,
                           'earth_intervals_epsec':earth,'beam_intervals_epsec':beam,'joint_intervals_epsec':joint})
            if joint:
                eligible=[(l,r) for l,r in joint if r-l>=CONFIG['minimum_normal_duration_s']+2*CONFIG['coverage_guard_s']]
                if not eligible:continue
                left,right=min(eligible,key=lambda x:x[0]) if earliest else max(eligible,key=lambda x:x[1]-x[0]); available=right-left-2*CONFIG['coverage_guard_s']
                if available>=CONFIG['minimum_normal_duration_s']:
                    duration=math.floor(min(CONFIG['normal_sample_duration_s'],available)/CONFIG['sample_interval_s'])*CONFIG['sample_interval_s']
                    if earliest:
                        begin=math.ceil((left+CONFIG['coverage_guard_s'])/CONFIG['sample_interval_s'])*CONFIG['sample_interval_s']
                        duration=math.floor(min(duration,right-CONFIG['coverage_guard_s']-begin)/CONFIG['sample_interval_s'])*CONFIG['sample_interval_s']
                    else:begin=round(((left+right-duration)/2)/CONFIG['sample_interval_s'])*CONFIG['sample_interval_s']
                    end=begin+duration
                    if duration<CONFIG['minimum_normal_duration_s']:continue
                    if selected is None or begin<selected[4]:selected=(sat,uav,earth,beam,begin,end,route_start,route_stop)
                    if not earliest:break
        save(out/(CONFIG['run_id']+'_moving_access.json'),trials)
        if selected is None:raise RuntimeError('Static candidates did not give >=1 s moving coverage')
        sat,uav,earth,beam,begin,end,route_start,route_stop=selected
        step=CONFIG['sample_interval_s']
        times,uav_lla=series(uav,'LLA State','Fixed',begin,end,step,[1,2,3])
        def read(obj,provider,group,indices):
            t,v=series(obj,provider,group,begin,end,step,indices)
            if t.shape!=times.shape or not np.allclose(times,t,atol=1e-7,rtol=0):raise ValueError('Time alignment failure')
            return v
        sat_lla=read(sat,'LLA State','Fixed',[1,2,3])
        sx=read(sat,'Cartesian Position','Fixed',[1,2,3])*1000
        ux=read(uav,'Cartesian Position','Fixed',[1,2,3])*1000
        sv=read(sat,'Cartesian Velocity','Fixed',[1,2,3])*1000
        uv=read(uav,'Cartesian Velocity','Fixed',[1,2,3])*1000
        ua=read(uav,'Cartesian Acceleration','Fixed',[1,2,3])*1000
        delta=sx-ux;distance=np.linalg.norm(delta,axis=1)
        radial=np.sum(delta*(sv-uv),axis=1)/distance;doppler=-radial*CONFIG['fc_hz']/C
        offaxis=np.rad2deg(np.arccos(np.clip(np.sum((-delta)*(-sx),axis=1)/(distance*np.linalg.norm(sx,axis=1)),-1,1)))
        la,lo=np.deg2rad(uav_lla[:,0]),np.deg2rad(uav_lla[:,1]);up=np.column_stack((np.cos(la)*np.cos(lo),np.cos(la)*np.sin(lo),np.sin(la)))
        elev=np.rad2deg(np.arcsin(np.clip(np.sum(delta*up,axis=1)/distance,-1,1)))
        ef,bf=flags(times,earth),flags(times,beam);geom=ef*bf
        if not np.all(geom==1) or not np.all(offaxis<=CONFIG['beam_half_angle_deg']+1e-5):raise RuntimeError('Independent coverage validation failed')
        if not np.allclose(np.diff(times),step,atol=1e-7,rtol=0):raise RuntimeError(f'Not a 1 ms grid: N={len(times)}, min={np.diff(times).min()}, max={np.diff(times).max()}, edges={times[:3]}, {times[-3:]}')
        speed=np.linalg.norm(uv,axis=1)
        error=np.abs(np.diff(distance)/np.diff(times)-(radial[:-1]+radial[1:])/2)
        jump=np.linalg.norm(np.diff(uv,axis=0),axis=1)
        if error.max()>1 or jump.max()>1:raise RuntimeError('Unexpected motion discontinuity in straight sample')
        header=['Time_s','ScenarioTime_s','RunId','TerminalId','TrajectoryId','SatName','SatLat','SatLon','SatAlt_km',
                'SatVx_mps','SatVy_mps','SatVz_mps','UavLat','UavLon','UavAlt_km','UavVx_mps','UavVy_mps','UavVz_mps',
                'UavAx_mps2','UavAy_mps2','UavAz_mps2','Distance_m','RadialVel_mps','Doppler_Hz','Elevation_deg','OffAxis_deg',
                'BeamCover','EarthLOS','BuildingLOS','ShadowFade_dB','GeometryAvailable','DirectLOS']
        with (out/(CONFIG['run_id']+'_stk_link.csv')).open('w',newline='',encoding='utf-8') as f:
            w=csv.writer(f);w.writerow(header)
            for i,t in enumerate(times):
                w.writerow([float(t-begin),float(t),CONFIG['run_id'],uav.InstanceName,'straight_north_50mps',sat.InstanceName,
                    *sat_lla[i],*sv[i],*uav_lla[i],*uv[i],*ua[i],distance[i],radial[i],doppler[i],elev[i],offaxis[i],
                    int(bf[i]),int(ef[i]),1,0.,int(geom[i]),int(geom[i])])
        metadata={'schema_version':3,'config':CONFIG,'selected_satellite':sat.InstanceName,'rows':len(times),
                  'sample_start_utc':root.ConversionUtility.ConvertDate('EpSec','UTCG',str(begin)),
                  'sample_stop_utc':root.ConversionUtility.ConvertDate('EpSec','UTCG',str(end)),
                  'sample_start_epsec':begin,'sample_stop_epsec':end,'actual_duration_s':float(times[-1]-times[0]),
                  'route_start_epsec':route_start,'route_stop_epsec':route_stop,
                  'geometry_available_samples':int(geom.sum()),'offaxis_deg_min_max':[float(offaxis.min()),float(offaxis.max())],
                  'speed_mps_min_max':[float(speed.min()),float(speed.max())],'range_rate_error_max_mps':float(error.max()),
                  'velocity_step_max_mps':float(jump.max()),'stk_api':'agi.stk12 12.6','stk_runtime':runtime,'attitude':'explicit NadirECIVel Offset 0',
                  'beam_pointing':'Fixed Az=0 El=90, +Z axis','blockage_model':'open high-altitude baseline, no building obstacles',
                  'source_tle_sha256':digest(tle),'geometry_csv_sha256':digest(out/(CONFIG['run_id']+'_stk_link.csv')),
                  'created_utc':datetime.now(timezone.utc).isoformat()}
        for key in ('sample_start','sample_stop'):
            utc=datetime.strptime(metadata[key+'_utc'],'%d %b %Y %H:%M:%S.%f').replace(tzinfo=timezone.utc)
            metadata[key+'_beijing']=utc.astimezone(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')
        save(out/(CONFIG['run_id']+'_metadata.json'),metadata)
        print(json.dumps(metadata,ensure_ascii=False,indent=2),flush=True)
    finally:
        engine.ShutDown()


if __name__=='__main__':
    try:main()
    except Exception:
        traceback.print_exc();sys.exit(1)
