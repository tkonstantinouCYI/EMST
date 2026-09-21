"""Generate standalone PNG plots from recorded CSV results. Requires matplotlib."""
import csv
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
rows = list(csv.DictReader((HERE / 'results/participant_metrics.csv').open()))
lookup = {(r['scenario'], r['participant'], r['stage']): r for r in rows}
names = ('IND', 'COM', 'RESI', 'PV_C', 'PV_A', 'W_C', 'W_A')
scenarios = ('Baseline', 'Low RES', 'High RES', 'High demand', 'No revision',
             'Double revision', 'Reverse revision')
out = HERE / 'plots'
out.mkdir(exist_ok=True)

fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), layout='constrained')
for ax, group, side, title in [(axes[0], names[:3], 'buy_mwh', 'Supplier purchases'),
                              (axes[1], names[3:], 'sell_mwh', 'Renewable sales')]:
    key = 'buy_vwap' if side == 'buy_mwh' else 'sell_vwap'
    for pid, marker in zip(group, ('s', '^', 'o', 'D')):
        prices = [float(lookup[s, pid, 'ALL'][key]) for s in scenarios]
        ax.plot(range(7), prices, linestyle='none', marker=marker, label=pid.replace('_', '-'))
    ax.set_xticks(range(7), scenarios, rotation=35, ha='right')
    ax.set_ylabel('Price (EUR/MWh)')
    ax.set_title(title)
    ax.grid(axis='y', alpha=.3)
    ax.legend(ncol=2)
fig.savefig(out / 'participant_prices.png', dpi=300)
plt.close(fig)

fig, ax = plt.subplots(figsize=(8, 4.5), layout='constrained')
bottom = [0.] * len(names)
for stage in ('FM', 'DAM', 'IDA1', 'IDA2', 'IDA3'):
    values = []
    for pid in names:
        key = 'buy_mwh' if pid in names[:3] else 'sell_mwh'
        values.append(100 * float(lookup['Baseline', pid, stage][key]) /
                      float(lookup['Baseline', pid, 'ALL'][key]))
    ax.bar([p.replace('_', '-') for p in names], values, bottom=bottom, label=stage.replace("IDA", "IA"))
    bottom = [b + v for b, v in zip(bottom, values)]
assert all(abs(v - 100) < 1e-7 for v in bottom)
ax.set_ylim(0, 100)
ax.set_ylabel('Share of gross purchase/sales volume (%)')
ax.legend(ncol=5, loc='upper center', bbox_to_anchor=(.5, 1.14))
fig.savefig(out / 'market_mix.png', dpi=300)
plt.close(fig)
print('Wrote participant_prices.png and market_mix.png to', out)

# Baseline DAM profiles, positive MW from half-hour MWh.
forecast = list(csv.DictReader((HERE / 'results/forecasts.csv').open()))
profile = {(r['participant'], int(r['period'])): float(r['target_mwh']) * 2
           for r in forecast if r['scenario'] == 'Baseline' and r['stage'] == 'DAM'}
hours = [(i - .5) / 2 for i in range(1, 49)]
fig, ax = plt.subplots(figsize=(7, 4), layout='constrained')
for pid in names[:3]:
    ax.plot(hours, [-profile[pid, i] for i in range(1,49)], label=pid)
ax.set(xlabel='Hour', ylabel='Demand (MW)', xlim=(0,24), ylim=(0,None))
ax.legend(); ax.grid(alpha=.3)
fig.savefig(out / 'baseline_demand_profiles.png', dpi=300)
plt.close(fig)
fig, axes = plt.subplots(1,2,figsize=(8,4),layout='constrained')
for ax, pid, match in zip(axes, ('PV_C','W_C'), ('PV_A','W_A')):
    assert all(profile[pid,i] == profile[match,i] for i in range(1,49))
    ax.plot(hours, [profile[pid,i] for i in range(1,49)])
    ax.set(title=pid.replace('_','-')+' / '+match.replace('_','-'), xlabel='Hour', ylabel='Generation (MW)', xlim=(0,24), ylim=(0,None))
    ax.grid(alpha=.3)
fig.savefig(out / 'baseline_res_profiles.png', dpi=300)
plt.close(fig)
