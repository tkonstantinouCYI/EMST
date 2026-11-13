"""Sequential daily execution with portable, verified, atomic checkpoints."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
import hashlib
import json
import os
import tempfile

from .market import MarketResults, ThermalState, StorageState


def _json(value):
    value=json.loads(json.dumps(value,allow_nan=False))
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(_json(value));stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def _configuration(market):
    return dict(rules=market.ruleset.to_dict(),parameters=market.settlement_parameters,
        participants=[asdict(p) for p in market.participants.values()],
        assets=[dict(id=a.id,participant=a.participant,technology=a.technology,
            parameters=a.parameters,strategy=asdict(a.strategy),
            initial_state=asdict(a.initial_state) if a.initial_state else None)
            for a in market.assets.values()],forwards=[asdict(f) for f in market.forwards],
        code={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
              for p in Path(__file__).parent.glob('*.py')})


def _day(value):
    return date.fromisoformat(value) if isinstance(value,str) else value


def _kind(day):
    return 'weekend' if day.weekday()>=5 else 'weekday'


def _history(value):
    result={int(k):[[float(p) for p in row] for row in v] for k,v in value.items()}
    if set(result)!=set(range(48)) or any(len(v)!=3 or any(len(r)!=6 for r in v) for v in result.values()):
        raise ValueError('Historical bootstrap requires 48 periods, each with three six-price days')
    _json(result)
    return result


@dataclass
class PeriodResults:
    days: dict
    checkpoint: Path
    completed_days: list

    def to_readable(self, *, monthly, audience='shared', participant=None):
        from .reporting import readable_month
        return readable_month(self, monthly, audience=audience, participant=participant)

    def save_readable(self, directory, *, monthly):
        from .reporting import save_readable_month
        return save_readable_month(self, monthly, directory)

    def settle_month(self, month, **parameters):
        """Close a complete simulated month and optionally reconcile meter revisions."""
        from .monthly import settle_month
        return settle_month(self, month, **parameters)


def load_checkpoint(path,market):
    path=Path(path).resolve()
    envelope=json.loads(path.read_text(encoding='utf-8'));state=envelope['state']
    if envelope['sha256']!=_digest(state):raise ValueError('Checkpoint checksum mismatch')
    if state['schema']!=1:raise ValueError('Unsupported checkpoint schema')
    if state['configuration_sha256']!=_digest(_configuration(market)):
        raise ValueError('Checkpoint market configuration or implementation differs')
    days={}
    for record in state['results']:
        target=(path.parent/record['file']).resolve()
        if not target.is_relative_to(path.parent):raise ValueError('Invalid checkpoint result path')
        raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Saved daily result checksum mismatch')
        days[record['day']]=MarketResults(**json.loads(raw))
    return state,days


def run_period(market,start,end,scenario,checkpoint_directory,resume_from=None,must_run_auction_frequency="monthly"):
    """Scenario maps ISO dates to forecasts/actuals by asset plus daily run kwargs.

    No next-day lookahead, monthly finalisation, or silent repetition of series.
    Original Market inputs are never modified. Resume must start the next day.
    """
    if must_run_auction_frequency not in ('daily','monthly'):raise ValueError('Must-run auction frequency is daily or monthly')
    start,end=_day(start),_day(end)
    if not isinstance(start,date) or not isinstance(end,date) or end<start:
        raise ValueError('Expected an inclusive ascending date range')
    root=Path(checkpoint_directory).resolve()
    config=_digest(_configuration(market))
    state=dict(schema=1,configuration_sha256=config,last_day=None,ending_states={},
        previous_system_observation=None,price_history=[],bootstrap={},results=[],
        scope='daily_accrual',next_day_lookahead=False,must_run_auctions={},must_run_auction_frequency=must_run_auction_frequency)
    days={}
    if resume_from:
        if Path(resume_from).resolve().parent!=root:
            raise ValueError('Resume into the checkpoint directory; copy the directory to relocate it')
        state,days=load_checkpoint(resume_from,market)
        if start!=_day(state['last_day'])+timedelta(days=1):
            raise ValueError('Resume start must immediately follow the last completed day')
        latest=root/'checkpoint.json'
        if latest.exists() and latest!=Path(resume_from).resolve():
            committed,_=load_checkpoint(latest,market)
            if committed['last_day']>state['last_day']:
                raise ValueError('A newer checkpoint is already committed in this directory')
    elif (root/'checkpoint.json').exists():
        raise FileExistsError('Checkpoint directory already contains a run; use resume_from')
    if state.get('must_run_auction_frequency',must_run_auction_frequency)!=must_run_auction_frequency:
        raise ValueError('Cannot change must-run auction frequency on resume')
    daily={_day(k):v for k,v in scenario.items()}
    dates=[start+timedelta(days=i) for i in range((end-start).days+1)]
    series_assets={a.id for a in market.assets.values() if a.technology in ('res','supplier')}
    allowed={'forecasts','actuals','forwards','reserve_requirements','price_forecast',
        'historical_rtbm_prices','price_ranges','system_forecasts','system_observations',
        'previous_system_observation','must_run_requirement_mw','must_run_supplier_shares'}
    for day in dates:
        if day not in daily:raise ValueError(f'Missing scenario for {day}')
        data=daily[day]
        if set(data)-allowed:raise ValueError(f'Unknown daily inputs: {set(data)-allowed}')
        for key in ('forecasts','actuals'):
            if set(data.get(key,{}))!=series_assets:
                raise ValueError(f'{day}: {key} must cover every RES and supplier asset exactly')
        if len(market.time_index(day))!=48:raise NotImplementedError('Multi-day DST support is not implemented')
        if state['last_day'] and 'previous_system_observation' in data:
            raise ValueError('A carried observation cannot be overridden')
        if not resume_from and (root/str(day)/'result.json').exists():
            raise FileExistsError(f'Daily result already exists for {day}; preserve it or use a new directory')
    from .must_run import clear_auction,supplier_weights
    from calendar import monthrange
    state.setdefault('must_run_auctions',{})
    if must_run_auction_frequency=='monthly':
        months=sorted({d.strftime('%Y-%m') for d in dates if daily[d].get('must_run_requirement_mw') is not None})
        for month in months:
            if month in state['must_run_auctions']:continue
            first=date.fromisoformat(month+'-01')
            month_dates=[first.replace(day=d) for d in range(1,monthrange(first.year,first.month)[1]+1)]
            if any(d not in daily or daily[d].get('must_run_requirement_mw') is None for d in month_dates):
                raise ValueError('Monthly must-run auction requires requirements and forecasts for the complete month before clearing; use daily frequency for partial studies')
            awards=clear_auction(market,{str(d):daily[d]['must_run_requirement_mw'] for d in month_dates},
                frequency='monthly',auction_id='monthly:'+month)
            from .series import mw_values
            forecasts={a.id:[v for d in month_dates for v in mw_values(daily[d]['forecasts'][a.id],market.time_index(d),a.id+' monthly forecast')]
                for a in market.assets.values() if a.technology=='supplier'}
            supplied=[daily[d].get('must_run_supplier_shares') for d in month_dates]
            if any(v!=supplied[0] for v in supplied):raise ValueError('Monthly must-run supplier shares must be constant throughout the month')
            shares=supplier_weights(market,forecasts,supplied[0])
            state['must_run_auctions'][month]=dict(awards=awards,supplier_shares=shares,
                share_source='supplied' if supplied[0] is not None else 'monthly_forecast_consumption')
    working=deepcopy(market)
    for day in dates:
        data=deepcopy(daily[day]);kind=_kind(day)
        working._must_run_award=None
        procurement=state['must_run_auctions'].get(day.strftime('%Y-%m'))
        if procurement is not None:
            if data.get('must_run_requirement_mw') is None:raise ValueError('Cannot omit a precleared monthly must-run requirement')
            working._must_run_award=deepcopy(procurement['awards'][str(day)])
            working._must_run_award['supplier_share_source']=procurement['share_source']
            if data.get('must_run_supplier_shares') is not None and data['must_run_supplier_shares']!=procurement['supplier_shares']:
                raise ValueError('Cannot change precleared monthly supplier shares')
            data['must_run_supplier_shares']=procurement['supplier_shares']
        for a in working.assets.values():
            if a.id in series_assets:
                a.forecast=data['forecasts'][a.id];a.actual=data['actuals'][a.id]
            if a.id in state['ending_states']:
                cls=ThermalState if a.technology=='thermal' else StorageState
                a.initial_state=cls(**state['ending_states'][a.id])
        working.forwards=deepcopy(data.pop('forwards',market.forwards))
        data.pop('forecasts');data.pop('actuals')
        if state['last_day']:
            if 'previous_system_observation' in data:raise ValueError('A carried observation cannot be overridden')
            data['previous_system_observation']=state['previous_system_observation']
        supplied=data.pop('historical_rtbm_prices',None)
        if supplied is not None:state['bootstrap'][kind]=_history(supplied)
        past=[r for r in state['price_history'] if _kind(_day(r['day']))==kind][-3:]
        seed=state['bootstrap'].get(kind)
        if len(past)<3 and seed is None:
            raise ValueError(f'{day}: supply historical_rtbm_prices to bootstrap {kind} prices')
        history={t:([r['prices'][t*6:t*6+6] for r in past]+
            ([] if len(past)==3 else (seed.get(t,seed.get(str(t))))[len(past):])) for t in range(48)}
        result=working.run(time_index=market.time_index(day),historical_rtbm_prices=history,**data)
        if not result.audit['passed']:raise RuntimeError(f'Daily audit failed for {day}; no checkpoint committed')
        target=root/str(day)/'result.json'
        _atomic(target,asdict(result))
        state['last_day']=str(day);state['ending_states']=deepcopy(result.ending_states)
        state['previous_system_observation']=deepcopy(result.rtbm[-1]['system_observation'])
        state['price_history'].append(dict(day=str(day),prices=[r['price_eur_mwh'] for r in result.rtbm]))
        state['results'].append(dict(day=str(day),file=f'{day}/result.json',
            sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        envelope=dict(state=state,sha256=_digest(state))
        _atomic(root/f'checkpoint-{day}.json',envelope)
        _atomic(root/'checkpoint.json',envelope)
        days[str(day)]=result
    return PeriodResults(days,root/'checkpoint.json',[r['day'] for r in state['results']])
