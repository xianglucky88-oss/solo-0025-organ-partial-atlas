"""复测稳定性与调律交接回归。

链条第三节：
- 独立核对音分、槽位与稳定性见证（evidence）；
- 稳定性样本仍够（>=2 个可信基音点）与减少到不够（<2）两条交接结果；
- 漂移触发 revisit，稳定且调律合格才可信交接；
- 单管佐证只接受单管且独占的槽位。
"""
import unittest
from math import isclose
from tempfile import TemporaryDirectory

from pipeatlas.domain import parse
from pipeatlas.harmonics import evaluate
from pipeatlas.review import analyze_review
from pipeatlas.rules import decide
from pipeatlas.take_evidence import stability

from .helpers import (WARM, analyze, cents, find, hz_for_cents, make_source,
                      take, temp_ratio, workbench)

BASE = 260.0
PIPES = [('稳管己', BASE, 1), ('旁管庚', 520.0, 1)]


def baseline_source(takes, limit=5.0):
    target = BASE * temp_ratio(WARM)
    base_peak = ('基线峰', hz_for_cents(target, 0.0), 0.8)
    review = dict(takes=takes, stability_limit=limit)
    return make_source(PIPES, ['稳管己'], [base_peak], review=review)


def single_take(take_id, temperature, offset):
    target = BASE * temp_ratio(temperature)
    return take(take_id, temperature, ['稳管己'],
                [('复测峰', hz_for_cents(target, offset), 0.8)])


class StabilityHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wb = workbench(self.tmp.name + '/case.sqlite')

    def test_stability_witnesses_hold_independent_cents(self):
        source = baseline_source([single_take('复测甲', 20.0, 0.0),
                                  single_take('复测乙', 25.0, 0.0)])
        packet = analyze(self.wb, source)
        row = find(packet['outputs']['stability'], '稳管己')
        self.assertEqual(row['count'], 3)
        self.assertEqual(set(row['evidence']), {'基线', '复测/复测甲', '复测/复测乙'})
        # 每个见证点都与独立音分重算一致（各自温度修正后物理偏差同为 0 音分）
        baseline_target = BASE * temp_ratio(WARM)
        self.assertTrue(isclose(row['evidence']['基线'],
                                cents(source['peaks'][0]['hz'], baseline_target), abs_tol=1e-12))
        for take_data, witness in zip(source['review']['takes'], ('复测/复测甲', '复测/复测乙')):
            target = BASE * temp_ratio(take_data['temperature'])
            self.assertTrue(isclose(row['evidence'][witness],
                                    cents(take_data['peaks'][0]['hz'], target), abs_tol=1e-12))
            self.assertTrue(isclose(row['evidence'][witness], 0.0, abs_tol=1e-9))
        self.assertTrue(isclose(row['spread'], 0.0, abs_tol=1e-9))
        self.assertEqual(row['state'], 'stable')

    def test_enough_samples_stable_handoff_is_ready(self):
        source = baseline_source([single_take('复测甲', 22.0, 1.0),
                                  single_take('复测乙', 26.0, -1.0)])
        packet = analyze(self.wb, source)
        stable = find(packet['outputs']['stability'], '稳管己')
        self.assertGreaterEqual(stable['count'], 2)
        self.assertEqual(stable['state'], 'stable')
        handoff = find(packet['outputs']['handoff'], '稳管己')
        self.assertEqual(handoff['state'], 'ready')
        self.assertEqual(handoff['evidence']['stability'], 'stable')
        self.assertFalse(handoff['evidence']['isolated_lead'])

    def test_drift_handoff_requires_revisit(self):
        # 基线上偏离 0，复测偏离 8 音分，超过 5 音分限值。
        source = baseline_source([single_take('复测甲', 22.0, 8.0)], limit=5.0)
        packet = analyze(self.wb, source)
        row = find(packet['outputs']['stability'], '稳管己')
        self.assertEqual(row['state'], 'drift')
        handoff = find(packet['outputs']['handoff'], '稳管己')
        self.assertEqual(handoff['state'], 'revisit')
        self.assertEqual(handoff['evidence']['stability'], 'drift')

    def test_reduced_to_insufficient_samples_stays_pending(self):
        # 基线可信但没有任何复测：只有 1 个见证点，样本不够，交接不能放行。
        enough = baseline_source([single_take('复测甲', 22.0, 1.0)])
        sparse = baseline_source([])
        good = analyze(self.wb, enough)
        thin = analyze(self.wb, sparse)
        self.assertEqual(find(good['outputs']['stability'], '稳管己')['count'], 2)
        thin_row = find(thin['outputs']['stability'], '稳管己')
        self.assertEqual(thin_row['count'], 1)
        self.assertIsNone(thin_row['spread'])
        self.assertEqual(thin_row['state'], 'pending')
        handoff = find(thin['outputs']['handoff'], '稳管己')
        # 稳定性见证本身停在 pending，调律交接不得升级为可信放行
        self.assertEqual(handoff['evidence']['stability'], 'pending')
        self.assertNotIn(handoff['state'], ('ready', 'adjust'))
        self.assertEqual(handoff['state'], 'pending')

    def test_removing_one_take_downgrades_handoff_chain(self):
        # 同一条链：两份复测 → 三个见证点稳定可放行；抽掉复测只剩基线单点 → 交接待定。
        two = baseline_source([single_take('复测甲', 21.0, 0.5),
                               single_take('复测乙', 27.0, -0.5)])
        none_left = baseline_source([])
        self.assertEqual(find(analyze(self.wb, two)['outputs']['handoff'], '稳管己')['state'], 'ready')
        thin = find(analyze(self.wb, none_left)['outputs']['handoff'], '稳管己')
        self.assertEqual(thin['evidence']['stability'], 'pending')
        self.assertEqual(thin['state'], 'pending')

    def test_isolated_support_requires_single_pipe_and_exclusive_slot(self):
        # 合奏里两个峰争 稳管己/1；单管复测独占该槽才能给出线索。
        target = BASE * temp_ratio(WARM)
        peaks = [('争峰一', hz_for_cents(target, -6.0), 0.9),
                 ('争峰二', hz_for_cents(target, 5.0), 0.7)]
        review = dict(takes=[take('单管佐证批', WARM, ['稳管己'],
                                  [('独证峰', hz_for_cents(target, 1.0), 0.8)])],
                      stability_limit=5)
        source = make_source(PIPES, ['稳管己'], peaks, review=review)
        packet = analyze(self.wb, source)
        supported = {row['id']: row for row in packet['outputs']['isolated']}
        for peak_id in ('争峰一', '争峰二'):
            row = supported[peak_id]
            self.assertEqual(row['state'], 'supported')
            self.assertEqual(row['evidence'], {'稳管己/1': ['单管佐证批/独证峰']})
        # 同一复核结果直接用归约函数独立复算一遍，证据与整包输出一致
        model = parse(source)
        facts = evaluate(model)
        iso_rows = analyze_review(source['review'], model, facts, decide(model, facts))['isolated']
        direct = {row['id']: row for row in iso_rows}
        for peak_id in ('争峰一', '争峰二'):
            self.assertEqual(direct[peak_id]['evidence'],
                             supported[peak_id]['evidence'])

    def test_ensemble_take_never_counts_as_isolated_lead(self):
        target = BASE * temp_ratio(WARM)
        review = dict(takes=[take('合奏批', WARM, ['稳管己', '旁管庚'],
                                  [('合奏峰', hz_for_cents(target, 1.0), 0.8),
                                   ('旁峰', hz_for_cents(520.0 * temp_ratio(WARM), 0.0), 0.8)])],
                      stability_limit=5)
        source = make_source(PIPES, ['稳管己'],
                             [('争峰一', hz_for_cents(target, -6.0), 0.9),
                              ('争峰二', hz_for_cents(target, 5.0), 0.7)], review=review)
        packet = analyze(self.wb, source)
        self.assertTrue(all(row['state'] == 'unconfirmed' for row in packet['outputs']['isolated']))
        handoff = find(packet['outputs']['handoff'], '稳管己')
        self.assertFalse(handoff['evidence']['isolated_lead'])

    def test_stability_helper_independent_of_packet(self):
        source = baseline_source([single_take('复测甲', 24.0, 3.0)])
        model = parse(source)
        facts = evaluate(model)
        outputs = decide(model, facts)
        rows = stability([], outputs, 5.0)  # 无复测时只看基线
        row = find(rows, '稳管己')
        self.assertEqual(row['count'], 1)
        self.assertEqual(row['state'], 'pending')
        self.assertTrue(isclose(row['evidence']['基线'], 0.0, abs_tol=1e-12))


if __name__ == '__main__':
    unittest.main()
