"""候选唯一性与槽位争用回归。

链条第二节：
- 两个孤立看来都合格的峰，因争用同一槽位而一起失去归属；
- 非基音峰贴近幅值门槛，单幅值扰动在弱峰/候选间翻转；
- 争用组、槽位拥挤与冲突归因用独立重算交叉核对；
- 不允许按最小音分残差强行匹配。
"""
import unittest
from math import isclose
from tempfile import TemporaryDirectory

from pipeatlas.allocation import assign_peaks
from pipeatlas.domain import parse
from pipeatlas.harmonics import evaluate
from pipeatlas.reconcile import summarize
from pipeatlas.rules import decide

from .helpers import (FLOOR, TOLERANCE, WARM, analyze, cents, find, hz_for_cents,
                      make_source, only, temp_ratio, workbench)

BASE_A = 220.0
BASE_B = 440.0


def competition_source():
    """两个峰都只够到 温管甲/1 这一条槽，各自合格但互争唯一归属。"""
    target = BASE_A * temp_ratio(WARM)
    peaks = [
        ('争峰一', hz_for_cents(target, -8.0), 0.9),
        ('争峰二', hz_for_cents(target, 4.0), 0.7),
    ]
    return make_source([('温管甲', BASE_A, 2), ('闭管乙', BASE_B, 2)],
                       ['温管甲'], peaks)


def floor_source(amplitude):
    """非基音（二阶）峰，幅值贴近可信门槛。"""
    h2 = 2 * BASE_A * temp_ratio(WARM)
    return make_source([('温管甲', BASE_A, 2)], ['温管甲'],
                       [('贴门二阶峰', hz_for_cents(h2, 1.5), amplitude)])


class CandidateUniquenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wb = workbench(self.tmp.name + '/case.sqlite')

    def test_independent_graph_recomputes_candidates(self):
        source = competition_source()
        model = parse(source)
        facts = evaluate(model)
        target = BASE_A * temp_ratio(WARM)
        for peak in source['peaks']:
            with self.subTest(peak=peak['id']):
                deviation = cents(peak['hz'], target)
                self.assertTrue(abs(deviation) <= TOLERANCE)  # 孤立看合格
                edges = facts['graph'][peak['id']]
                self.assertEqual([(e[0], e[1]) for e in edges], [('温管甲', 1)])
                self.assertTrue(isclose(edges[0][2], deviation, abs_tol=1e-12))

    def test_two_valid_peaks_lose_attribution_together(self):
        source = competition_source()
        packet = analyze(self.wb, source)
        for peak_id in ('争峰一', '争峰二'):
            row = find(packet['outputs']['assign'], peak_id)
            self.assertIsNone(row['pipe'])
            self.assertIsNone(row['harmonic'])
            self.assertEqual(row['state'], 'ambiguous')
        conflict = {row['id']: row for row in packet['outputs']['conflict']}
        for peak_id in ('争峰一', '争峰二'):
            self.assertEqual(conflict[peak_id]['state'], 'contested')
            self.assertEqual(conflict[peak_id]['contested'], ['温管甲/1'])
        # 音分更小的峰也不能靠最小残差抢走归属
        residual_one = abs(cents(source['peaks'][0]['hz'], BASE_A * temp_ratio(WARM)))
        residual_two = abs(cents(source['peaks'][1]['hz'], BASE_A * temp_ratio(WARM)))
        self.assertGreater(residual_one, residual_two)
        self.assertIsNone(find(packet['outputs']['assign'], '争峰二')['pipe'])
        # 基音失证 → 不给调律偏差
        tune = find(packet['outputs']['tune'], '温管甲')
        self.assertIsNone(tune['deviation'])
        self.assertEqual(tune['state'], 'pending')

    def test_allocation_votes_require_uniqueness_on_both_ends(self):
        model = parse(competition_source())
        facts = evaluate(model)
        assigned, allocation, conflicts = assign_peaks(facts['graph'], facts['candidate'])
        self.assertEqual(assigned, {})
        self.assertEqual({row['id'] for row in conflicts}, {'争峰一', '争峰二'})
        self.assertTrue(all(row['state'] == 'ambiguous' for row in allocation))

    def test_cluster_groups_competing_peaks_as_oversubscribed(self):
        model = parse(competition_source())
        facts = evaluate(model)
        outputs = decide(model, facts)
        clusters = summarize(model, facts, outputs)
        cluster = only(clusters)
        self.assertEqual(cluster['count'], 2)
        self.assertEqual(cluster['slots'], 1)
        self.assertEqual(cluster['state'], 'oversubscribed')
        self.assertEqual(cluster['evidence']['peaks'], ['争峰一', '争峰二'])
        self.assertEqual(cluster['evidence']['slots'], ['温管甲/1'])

    def test_pressure_distinguishes_shared_slot_from_multi_candidate_peak(self):
        # 再加一个同时命中两条槽的峰，区分单峰多候选与多峰挤同槽。
        target_a1 = BASE_A * temp_ratio(WARM)
        source = competition_source()
        source['pipes'].append(dict(id='邻管丙', base=BASE_A, maximum=1))
        source['opened'].append('邻管丙')
        source['peaks'].append(dict(id='跨槽峰', hz=hz_for_cents(target_a1, 0.0), amplitude=0.6))
        source['review'] = dict(takes=[])
        packet = analyze(self.wb, source)
        pressure = {row['id']: row for row in packet['outputs']['pressure']}
        slot = pressure['温管甲/1']
        self.assertEqual(slot['count'], 3)
        self.assertEqual(slot['state'], 'contested')
        cross = find(packet['outputs']['candidate'], '跨槽峰')
        # 跨槽峰的候选数由独立图核对：同基频的两支音管一阶槽都命中
        model = parse(source)
        self.assertEqual(cross['count'], 2)
        self.assertEqual(len(evaluate(model)['graph']['跨槽峰']), 2)

    def test_amplitude_floor_edge_single_perturbation(self):
        above = floor_source(FLOOR + 0.001)
        below = floor_source(FLOOR - 0.001)
        good = analyze(self.wb, above)
        bad = analyze(self.wb, below)
        good_row = find(good['outputs']['candidate'], '贴门二阶峰')
        bad_row = find(bad['outputs']['candidate'], '贴门二阶峰')
        self.assertEqual(good_row['state'], 'candidate')
        self.assertEqual(good_row['count'], 1)
        self.assertEqual(bad_row['state'], 'weak')
        self.assertEqual(bad_row['count'], 0)
        # 弱峰不进候选图，也不产生争用
        self.assertEqual(evaluate(parse(below))['graph']['贴门二阶峰'], [])
        # 两次输入只有幅值一个字段不同
        self.assertEqual(below['pipes'], above['pipes'])
        self.assertEqual(below['peaks'][0]['hz'], above['peaks'][0]['hz'])
        self.assertNotEqual(below['peaks'][0]['amplitude'], above['peaks'][0]['amplitude'])

    def test_unique_endpoint_still_assigned_alongside_contest(self):
        # 争用组之外，双端唯一的峰照常归属，证明规则只冻结歧义部分。
        target = BASE_A * temp_ratio(WARM)
        source = competition_source()
        source['pipes'].append(dict(id='独管丙', base=330.0, maximum=1))
        source['opened'].append('独管丙')
        source['peaks'].append(dict(id='独占峰', hz=hz_for_cents(330.0 * temp_ratio(WARM), 0.5), amplitude=0.5))
        packet = analyze(self.wb, source)
        winner = find(packet['outputs']['assign'], '独占峰')
        self.assertEqual(winner['state'], 'assigned')
        self.assertEqual(winner['pipe'], '独管丙')
        self.assertTrue(isclose(winner['deviation'], cents(source['peaks'][-1]['hz'],
                                                           330.0 * temp_ratio(WARM)), abs_tol=1e-12))
        self.assertIsNone(find(packet['outputs']['assign'], '争峰一')['pipe'])


if __name__ == '__main__':
    unittest.main()
