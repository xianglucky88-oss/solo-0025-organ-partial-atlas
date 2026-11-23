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


def audit_silence(port, data):
    take = dict(id='暗管单开', temperature=20, opened=['暗管戊'], floor=0.2,
                certificate=dict(floor=0.2, bands=[dict(lo=620, hi=660)]),
                peaks=[dict(id='新基音', hz=640, amplitude=0.8)])
    probe = dict(data, review=dict(data['review'], takes=[take]))
    rows = request(port, 'evaluate', probe)[0]['outputs']['silence']
    observe(rows, '暗管戊', 'state', 'covered')
    row = next(item for item in rows if item['id'] == '暗管戊')
    if row['evidence']['observed'] != ['暗管单开'] or row['evidence']['base'] != 'closed':
        raise ValueError('基线与复测开管未分列')
    # One certified absence must not be cancelled by another blind retest.
    quiet = dict(id='背管静默', temperature=20, opened=['背管丁'], floor=0.2,
                 certificate=dict(floor=0.2, bands=[dict(lo=500, hi=545)]), peaks=[])
    side = dict(id='背管旁带', temperature=20, opened=['背管丁'], floor=0.2,
                certificate=dict(floor=0.2, bands=[dict(lo=1000, hi=1100)]), peaks=[])
    probe = dict(data, review=dict(data['review'], takes=[quiet, side]))
    rows = request(port, 'evaluate', probe)[0]['outputs']['silence']
    observe(rows, '背管丁', 'state', 'unseen')
    row = next(item for item in rows if item['id'] == '背管丁')
    if row['evidence']['unseen'] != ['背管静默'] or row['evidence']['blind'] != ['背管旁带']:
        raise ValueError('有证未见与盲测未分列')
    # A peak heard only in the other band cannot fill in the missing fundamental.
    heard = dict(id='背管泛音', temperature=20, opened=['背管丁'], floor=0.2,
                 certificate=dict(floor=0.2, bands=[dict(lo=1000, hi=1100)]),
                 peaks=[dict(id='泛音', hz=1040, amplitude=0.8)])
    probe = dict(data, review=dict(data['review'], takes=[quiet, heard]))
    rows = request(port, 'evaluate', probe)[0]['outputs']['silence']
    observe(rows, '背管丁', 'state', 'unseen')
    # A legacy retest without a certificate replays under the old definitions and is not
    # counted as certified evidence.
    legacy = dict(id='背管旧档', temperature=20, opened=['背管丁'], peaks=[])
    probe = dict(data, review=dict(data['review'], takes=[quiet, legacy]))
    rows = request(port, 'evaluate', probe)[0]['outputs']['silence']
    row = next(item for item in rows if item['id'] == '背管丁')
    if row['evidence']['legacy'] != ['背管旧档'] or row['evidence']['certified'] != ['背管静默']:
        raise ValueError('无证书旧档被计入有证')
    # A chain that never covered the fundamental and a chain whose band sat on it but was
    # notched are different failures and must not share one ledger kind.
    notched = dict(id='背管陷波', temperature=20, opened=['背管丁'], floor=0.2,
                   certificate=dict(floor=0.2, bands=[dict(lo=500, hi=545)],
                                    polluted=[dict(lo=514, hi=525, kind='notch')]), peaks=[])
    probe = dict(data, review=dict(data['review'], takes=[side, notched]))
    rows = request(port, 'evaluate', probe)[0]['outputs']['silence']
    row = next(item for item in rows if item['id'] == '背管丁')
    if row['evidence']['blind'] != ['背管旁带'] or row['evidence']['muted'] != ['背管陷波']:
        raise ValueError('仅盲测与有证被污染未分列')
    if row['state'] != 'mixed':
        raise ValueError('盲测与污染并存时被折成单一种失败')


def audit_certificates(port, data):
    # Same raw peaks, two different certificates. A second valid band that re-claims a
    # notched stretch cannot whitewash the notch, and the notched pseudo-evidence cannot
    # mark the pipe covered.
    source = json.loads(json.dumps(data))
    source['peaks'].append(dict(id='假基音', hz=520, amplitude=0.9))
    notch = dict(lo=514, hi=525, kind='notch')
    cert_a = dict(floor=0.2, bands=[dict(lo=500, hi=545)], polluted=[notch])
    cert_b = dict(floor=0.2, bands=[dict(lo=500, hi=545), dict(lo=504, hi=530)], polluted=[notch])
    for label, cert in (('单带', cert_a), ('重叠带', cert_b)):
        outputs = request(port, 'evaluate', dict(source, certificate=cert))[0]['outputs']
        row = next(item for item in outputs['certificates'] if item['id'] == '基线')
        if row['gaps'] != [[514, 525]]:
            raise ValueError(f'{label}掩盖了陷波缺口')
        peak = next(item for item in outputs['candidate'] if item['id'] == '假基音')
        if peak['count'] or peak['state'] != 'polluted':
            raise ValueError(f'{label}的陷波区伪证参与了候选')
        pipe = next(item for item in outputs['coverage'] if item['id'] == '背管丁')
        if pipe['state'] == 'covered':
            raise ValueError(f'{label}的陷波区伪证把音管标成已覆盖')
    # With no certificate at all the same peak under the old definitions does assign.
    legacy = next(item for item in request(port, 'evaluate', source)[0]['outputs']['coverage'] if item['id'] == '背管丁')
    if legacy['state'] != 'covered':
        raise ValueError('无证书旧档未按原口径回放')
    # A peak that only the other band hears cannot fill the missing fundamental: one
    # certified absence is not cancelled by another blind retest.
    quiet = dict(id='静默链', temperature=20, opened=['背管丁'], floor=0.2,
                 certificate=dict(floor=0.2, bands=[dict(lo=500, hi=545)]), peaks=[])
    other = dict(id='旁带链', temperature=20, opened=['背管丁'], floor=0.2,
                 certificate=dict(floor=0.2, bands=[dict(lo=1000, hi=1100)]),
                 peaks=[dict(id='假基音', hz=520, amplitude=0.9)])
    outputs = request(port, 'evaluate', dict(data, review=dict(data['review'], takes=[quiet, other])))[0]['outputs']
    ledger = next(item for item in outputs['silence'] if item['id'] == '背管丁')
    if ledger['state'] != 'unseen' or ledger['evidence']['unseen'] != ['静默链']:
        raise ValueError('有证未见被盲测抵消')
    # Certificates must match the amplitude scale of their own session.
    take = dict(id='错门限', temperature=20, opened=['背管丁'], floor=0.5,
                certificate=dict(floor=0.2, bands=[dict(lo=500, hi=545)]), peaks=[])
    request(port, 'evaluate', dict(data, review=dict(data['review'], takes=[take])), status=400)
    request(port, 'evaluate', dict(data, certificate=dict(floor=0.9, bands=[dict(lo=1, hi=2)])), status=400)


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
            audit_silence(port, data)
        if flow == 'certificates':
            audit_certificates(port, data)
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
