"""Extract the fixed TSOC historical dataset using cached XLSX values."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

DAY = '2026-10-14'
BASE = 'https://s3-eu-central-1.amazonaws.com/tso-cy/'
SOURCES = {
    'isp_forecast.xlsx': BASE + 'ISP_FCST/excel/2026/10/ISP_FCST_20261014.xlsx',
    'balancing_14.xlsx': BASE + 'REP_TSO-001/2026/10/REP_TSO-001-en-20261014-20261015060205.xlsx',
    'balancing_13.xlsx': BASE + 'REP_TSO-001/2026/10/REP_TSO-001-en-20261013-20261014060202.xlsx',
    'balancing_12.xlsx': BASE + 'REP_TSO-001/2026/10/REP_TSO-001-en-20261012-20261013060153.xlsx',
    'balancing_09.xlsx': BASE + 'REP_TSO-001/2026/10/REP_TSO-001-en-20261009-20261010060149.xlsx',
    'dam.xlsx': BASE + 'REP_MO-003/2026/10/REP_MO-003-S-en-20261014-20261013134813.xlsx',
}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def workbook(path):
    with zipfile.ZipFile(path) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets = {}
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
            target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            rows = {}
            for row in ET.fromstring(z.read(target)).findall('s:sheetData/s:row', NS):
                cells = {}
                for cell in row:
                    v = cell.find('s:v', NS)
                    value = v.text if v is not None else ''.join(cell.itertext()) if cell.get('t') == 'inlineStr' else ''
                    if cell.get('t') == 's': value = strings[int(value)]
                    cells[''.join(c for c in cell.attrib['r'] if c.isalpha())] = value
                rows[int(row.attrib['r'])] = cells
            sheets[sheet.attrib['name']] = rows
        return sheets


def column_name(n):
    result = ''
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def series(rows, row, count=48):
    values = [float(rows[row][column_name(i + 2)]) for i in range(count)]
    if not all(math.isfinite(v) for v in values): raise ValueError('Incomplete/nonfinite data')
    return values


def check_grid(values, day, minutes):
    for i, value in enumerate(values):
        at = datetime(1899, 12, 30) + timedelta(days=value)
        expected = datetime.fromisoformat(day) + timedelta(minutes=i * minutes)
        if abs((at - expected).total_seconds()) > .01: raise ValueError('Invalid timestamp grid')


def extract(folder):
    forecasts = next(r for r in workbook(folder/'isp_forecast.xlsx').values() if 2 in r and 'E' in r[2])
    assert [int(forecasts[i]['A']) for i in range(2, 50)] == list(range(1, 49))
    check_grid([float(forecasts[i]['B']) for i in range(2, 50)], DAY, 30)
    def fc(col):
        values = [float(forecasts[i][col]) for i in range(2, 50)]
        assert all(math.isfinite(v) and v >= 0 for v in values)
        return values
    actual = workbook(folder/'balancing_14.xlsx')['PT30M']
    check_grid(series(actual, 2), DAY, 30)
    assert actual[2]['A'] == 'Expost System Load'
    assert [actual[i]['A'] for i in (5, 7, 8)] == ['Oil', 'Solar', 'Wind']
    history, fills = [], []
    for day in ('09', '12', '13'):
        rows = workbook(folder/f'balancing_{day}.xlsx')['PT5M']
        check_grid(series(rows, 2, 288), f'2026-10-{day}', 5)
        up, down = series(rows, 3, 288), series(rows, 4, 288)
        prices = []
        for i in range(288):
            raw = rows[6 if up[i] >= down[i] else 7].get(column_name(i+2), '')
            value = float(raw) if raw else None
            if value is None or abs(value-999.999) < 1e-6 or abs(value) >= 999999:
                value = prices[-1] if prices else 250.
                fills.append(dict(day=f'2026-10-{day}', rtu=i, original=raw, replacement=value,
                                  method='previous_valid_price_or_initial_250'))
            if not math.isfinite(value): raise ValueError('Nonfinite price')
            prices.append(value)
        history.append(prices)
    dam = next(iter(workbook(folder/'dam.xlsx').values()))
    all_rows = [r for r in dam.values() if r.get('B') == 'ALL']
    assert len(all_rows) == 48
    for i, row in enumerate(all_rows):
        expected = (datetime.fromisoformat(DAY)+timedelta(minutes=30*i)).strftime('%d/%m/%Y %H:%M')
        assert row['A'].startswith(expected+'-')
    data = dict(day=DAY, timezone='Europe/Nicosia', native_resolution_minutes=30,
        forecast_mw=dict(demand=fc('E'), solar=fc('F'), wind=fc('G'), res=fc('H')),
        actual_mw=dict(demand=series(actual, 3), solar=series(actual, 7), wind=series(actual, 8)),
        observed_oil_generation_mw=series(actual, 5),
        reserve_requirements={p:{d:fc(c) for d,c in ds.items()} for p,ds in {
            'FCR':{'up':'I','down':'L'},'aFRR':{'up':'J','down':'M'},'mFRR':{'up':'K','down':'N'}}.items()},
        published_minimum_must_run_mw=fc('O'),
        historical_rtbm_prices={i:[prices[i*6:i*6+6] for prices in history] for i in range(48)},
        history_price_fills=fills,
        history_method='Net-activation-direction marginal-price proxy; missing/sentinel prices carried forward within each earlier day, initial fallback 250 EUR/MWh.',
        benchmark=dict(dam_prices_eur_mwh=[float(r.get('C') or 0) for r in all_rows],
                       dam_volume_mwh=[float(r['D']) for r in all_rows]),
        previous_day_oil_power_proxy_mw=series(workbook(folder/'balancing_13.xlsx')['PT30M'],5)[-1])
    assert all(abs(a+b-c) < 1e-6 for a,b,c in zip(fc('F'),fc('G'),fc('H')))
    assert all(v >= 0 for values in data['actual_mw'].values() for v in values)
    data['sources'] = [dict(file=n, url=u, sha256=hashlib.sha256((folder/n).read_bytes()).hexdigest(),
                            retrieved_at=datetime.now(timezone.utc).isoformat()) for n,u in SOURCES.items()]
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, default=Path('output/historical-cyprus/sources'))
    parser.add_argument('--output', type=Path, default=Path('examples/data/cyprus_2026_10_14.json'))
    args = parser.parse_args()
    args.sources.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        if not (args.sources/name).exists():
            (args.sources/name).write_bytes(urllib.request.urlopen(url, timeout=30).read())
    data = extract(args.sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, allow_nan=False), encoding='utf-8')
    print(f"48-MTU dataset saved to {args.output}; {len(data['history_price_fills'])} historical price fills recorded")


if __name__ == '__main__': main()
