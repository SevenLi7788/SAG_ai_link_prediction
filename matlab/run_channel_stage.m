function run_channel_stage()
% 新版正常覆盖样例的MATLAB信道生成。按 run_id 前缀写入整理后的平铺目录。
% MATLAB -batch "run_channel_stage"（当前目录为本文件所在目录）
project_root = fileparts(fileparts(mfilename('fullpath')));
config_path = getenv('NTN_CONFIG');
if isempty(config_path), config_path = fullfile(project_root, 'stk', 'config.json'); end
cfg = jsondecode(fileread(config_path));
assert(~isempty(regexp(cfg.run_id, '^[A-Za-z0-9_-]+$', 'once')), 'Invalid run_id');
out_dir = fullfile(project_root, 'csv');
mat_dir = fullfile(project_root, 'matlab');
input_csv = fullfile(out_dir, [cfg.run_id '_stk_link.csv']);
output_csv = fullfile(out_dir, [cfg.run_id '_channel_result.csv']);
assert(isfile(input_csv), '请先成功运行STK生成脚本');
assert(~isfile(output_csv), '结果已存在，请更换run_id，避免覆盖');
data = readtable(input_csv, 'VariableNamingRule', 'preserve');
meta = jsondecode(fileread(fullfile(out_dir, [cfg.run_id '_metadata.json'])));
N = height(data);
t = double(data.Time_s(:)); dt = diff(t);
assert(N == meta.rows && N >= 2, '样本数不一致');
assert(all(isfinite(t)) && all(abs(dt-cfg.sample_interval_s)<1e-7), '时间网格不一致');
assert(isscalar(unique(string(data.SatName))), '本阶段仅处理单链路');
assert(all(string(data.RunId)==string(cfg.run_id)), 'RunId不匹配');
assert(all(isfinite(data.Distance_m)) && all(data.Distance_m>0), '距离非法');
for name = {'BeamCover','EarthLOS','BuildingLOS','GeometryAvailable','DirectLOS'}
    assert(all(ismember(data.(name{1}), [0 1])), '状态必须为0/1');
end
geometry = logical(data.EarthLOS) & logical(data.BeamCover);
direct = geometry & logical(data.BuildingLOS);
assert(isequal(geometry,logical(data.GeometryAvailable)), '几何状态不一致');
assert(isequal(direct,logical(data.DirectLOS)), '直达状态不一致');
assert(all(geometry), '本阶段输入应为已确认正常覆盖的连续片段');
assert(all(isfinite(data.ShadowFade_dB)) && all(data.ShadowFade_dB>=0), '遮挡损耗非法');
assert(all(isfinite(data.Doppler_Hz)), '多普勒非法');
assert(strcmp(cfg.phase_mode, 'ideal_compensated'), '本版本只支持明确的理想补偿模式');
assert(cfg.scatter_correlation_s>0 && isfinite(cfg.scatter_correlation_s), '相关时间非法');
rng(cfg.random_seed,'twister');
c = 299792458;
lambda = c/cfg.fc_hz;
path_loss = 20*log10(4*pi*double(data.Distance_m)/lambda)+double(data.ShadowFade_dB);
large_scale_raw = double(data.BeamCover)*cfg.beam_gain_db-path_loss;
large_scale_lin = 10.^(large_scale_raw/10);
scatter = complex(zeros(N,1));
scatter(1) = (randn+1j*randn)/sqrt(2);
for i=2:N
    rho=exp(-dt(i-1)/cfg.scatter_correlation_s);
    scatter(i)=rho*scatter(i-1)+sqrt(1-rho^2)*(randn+1j*randn)/sqrt(2);
end
K = 10^(cfg.K_db/10);
h = complex(zeros(N,1)); state=zeros(N,1);
for i=1:N
    if ~geometry(i)
        small=0;
    elseif data.BuildingLOS(i)
        state(i)=1;small=sqrt(K/(K+1))+sqrt(1/(K+1))*scatter(i);
    else
        state(i)=2;small=scatter(i);
    end
    h(i)=sqrt(large_scale_lin(i))*small;
end
gain = -inf(N,1); nonzero=abs(h)>0;
gain(nonzero)=20*log10(abs(h(nonzero)));
large_scale=large_scale_raw;large_scale(~geometry)=NaN;
result = table;
result.Time_s=t;
result.ScenarioTime_s=data.ScenarioTime_s;
result.RunId=string(data.RunId);
result.TerminalId=string(data.TerminalId);
result.TrajectoryId=string(data.TrajectoryId);
result.SatName=string(data.SatName);
result.Distance_km=data.Distance_m/1000;
result.Doppler_Hz=data.Doppler_Hz;
result.DopplerRate_Hzps=[NaN; diff(data.Doppler_Hz)./dt];
result.DopplerComp_Hz=data.Doppler_Hz;
result.ResidualDoppler_Hz=zeros(N,1);
result.PathLoss_dB=path_loss;
result.ShadowFade_dB=data.ShadowFade_dB;
result.LargeScaleGain_dB=large_scale;
result.ChannelGain_dB=gain;
result.GainValid=double(geometry & isfinite(gain));
result.CSI_Real=real(h);result.CSI_Imag=imag(h);
result.CSIIsCompensated=ones(N,1);
result.BeamCover=data.BeamCover;result.EarthLOS=data.EarthLOS;
result.BuildingLOS=data.BuildingLOS;
result.GeometryAvailable=double(geometry);result.DirectLOS=double(direct);
result.ChannelState=state;
assert(all(result.GainValid), '正常片段出现无效增益');
writetable(result, output_csv);
save(fullfile(mat_dir, [cfg.run_id '_channel_result.mat']),'result','h','scatter','cfg','meta','-v7');
channel_meta=struct('run_id',cfg.run_id,'rows',N,'phase_mode',cfg.phase_mode, ...
    'fc_hz',cfg.fc_hz,'beam_gain_db',cfg.beam_gain_db,'K_db',cfg.K_db, ...
    'scatter_correlation_s',cfg.scatter_correlation_s,'random_seed',cfg.random_seed, ...
    'matlab_version',version,'valid_gain_rows',sum(result.GainValid), ...
    'gain_min_db',min(gain),'gain_max_db',max(gain), ...
    'note','Offline narrowband correlated Rician baseline; ideal Doppler compensation; not a full 5G NTN waveform');
fid=fopen(fullfile(out_dir, [cfg.run_id '_channel_metadata.json']),'w','n','UTF-8');
assert(fid>=0,'Cannot write channel metadata');
cleaner=onCleanup(@()fclose(fid));
fprintf(fid,'%s',jsonencode(channel_meta,PrettyPrint=true));
fprintf('CHANNEL_OK rows=%d valid=%d time=%.6f..%.6f s gain=%.6f..%.6f dB\n', ...
    N,sum(result.GainValid),t(1),t(end),min(gain),max(gain));
end
