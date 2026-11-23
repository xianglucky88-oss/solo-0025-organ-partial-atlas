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


def near(rows, row_id, field, target, tol=1e-6):
    result = next((row for row in rows if row['id'] == row_id), None)
    if result is None:
        raise ValueError(f'{row_id} 不在结果中')
    actual = result.get(field)
    if not isinstance(actual, (int, float)) or abs(actual - target) > tol:
        raise ValueError(f'{row_id}.{field} 为 {actual!r}，应为 {target!r}')


def verify_handover(port, flow, data, original, alternate):
    """Check same-seat replacement, same-entity migration and an unregistered swap."""
    swap, migration, gaps = (original['outputs'][key] for key in ('swap', 'migration', 'unaligned'))
    observe(swap, '低音甲', 'state', 'swapped')
    observe(swap, '低音甲', 'entity', '甲二')
    exchange = next(row for row in swap if row['id'] == '低音甲')['evidence']
    if exchange['entities'] != ['甲一', '甲二']:
        raise ValueError('同名席位换管的实体序列不完整')
    boundary = next(item for item in exchange['observations'] if item['take'] == '甲换管当下')
    if boundary['entity'] != '甲二' or boundary['link'] != '联甲二' or boundary['credential'] != '维甲二':
        raise ValueError('同一秒换管没有以生效后的实体承接新试录')
    if abs(boundary['raw']['deviation'] - (-17.72986385952954)) > 1e-6:
        raise ValueError('换管当下的席位原始判定没有保留')

    # 2. The old pipe moved to another seat and keeps its own history.  Its
    #    observations align by entity and are re-expressed against the entity
    #    reference, not by subtracting two seat-target deviations.
    observe(swap, '备管己', 'entity', '丙一')
    observe(migration, '丙一', 'state', 'moved')
    migrated = next(row for row in migration if row['id'] == '丙一')
    if migrated['evidence']['seats'] != ['独管丙', '备管己'] or migrated['evidence']['base'] != 310.6:
        raise ValueError('旧管迁移没有按实体保留席位序列与自有基准')
    near(migration, '丙一', 'delta', -7.785839234771144)
    moved = next(item for item in migrated['evidence']['observations'] if item['take'] == '丙迁后')
    if abs(moved['cents']) > 1e-9 or abs(moved['raw']['deviation'] - 14.552974566045034) > 1e-6:
        raise ValueError('跨席位观测没有按实体基准重新表达音分')
    # The naive subtraction of the two seat-relative deviations must not be the
    # entity value: that would mix two different seat targets.
    before = next(item for item in migrated['evidence']['observations'] if item['take'] == '基线')
    naive = moved['raw']['deviation'] - before['raw']['deviation']
    if abs(naive - migrated['delta']) < 1:
        raise ValueError('跨席位偏差被直接相减')
    if abs(moved['cents']) > 1e-9 or abs(before['cents'] - 7.785839234771144) > 1e-6:
        raise ValueError('实体基准没有用事先选定的频率与温度')

    # 3. An unregistered replacement cannot be aligned: neither the seat row
    #    nor the entity row guesses an identity for it.
    observe(swap, '侧管庚', 'state', 'unbound')
    observe(swap, '侧管庚', 'entity', None)
    observe(gaps, '庚未登记/侧管庚', 'state', 'unbound')
    observe(gaps, '庚未登记/侧管庚', 'seat', '侧管庚')
    observe(swap, '副管辛', 'state', 'conflict')
    observe(gaps, '辛凭据冲突/副管辛', 'state', 'conflict')
    observe(swap, '背管丁', 'state', 'untimed')
    observe(swap, '背管丁', 'entity', None)
    observe(gaps, '丁无时刻/背管丁', 'state', 'untimed')
    observe(gaps, '基线/高音乙', 'state', 'untrusted')
    if any(item['seat'] == '侧管庚' and item['at'] == 250
           for row in migration for item in row['evidence']['observations']):
        raise ValueError('未登记更换被猜成了同一实体')
    observe(alternate['outputs']['swap'], '侧管庚', None, None)
    observe(alternate['outputs']['migration'], '庚一', None, None)

    # A sealed packet keeps the registry it was sealed with; later
    # registrations only affect new checks.
    sealed = request(port, 'seal', data)[0]
    stored = request(port, f"packet/{sealed['digest']}")[0]
    if stored != original:
        raise ValueError('封存核对改变了已绑定维修记录')
    later = json.loads(json.dumps(data))
    later['review']['registry']['links'].append(
        dict(id='联庚补登', seat='侧管庚', entity='庚一', start=240, end=None, credential='维庚补'))
    fresh = request(port, 'evaluate', later)[0]['outputs']
    observe(fresh['swap'], '侧管庚', 'state', 'unchanged')
    if any(row['id'] == '庚未登记/侧管庚' for row in fresh['unaligned']):
        raise ValueError('后补登记没有更正新的核对')
    if stored['outputs'] != original['outputs']:
        raise ValueError('后补登记改写了已封存的历史核对')


def audit(port, flow):
    handover = flow in ('swap', 'migration', 'unaligned')
    review = not handover and any(op['id'] == flow and op.get('optional') for op in CONFIG['operations'])
    prefix = 'handover-' if handover else 'review-' if review else ''
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
        if handover:
            verify_handover(port, flow, data, original, alternate)
            return
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
