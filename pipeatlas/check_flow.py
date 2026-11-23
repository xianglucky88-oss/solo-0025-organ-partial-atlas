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


def audit_ensemble(port):
    data = request(port, 'ensemble-sample')[0]
    alt = request(port, 'ensemble-alternate')[0]
    bad = json.loads((ROOT/'examples'/'ensemble-reject.json').read_text(encoding='utf8'))
    request(port, 'evaluate', bad, status=400)
    request(port, 'seal', bad, status=400)
    original = request(port, 'evaluate', data)[0]
    alternate = request(port, 'evaluate', alt)[0]

    # 关闭整组模式后，不出现 ensemble 结果；候选生成阶段（温标、门槛、名单、容差）
    # 必须逐字段一致，归属之后的阶段允许采用新的可信基音口径。
    plain = json.loads(json.dumps(data, ensure_ascii=False))
    plain.pop('ensemble')
    baseline_packet = request(port, 'evaluate', plain)[0]
    baseline = baseline_packet['outputs']
    if 'ensemble' in baseline or 'ensemble' in baseline_packet['source']:
        raise ValueError('关闭整组模式仍输出整组结果')
    for stage in ('target', 'candidate'):
        if original['outputs'][stage] != baseline[stage]:
            raise ValueError(f'整组模式改动了候选生成阶段：{stage}')
    for stage in ('target', 'candidate', 'assign', 'conflict', 'coverage', 'tune', 'revisit', 'clusters'):
        if baseline[stage] != request(port, 'evaluate', plain)[0]['outputs'][stage]:
            raise ValueError('旧口径结果不稳定')

    cases = json.loads((ROOT/'examples'/'ensemble-observations.json').read_text(encoding='utf8'))
    for stage in ('ensemble', 'clusters', 'assign', 'tune', 'handoff'):
        row, key, value, alt_row, alt_key, alt_value = cases[stage]
        observe(original['outputs'][stage], row, key, value)
        observe(alternate['outputs'][stage], alt_row, alt_key, alt_value)

    rows = {r['id']: r for r in original['outputs']['ensemble']}
    clusters = {r['id']: r for r in original['outputs']['clusters']}

    # 交替可换的两个峰：都可占两个槽，都不能未归，都没有唯一必然槽。
    for peak in ('峰己乙', '峰泛己'):
        r = rows[peak]
        if r['state'] != 'flexible' or r['may_unassign'] or r['forced'] is not None:
            raise ValueError(f'{peak} 应为可交替归属的歧义峰')
        if r['slots'] != ['管乙/2', '管己/1']:
            raise ValueError(f'{peak} 的可占槽集合错误')

    # 只能强制归位的两个峰：唯一必然槽固定，且必死边被标出。
    forced = rows['峰换甲']
    if forced['forced'] != '管甲/1' or forced['may_unassign'] or forced['dead_edges'] != ['管副甲/1']:
        raise ValueError('峰换甲 应强制归位并标出必死候选边')
    if rows['峰争甲']['forced'] != '管副甲/1' or rows['峰争甲']['dead_edges']:
        raise ValueError('峰争甲 应唯一归位到 管副甲/1')
    dead_cluster = clusters['峰争甲']
    if dead_cluster['state'] != 'resolved' or dead_cluster['dead_edges'] != ['峰换甲→管副甲/1']:
        raise ValueError('争用组没有正确标出必死候选边')

    # 基音峰可归不同槽，也可以不指认；该组至少一个峰无法同时归属。
    loose = rows['峰余戊']
    if loose['state'] != 'flexible' or not loose['may_unassign'] or set(loose['slots']) != {'管戊/1', '管甲/2'}:
        raise ValueError('峰余戊 应可归不同槽或不指认')
    over = clusters['峰强丁']
    if over['deficit'] != 1 or over['matched'] != 2 or over['state'] != 'oversubscribed':
        raise ValueError('争用组未报告最少不能同时归属的峰数')

    # 可信基音口径：被否决的候选不能进入调律与交接，缺测保持待定。
    tune = {r['id']: r for r in original['outputs']['tune']}
    if tune['管戊']['state'] != 'pending' or tune['管戊']['deviation'] is not None:
        raise ValueError('未确认基音不应给出调律偏差')
    if tune['管甲']['state'] != 'adjust' or tune['管甲']['evidence'] != '峰换甲':
        raise ValueError('可信基音未进入调律口径')
    handoff = {r['id']: r for r in original['outputs']['handoff']}
    if handoff['管戊']['state'] != 'review' or not handoff['管戊']['evidence']['isolated_lead']:
        raise ValueError('单管线索没有进入交接口径')
    if handoff['管己']['state'] != 'pending' or handoff['管己']['evidence']['isolated_lead']:
        raise ValueError('无单管佐证的未决基音不应给出交接结论')

    # 无候选的峰留账但不进入任何争用组结论。
    if rows['弱峰']['state'] != 'idle' or not rows['弱峰']['may_unassign'] or rows['弱峰']['slots']:
        raise ValueError('低于幅值门限的峰不应拥有候选槽')

    # 封存与导出仍保留整组结果，且同输入只写一次。
    saved = request(port, 'seal', data)[0]
    again = request(port, 'seal', data)[0]
    if not saved['created'] or again['created']:
        raise ValueError('整组成果封存去重失败')
    if request(port, f"packet/{saved['digest']}")[0] != original:
        raise ValueError('整组结果未完整封存')


def audit(port, flow):
    review = any(op['id'] == flow and op.get('optional') for op in CONFIG['operations'])
    if flow == 'ensemble':
        audit_ensemble(port)
        return
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
