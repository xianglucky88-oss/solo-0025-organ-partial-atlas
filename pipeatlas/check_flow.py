import argparse
import json
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .server import CONFIG, PREFIX, ROOT, serve


def request(port, route, body=None, status=200):
    url = f'http://127.0.0.1:{port}{PREFIX}{route}'
    opts = {} if body is None else dict(data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    response = None
    try:
        response = urlopen(Request(url, **opts), timeout=5)
    except HTTPError as failure:
        response = failure
    with response:
        if response.status != status:
            raise ValueError(f'{route}：状态码 {response.status}，预期 {status}；{response.read(300)!r}')
        return json.load(response), response.headers


def observe(rows, row_id, field, target):
    result = next((row for row in rows if row['id'] == row_id), None)
    if field is None:
        if result is not None:
            raise ValueError(f'{row_id} 不应出现在结果中')
        return
    actual = result.get(field) if result is not None else None
    ok = isclose(actual, target, abs_tol=1e-8) if type(target) is float and type(actual) in (int,float) else actual == target
    if not ok or result is None or field not in result:
        raise ValueError(f'{row_id}.{field} 为 {actual!r}，应为 {target!r}')


def audit(port, flow):
    review = any(op['id'] == flow and op.get('optional') for op in CONFIG['operations'])
    prefix = 'review-' if review else ''
    data = request(port, prefix+'sample')[0]
    alt = request(port, prefix+'alternate')[0]
    bad = json.loads((ROOT/'examples'/(prefix+'reject.json')).read_text(encoding='utf8'))
    original = request(port,'evaluate',data)[0]
    alternate = request(port,'evaluate',alt)[0]
    request(port,'evaluate',bad,status=400)
    if review:
        request(port, 'seal', bad, status=400)
        baselines = [request(port, 'evaluate', request(port, name)[0])[0] for name in ('sample', 'alternate')]
        for baseline, enhanced in zip(baselines, (original, alternate), strict=True):
            if flow in baseline['outputs'] or any(enhanced['outputs'][k] != v for k,v in baseline['outputs'].items()):
                raise ValueError('复核改变了基础口径')
    if flow in original['outputs']:
        cases = json.loads((ROOT/'examples'/(prefix+'observations.json')).read_text(encoding='utf8'))
        row, key, value, alt_row, alt_key, alt_value = cases[flow]
        observe(original['outputs'][flow],row,key,value)
        observe(alternate['outputs'][flow],alt_row,alt_key,alt_value)
        if flow == 'silence':
            take = dict(id='暗管单开', temperature=20, opened=['暗管戊'],
                        peaks=[dict(id='新基音', hz=640, amplitude=0.8)])
            probe = dict(data, review=dict(data['review'], takes=[take]))
            rows = request(port, 'evaluate', probe)[0]['outputs'][flow]
            observe(rows, '暗管戊', 'state', 'covered')
            observe(rows, '暗管戊', 'evidence', dict(base='closed', observed=['暗管单开'], uncertain=[], absent=[]))
        if flow == 'recovery':
            rows = original['outputs'][flow]
            observe(rows, '阵风短跌/合奏负载', 'baseline', 320)
            observe(rows, '阵风短跌/合奏负载', 'level', 280)
            observe(rows, '阵风短跌/合奏负载', 'confirmed', 25)
            observe(rows, '重载迟迟/合奏负载', 'state', 'delayed')
            observe(rows, '重载迟迟/合奏负载', 'crossing', None)
            observe(rows, '中途换载/第一段负载', 'confirmed', 28)
            observe(rows, '中途换载/第二段负载', 'baseline', 300)
            observe(rows, '中途换载/第二段负载', 'level', 262.5)
            others = alternate['outputs'][flow]
            observe(others, '基准残缺/合奏负载', 'state', 'blocked')
            observe(others, '同刻矛盾/名单甲', 'state', 'blocked')
            observe(others, '同刻矛盾/名单乙', 'state', 'blocked')
            hole = next(r for r in others if r['id'] == '断档待查/合奏负载')['evidence']['gap']
            if hole != [20, 42]:
                raise ValueError(f'断档待查的阻断缺口为 {hole!r}，应为 [20, 42]')
            trial = dict(id='截断核查', gap=4,
                         series=[dict(t=t, pa=p) for t, p in [
                             (0, 320), (2, 320), (4, 320), (6, 320), (8, 320), (10, 320),
                             (14, 250), (16, 210), (18, 270), (20, 290), (22, 295),
                             (24, 300), (26, 298), (28, 297)]],
                         events=[dict(id='合奏负载', t=12, opened=['低音甲', '高音乙'],
                                      baseline=[0, 10], floor=240, ratio=0.875, hold=6, deadline=40),
                                 dict(id='提前换载', t=20, opened=['低音甲'],
                                      baseline=[14, 18], floor=240, ratio=0.875, hold=6, deadline=40)])
            probe = dict(data, review=dict(data['review'], trials=[trial]))
            truncated = request(port, 'evaluate', probe)[0]['outputs'][flow]
            observe(truncated, '截断核查/合奏负载', 'state', 'pending')
            observe(truncated, '截断核查/提前换载', 'state', 'recovered')
            observe(truncated, '截断核查/提前换载', 'level', 240)
            observe(truncated, '截断核查/提前换载', 'baseline', 235)
            hole = next(r for r in truncated if r['id'] == '截断核查/合奏负载')['evidence']['gap']
            if hole != [18, 52]:
                raise ValueError(f'截断核查的阻断缺口为 {hole!r}，应为 [18, 52]')
            event = trial['events'][0]
            for bad_trial in (dict(trial, events=[dict(event, baseline=[8, 14])]),
                              dict(trial, series=[dict(t=0, pa=300), dict(t=0, pa=301)]),
                              dict(trial, events=[dict(event, opened=['未知管'])]),
                              dict(trial, events=[dict(event, ratio=0)])):
                request(port, 'evaluate', dict(data, review=dict(data['review'], trials=[bad_trial])), status=400)
        return
    request(port,'seal',bad,status=400)
    left = request(port,'seal',data)[0]
    again = request(port,'seal',data)[0]
    right = request(port,'seal',alt)[0]
    if not left['created'] or again['created'] or again['sequence'] != left['sequence'] or not right['created']:
        raise ValueError('版本去重或首写失败')
    if len(request(port,'revisions')[0]) != 2:
        raise ValueError('无效及重复记录不应该入库')
    if flow == 'seal':
        observed = request(port,f"packet/{left['digest']}")[0]
        if observed != original:
            raise ValueError('较新的归约改变了历史成果')
    elif flow == 'compare':
        versions = dict(left=left['digest'],right=right['digest'])
        changes = request(port,'compare',versions)[0]
        if not changes['rows'] or request(port,'compare',dict(left=left['digest'],right=left['digest']))[0]['rows']:
            raise ValueError('版本比对没有区分同版及异版')
        other = request(port,'seal',dict(alt,id='不同对象'))[0]
        request(port,'compare',dict(left=left['digest'],right=other['digest']),status=409)
        base = request(port, 'seal', request(port, 'review-sample')[0])[0]
        mixed = request(port, 'compare', dict(left=left['digest'], right=base['digest']))[0]
        stages = {op['label'] for op in CONFIG['operations'] if op.get('optional')}
        if not mixed['rows'] or any(row['stage'] not in stages or row['state'] != 'added' for row in mixed['rows']):
            raise ValueError('旧版比对只应报告新增的复核结果')
        if request(port, f"export/{base['digest']}")[0] != request(port, f"packet/{base['digest']}")[0]:
            raise ValueError('复核信息未完整保存')
    elif flow == 'export':
        payload, headers = request(port,f"export/{left['digest']}")
        if payload != original or 'attachment;' not in headers.get('Content-Disposition',''):
            raise ValueError('导出没有保留原版本或下载头')
        request(port,'export/' + '0'*64,status=404)
    else:
        raise ValueError(f'未知工作流：{flow}')


def main():
    parser = argparse.ArgumentParser(description='运行有界的生产工作流检查')
    parser.add_argument('flow', choices=[op['id'] for op in CONFIG['operations']]+['seal','compare','export'])
    args = parser.parse_args()
    with TemporaryDirectory(prefix=CONFIG['namespace']+'-') as directory:
        with serve(Path(directory)/'observations.sqlite', 0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                audit(server.server_port,args.flow)
            finally:
                server.shutdown()
                thread.join()
    print(f'{args.flow}：样例、对照与合法拒绝均已核对')


if __name__ == '__main__':
    main()
