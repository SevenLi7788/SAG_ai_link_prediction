"""Render real exported results, without altering simulation data."""
from pathlib import Path
import csv,json
from datetime import datetime,timedelta
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from project_paths import ROOT, load_config
font=FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
plt.rcParams['font.family']=font.get_name()
plt.rcParams['axes.unicode_minus']=False
cfg=load_config()
out=ROOT/'csv';figdir=ROOT/'stk'
meta=json.loads((out/(cfg['run_id']+'_metadata.json')).read_text('utf-8'))
def read(name):
    with (out/(cfg['run_id']+'_'+name)).open(encoding='utf-8-sig') as f:r=list(csv.DictReader(f))
    return lambda k:np.array([float(x[k]) for x in r])
g=read('stk_link.csv');c=read('channel_result.csv');t=g('Time_s')
fig,ax=plt.subplots(3,1,figsize=(10,8),sharex=True,layout='constrained')
ax[0].plot(t,g('OffAxis_deg'),label='实际离轴角');ax[0].axhline(cfg['beam_half_angle_deg'],color='#c44e52',ls='--',label=f"波束半角 {cfg['beam_half_angle_deg']:g}°")
ax[0].set_ylabel('角度 / °');ax[0].legend(loc='upper right')
ax[1].plot(t,g('Distance_m')/1000,color='#287c8e');ax[1].set_ylabel('星机距离 / km')
ax[1].ticklabel_format(axis='y',style='plain',useOffset=False)
ax[2].plot(t,g('Doppler_Hz'),color='#8064a2');ax[2].set_ylabel('几何多普勒 / Hz');ax[2].set_xlabel('样例开始后的时间 / s')
for a in ax:a.grid(alpha=.25)
local_start=datetime.strptime(meta['sample_start_utc'],'%d %b %Y %H:%M:%S.%f')+timedelta(hours=8)
fig.suptitle(f"{meta['selected_satellite']}｜北京时间 {local_start:%Y-%m-%d %H:%M:%S} 起，{meta['actual_duration_s']:g} 秒\n{meta['rows']} 点全部满足 EarthLOS = BeamCover = GeometryAvailable = 1")
fig.savefig(figdir/(cfg['run_id']+'_01_覆盖与运动.png'),dpi=170);plt.close(fig)
fig,ax=plt.subplots(2,1,figsize=(10,6.5),sharex=True,layout='constrained')
ax[0].plot(t,c('ChannelGain_dB'),lw=.8,label='瞬时信道增益');ax[0].plot(t,c('LargeScaleGain_dB'),lw=1.5,label='大尺度增益')
ax[0].set_ylabel('增益 / dB');ax[0].legend()
ax[1].plot(t,c('CSI_Real')*1e6,lw=.8,label='实部');ax[1].plot(t,c('CSI_Imag')*1e6,lw=.8,label='虚部')
ax[1].set_ylabel('复信道系数 × 10^6');ax[1].set_xlabel('样例开始后的时间 / s');ax[1].legend()
for a in ax:a.grid(alpha=.25)
fig.suptitle(f"MATLAB 相关 Rician 简化信道：{meta['rows']} 点增益全部有限\n理想多普勒补偿；不是北斗实测信道或真实通信体制仿真")
fig.savefig(figdir/(cfg['run_id']+'_02_信道增益与CSI.png'),dpi=170);plt.close(fig)
print('Two result charts generated from actual CSV data.')
