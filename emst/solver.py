"""Copied solver configuration, per-evaluation context and structured failures."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import math

DEFAULTS=dict(backend='auto',time_limit_seconds=90.,relative_gap=0.,absolute_gap=0.,
              threads=1,logging=False)
STAGES={'DAM','ISP','RTBM','RTBM_projection','must_run','storage_strategy','diagnostic','unspecified'}
_session=ContextVar('emst_solver_session',default=None)
_stage=ContextVar('emst_solver_stage',default=None)
_purpose=ContextVar('emst_solver_purpose',default=None)
_details=ContextVar('emst_solver_details',default={})


def configuration(value=None):
    value=deepcopy({} if value is None else value)
    if not isinstance(value,dict):raise TypeError('solver_config must be a mapping')
    overrides=value.pop('stages',{})
    def validate(changes):
        if set(changes)-set(DEFAULTS):raise ValueError(f'Unsupported solver options: {set(changes)-set(DEFAULTS)}')
        data={**DEFAULTS,**changes}
        if data['backend'] not in ('auto','highs','cbc'):raise ValueError('Solver backend must be auto, highs or cbc')
        for key in ('time_limit_seconds','relative_gap','absolute_gap'):
            v=data[key]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 or (key=='time_limit_seconds' and v==0):raise ValueError(f'Invalid solver option {key}')
        if type(data['threads']) is not int or data['threads']<1:raise ValueError('Solver threads must be a positive integer')
        if type(data['logging']) is not bool:raise ValueError('Solver logging must be boolean')
        return data
    base=validate(value)
    if not isinstance(overrides,dict) or set(overrides)-STAGES:raise ValueError('Unknown solver stage override')
    return {**base,'stages':{key:validate({**base,**v}) for key,v in overrides.items()}}


def options(stage):
    session=_session.get()
    config=session['config'] if session else configuration()
    fallback=config['stages'].get('RTBM') if stage=='RTBM_projection' else None
    return deepcopy(config['stages'].get(stage,fallback or {k:v for k,v in config.items() if k!='stages'}))


class SolveError(RuntimeError):
    def __init__(self,record):
        self.record=deepcopy(record)
        super().__init__(f"{record['problem']}: {record['status']} ({record['stage']}/{record['purpose']}; {record['termination_reason']})")


@contextmanager
def session(config=None):
    value=dict(config=configuration(config),records=[])
    token=_session.set(value)
    try:yield value
    finally:_session.reset(token)


@contextmanager
def purpose(name):
    token=_purpose.set(name)
    try:yield
    finally:_purpose.reset(token)


def stage(name):
    def decorate(function):
        @wraps(function)
        def call(*args,**kwargs):
            selected=name(*args,**kwargs) if callable(name) else name
            token=_stage.set(selected)
            details={}
            if callable(name):
                intervals=kwargs.get('intervals',args[2] if len(args)>2 else [])
                if intervals:details=dict(horizon_start=intervals[0].start.isoformat(),horizon_intervals=len(intervals))
            detail_token=_details.set(details)
            try:return function(*args,**kwargs)
            finally:
                _details.reset(detail_token)
                _stage.reset(token)
        return call
    return decorate


def record(value):
    context=_session.get()
    if context is not None:context['records'].append(deepcopy(value))


def stage_name(problem):
    if problem.name=='optimal_dual_face':return 'diagnostic'
    if _stage.get():return _stage.get()
    return {'DAM':'DAM','library_ISP':'ISP','library_RTBM':'RTBM',
            'must_run_auction':'must_run','storage_strategy':'storage_strategy',
            'optimal_dual_face':'diagnostic'}.get(problem.name,'unspecified')


def solve_purpose(mip):
    return _purpose.get() or ('market_clearing' if mip else 'linear_clearing')


def details():return deepcopy(_details.get())
