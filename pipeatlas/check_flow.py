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
    spec = next((op for op in CONFIG['operations'] if op['id'] == flow), None)
    extension = spec.get('group', 'review') if spec and spec.get('optional') else None
    prefix = f'{extension}-' if extension else ''
    data = request(port, prefix+'sample')[0]
    alt = request(port, prefix+'alternate')[0]
    bad = json.loads((ROOT/'examples'/(prefix+'reject.json')).read_text(encoding='utf8'))
    original = request(port,'evaluate',data)[0]
    alternate = request(port,'evaluate',alt)[0]
    request(port,'evaluate',bad,status=400)
    if extension:
        request(port, 'seal', bad, status=400)
        for enhanced, source in ((original, data), (alternate, alt)):
            stripped = {key: value for key, value in source.items() if key != extension}
            baseline = request(port, 'evaluate', stripped)[0]
            if flow in baseline['outputs'] or any(enhanced['outputs'][k] != v for k,v in baseline['outputs'].items()):
                raise ValueError('扩展改变了基础口径')
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
    elif flow == 'recheck':
        source = json.loads((ROOT/'examples'/'plan-recheck.json').read_text(encoding='utf8'))
        digest = request(port, 'seal', source)[0]['digest']

        def recheck(take, status=200, key=None):
            return request(port, 'recheck', dict(plan=key or digest, take=take), status=status)[0]

        def take(key, temperature, peaks, opened=('律管甲', '律管乙')):
            return dict(id=key, temperature=temperature, opened=list(opened), peaks=peaks)

        def peak(key, hz):
            return dict(id=key, hz=hz, amplitude=0.8)

        # 两端温度归一后单管都在原允差内，约定音程却越界
        first = recheck(take('验收一', 24, [peak('甲峰', 222.1), peak('乙峰', 442.48)]))
        observe(first['intervals'], '甲乙八度', 'state', 'violated')
        observe(first['intervals'], '甲乙八度', 'offset', -6.7165748621012735)
        observe(first['references'], '律管乙', 'state', 'held')
        row = next(row for row in first['intervals'] if row['id'] == '甲乙八度')
        if not all(abs(reading) <= 5 for reading in row['evidence']['readings'].values()):
            raise ValueError('复验两端读数应都在原允差内')
        if first['state'] != 'rejected':
            raise ValueError('音程越界时复验应判不通过')
        # 读数回到约定区间内，参考管未漂移
        second = recheck(take('验收二', 20, [peak('甲峰', 220.2), peak('乙峰', 440.2)]))
        observe(second['intervals'], '甲乙八度', 'state', 'held')
        observe(second['references'], '律管乙', 'state', 'held')
        if second['state'] != 'accepted':
            raise ValueError('音程与参考管都合格时复验应通过')
        # 参考管实测偏差相对基线超出登记允差
        third = recheck(take('验收三', 20, [peak('甲峰', 220.4), peak('乙峰', 441.4)]))
        observe(third['intervals'], '甲乙八度', 'state', 'held')
        observe(third['references'], '律管乙', 'state', 'drift')
        observe(third['references'], '律管乙', 'drift', 5.49972704111046)
        if third['state'] != 'rejected':
            raise ValueError('参考管漂移超差时复验应判不通过')
        # 一端缺可信基音：证据不足，不把缺值当零
        fourth = recheck(take('验收四', 20, [peak('乙峰', 440)]))
        observe(fourth['intervals'], '甲乙八度', 'state', 'pending')
        observe(fourth['intervals'], '甲乙八度', 'offset', None)
        if fourth['state'] != 'pending':
            raise ValueError('缺可信基音时复验应标证据不足')
        # 复验条件沿用所选封存计划，而不是届时工作台里最新的音程或允差
        loosened = json.loads(json.dumps(source))
        loosened['plan']['intervals'][0]['lo'] = -10
        loosened['plan']['intervals'][0]['hi'] = 10
        loosened['conditions']['passband'] = 18
        request(port, 'seal', loosened)
        still = recheck(take('验收五', 24, [peak('甲峰', 222.1), peak('乙峰', 442.48)]))
        observe(still['intervals'], '甲乙八度', 'state', 'violated')
        drift = recheck(take('验收六', 20, [peak('甲峰', 220.4), peak('乙峰', 441.4)]))
        observe(drift['references'], '律管乙', 'state', 'drift')
        # 封存版本不含计划、试录引用未知音管、版本不存在
        recheck(take('验收七', 20, [peak('甲峰', 220)]), status=409, key=left['digest'])
        recheck(take('验收八', 20, [peak('甲峰', 220)], opened=('律管甲', '未知管')), status=400)
        recheck(take('验收九', 20, [peak('甲峰', 220)]), status=404, key='0'*64)
    else:
        raise ValueError(f'未知工作流：{flow}')


def main():
    parser = argparse.ArgumentParser(description='运行有界的生产工作流检查')
    parser.add_argument('flow', choices=[op['id'] for op in CONFIG['operations']]+['seal','compare','export','recheck'])
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
