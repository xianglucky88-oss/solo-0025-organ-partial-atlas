"""复算《调律交接接收手册》里的每一个判断。

离线读取 examples/handoff 的旧档样例，用与生产相同的归约链路重算，
断言手册中的交接矩阵、反例与温度陷阱；不写成果库，不启动 HTTP。
"""
import json
import sys
from math import log2, sqrt
from pathlib import Path
from tempfile import TemporaryDirectory

from .archive import Archive
from .artifacts import verify_packet
from .service import Workbench

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'workbench.json').read_text(encoding='utf-8'))
FIXTURES = ROOT / 'examples' / 'handoff'
LIMIT = 5.0
PIPE_TUNE = '低音甲'
PIPE_ADJUST = '独管丙'
SLOT_PIPE = '高音乙'


def cents(actual, target):
    return 1200 * log2(actual / target)


def analyze(name):
    """按生产链路归约一份旧档样例，并复核包内摘要。"""
    with TemporaryDirectory(prefix='handoff-rehearsal-') as directory:
        workbench = Workbench(Archive(Path(directory) / 'rehearsal.sqlite'), CONFIG)
        packet = workbench.analyze(json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8')))
    verify_packet(packet)
    return packet


def row(rows, row_id):
    found = next((item for item in rows if item['id'] == row_id), None)
    if found is None:
        raise AssertionError(f'结果中缺少 {row_id}')
    return found


def close(actual, expected, tolerance=1e-9):
    return abs(actual - expected) <= tolerance


def main():
    packets = {name: analyze(name) for name in
               ('baseline', 'ensemble', 'solo', 'both', 'counterexample',
                'two-open-no-solo', 'ensemble-24c')}
    outputs = {name: packet['outputs'] for name, packet in packets.items()}
    failures = []

    def claim(condition, message):
        if not condition:
            failures.append(message)

    # 1. 单份材料只解锁一项：合奏 → 低音甲可签；独奏 → 高音乙可列复查。
    matrix = []
    for name, label in (('baseline', '两份都未核验'), ('ensemble', '仅核验合奏'),
                        ('solo', '仅核验独奏'), ('both', '两份都已核验')):
        handoff = outputs[name]['handoff']
        sign = row(handoff, PIPE_TUNE)['state'] == 'ready'
        listing = row(handoff, SLOT_PIPE)['state'] == 'review'
        matrix.append((label, sign, listing))
        if name in ('ensemble', 'solo'):
            claim(sign != listing, f'{name}：一份材料应只解锁一项，实际 签发={sign} 列复查={listing}')

    claim(matrix[0] == ('两份都未核验', False, False), '基线不应已可交接')
    claim(matrix[1] == ('仅核验合奏', True, False), '合奏材料应只签发低音甲')
    claim(matrix[2] == ('仅核验独奏', False, True), '独奏材料应只列高音乙复查')
    claim(matrix[3] == ('两份都已核验', True, True), '两份齐备应两项都可交接')

    # 1b. 基线的 tune.state 已 ready，但 handoff.state 仍 pending：
    #     工作台显示的“已符合”不是复测稳定性，不能直接当交接结论。
    baseline_handoff = row(outputs['baseline']['handoff'], PIPE_TUNE)
    claim(row(outputs['baseline']['tune'], PIPE_TUNE)['state'] == 'ready',
          '基线低音甲 tune.state 应为 ready')
    claim(baseline_handoff['state'] == 'pending'
          and baseline_handoff['evidence']['stability'] == 'pending',
          '基线低音甲 handoff.state 应为 pending，显示范围不得升级为交接结论')

    # 2. 合奏材料的数值与状态。
    ensemble = outputs['ensemble']
    stable = row(ensemble['stability'], PIPE_TUNE)
    claim(stable['state'] == 'stable' and stable['count'] == 2 and close(stable['spread'], 0.0),
          '合奏：低音甲稳定性应为 count=2、spread=0、stable')
    claim(row(ensemble['stability'], PIPE_ADJUST)['state'] == 'stable',
          '合奏：独管丙稳定性应为 stable')
    claim(row(ensemble['handoff'], PIPE_ADJUST)['state'] == 'adjust',
          '合奏：基线待调的独管丙应可签发待调律判断')
    claim(all(item['state'] == 'unconfirmed' for item in ensemble['isolated']),
          '合奏：开管数大于 1 的复测不能作单管佐证')
    claim(row(ensemble['handoff'], SLOT_PIPE)['state'] == 'pending',
          '合奏：高音乙不应因合奏材料变成复查对象')

    # 3. 独奏材料的数值与状态。
    solo = outputs['solo']
    claim(row(solo['stability'], PIPE_TUNE)['state'] == 'pending'
          and row(solo['stability'], PIPE_TUNE)['count'] == 1,
          '独奏：只有二倍频，不能为低音甲提供基音复测点')
    claim(row(solo['handoff'], PIPE_TUNE)['state'] == 'pending',
          '独奏：低音甲不应因此可签发调律判断')
    supported = {item['id']: item for item in solo['isolated']}
    claim(supported['峰乙']['state'] == 'supported'
          and supported['峰乙']['evidence'] == {'低音甲/2': ['低音独奏复测/二倍频']},
          '独奏：峰乙应获得低音甲/2 的单管佐证')
    claim(row(solo['handoff'], SLOT_PIPE)['state'] == 'review',
          '独奏：高音乙应可列为复查对象')

    # 4. 开管名单不是单管时，整场复测不能冒名单管佐证。
    two_open = outputs['two-open-no-solo']
    claim(all(item['state'] == 'unconfirmed' for item in two_open['isolated']),
          '双开复测即使只录到一个峰，也不能作单管佐证')
    claim(row(two_open['handoff'], SLOT_PIPE)['state'] == 'pending',
          '双开复测不应让高音乙成为复查对象')

    # 5. 未核验复测反证“所有原复测都支持稳定”。
    control = outputs['counterexample']
    drift = row(control['stability'], PIPE_ADJUST)
    claim(drift['state'] == 'drift' and drift['count'] == 3,
          '反例：独管丙应因第三场复测变为 drift、count=3')
    claim(close(drift['spread'], cents(312.0, 310.0) - cents(312.0, 310.0 * sqrt((24 + 273.15) / (20 + 273.15)))),
          '反例：独管丙散布应为按各自温度修正后的 11.7314 音分')
    claim(row(control['handoff'], PIPE_ADJUST)['state'] == 'revisit',
          '反例：独管丙应转为需要复测')
    claim(row(control['handoff'], PIPE_TUNE)['state'] == 'ready',
          '反例不应改变低音甲的签发状态')

    # 6. 温度陷阱：不按每场温度修正会得到假稳定。
    naive_spread = abs(cents(312.0, 310.0) - cents(312.0, 310.0))
    claim(close(naive_spread, 0.0) and drift['state'] == 'drift',
          '温度陷阱应表现为：直接比赫兹得 0.0，修正后得 11.7314')

    # 7. 合奏复测若在另一温标下录入，结论会翻转，故温标必须列入核验。
    warm = row(outputs['ensemble-24c']['handoff'], PIPE_TUNE)
    claim(warm['state'] == 'revisit', '温标改变时低音甲应转为需要复测')
    claim(row(outputs['ensemble-24c']['handoff'], PIPE_ADJUST)['state'] == 'revisit',
          '温标改变时独管丙应转为需要复测')

    print('交接目的 → 材料 → 可交接项')
    print(f'{"材料核验情况":<14}{"低音甲 签发调律判断":<20}{"高音乙 列复查对象":<18}')
    for label, sign, listing in matrix:
        print(f'{label:<14}{("是" if sign else "否"):<20}{("是" if listing else "否"):<18}')
    print()
    print('关键复算：')
    print(f'  合奏低音甲 stability = {json.dumps(stable, ensure_ascii=False, sort_keys=True)}')
    print(f'  独奏峰乙 isolated    = {json.dumps(supported["峰乙"], ensure_ascii=False, sort_keys=True)}')
    print(f'  反例独管丙 stability = {json.dumps(drift, ensure_ascii=False, sort_keys=True)}')
    print(f'  温度陷阱：直接比赫兹散布 {naive_spread:.6f}，按各自温度修正后 {drift["spread"]:.6f}')
    print()
    print('成果摘要（同一输入应复现同一摘要）：')
    for name, packet in packets.items():
        print(f'  {name:<18}{packet["digest"]}')

    if failures:
        print('\n复算失败：', file=sys.stderr)
        for message in failures:
            print(f'  - {message}', file=sys.stderr)
        return 1
    print('\n手册全部断言通过：单份材料只解锁一项，反例与温度陷阱均可复现。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
