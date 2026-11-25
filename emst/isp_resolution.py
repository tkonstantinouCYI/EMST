"""Native effective ISP intervals and mapping to half-hour accounting, no re-solve."""
from copy import deepcopy
from .calendar import Interval
from .onoff import enabled


def validate(minutes,assets,rtbm_minutes):
    if type(minutes) is not int or minutes not in (30,60):raise ValueError('isp_interval_minutes must be 30 or 60')
    simplified=[a for a in assets if a.technology=='thermal' and enabled(a)]
    if simplified and rtbm_minutes!=30:raise NotImplementedError('ON/OFF constant boundary delivery currently requires 30-minute sequential RTBM')
    if minutes==60 and any(a.technology=='thermal' and not enabled(a) for a in assets):
        raise NotImplementedError('Native hourly ISP currently requires ON/OFF ordinary thermals; configured/staged hourly plants remain experimental')
    if minutes==60 and any(a.technology=='storage' for a in assets):
        raise NotImplementedError('Native hourly ISP currently supports thermal/RES/load portfolios; storage retains the 30-minute ISP')


def inputs(periods,load,res,commercial,requirements,mandatory,forecasts,minutes):
    if minutes==30:return periods,load,res,commercial,requirements,mandatory,forecasts
    if len(periods)%2 or any(a.end!=b.start for a,b in zip(periods,periods[1:])):raise ValueError('Hourly ISP needs complete consecutive half-hour pairs')
    avg=lambda x:[(x[i]+x[i+1])/2 for i in range(0,len(x),2)]
    add=lambda x:[x[i]+x[i+1] for i in range(0,len(x),2)]
    peak=lambda x:[max(x[i],x[i+1]) for i in range(0,len(x),2)]
    native=[Interval(t.id.replace(':30:',':60:'),t.start,60,t.period) for t in periods[::2]]
    return (native,avg(load),avg(res),{a:add(x) for a,x in commercial.items()},
        {p:peak(x) for p,x in requirements.items()},{a:peak(x) for a,x in mandatory.items()},{a:avg(x) for a,x in forecasts.items()})


def accounting(run,periods,schedules):
    result={}
    for row in run['rows']:
        count=round(row['hours']/.5)
        for offset in range(count):
            index=row['period']+offset
            if count==1:
                result[index]=deepcopy(row) if any(x.get('commitment_model')=='on_off' for x in row['assets'].values()) else row
                continue
            mapped=deepcopy(row);mapped.update(period=index,at=periods[index].start.isoformat(),hours=.5,
                effective_ISP_period=row['period'],mapped_from_hourly_ISP=True)
            mapped['reserve_prices']={p:v/count for p,v in row['reserve_prices'].items()}
            for aid,x in mapped['assets'].items():
                x['energy_mwh']=x['power_mw']*.5
                deviation=x['energy_mwh']-schedules[aid][index]
                x['up_mwh']=max(0,deviation);x['down_mwh']=max(0,-deviation)
                x['start']=x.get('start',0) if offset==0 else 0
                x['stop']=x.get('stop',0) if offset==0 else 0
            result[index]=mapped
    return result
