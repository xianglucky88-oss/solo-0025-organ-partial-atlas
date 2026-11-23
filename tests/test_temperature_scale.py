"""温标换算与异温录音回归。

链条第一节：
- 用独立计算核对绝对温度平方根换算与音分；
- 生成与基准录音物理偏差相同的异温录音；
- 至少一个峰正好落在候选音分容差边缘，冻结温标时该基音失证；
- 只扰动一个温度或一个幅值，观测候选状态翻转。
"""
import unittest
from math import isclose
from tempfile import TemporaryDirectory

from pipeatlas.domain import parse
from pipeatlas.harmonics import cents as production_cents, evaluate

from .helpers import (FLOOR, TOLERANCE, WARM, analyze, cents, find, hz_for_cents,
                      make_source, take, temp_ratio, workbench)

BASE = 220.0
PIPES = [('温管甲', BASE, 2)]
OPENED = ['温管甲']


def recording(edge_cents, amplitude=FLOOR + 0.1, temperature=WARM):
    """异温录音：边缘峰相对「当次温度换算目标」偏离 edge_cents 音分。"""
    warm_target = BASE * temp_ratio(temperature)
    hz = hz_for_cents(warm_target, edge_cents)
    return make_source(PIPES, OPENED, [('边缘峰', hz, amplitude)], temperature=temperature)


class TemperatureScaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wb = workbench(self.tmp.name + '/case.sqlite')

    def test_independent_ratio_matches_domain(self):
        for temperature in (15.0, 20.0, 24.0, 30.0):
            with self.subTest(temperature=temperature):
                source = make_source(PIPES, OPENED, [], temperature=temperature)
                model = parse(source)
                self.assertTrue(isclose(model['ratio'], temp_ratio(temperature), rel_tol=0, abs_tol=1e-15))
                target = find(evaluate(model)['target'], '温管甲')
                self.assertTrue(isclose(target['hz'], BASE * temp_ratio(temperature), abs_tol=1e-12))

    def test_independent_cents_matches_production(self):
        target = BASE * temp_ratio(WARM)
        for offset in (-18.0, -11.035659422650907, 0.0, 5.0, 17.999999999999982):
            hz = hz_for_cents(target, offset)
            self.assertTrue(isclose(production_cents(hz, target), cents(hz, target), abs_tol=1e-12))

    def test_same_physical_deviation_across_temperatures(self):
        # 两次录音各自相对当次换算目标都偏离 +3 音分：物理偏差相同、赫兹不同。
        sources = []
        for temperature in (20.0, WARM):
            target = BASE * temp_ratio(temperature)
            peak = ('恒偏峰', hz_for_cents(target, 3.0), 0.8)
            sources.append(make_source(PIPES, OPENED, [peak], temperature=temperature))
        packets = [analyze(self.wb, s) for s in sources]
        deviations = [find(p['outputs']['assign'], '恒偏峰')['deviation'] for p in packets]
        self.assertTrue(all(isclose(d, 3.0, abs_tol=1e-9) for d in deviations))
        self.assertNotEqual(sources[0]['peaks'][0]['hz'], sources[1]['peaks'][0]['hz'])
        # 音分见证独立重算一致
        for source, packet in zip(sources, packets):
            row = find(packet['outputs']['assign'], '恒偏峰')
            target = BASE * temp_ratio(source['conditions']['temperature'])
            self.assertTrue(isclose(row['deviation'], cents(source['peaks'][0]['hz'], target), abs_tol=1e-12))

    def test_edge_peak_kept_with_correct_scale_lost_when_frozen(self):
        # 边缘峰相对正确（30℃）温标目标正好 +18 音分，顶在候选容差边缘。
        source = recording(TOLERANCE)
        packet = analyze(self.wb, source)
        edge = find(packet['outputs']['candidate'], '边缘峰')
        self.assertEqual(edge['state'], 'candidate')
        self.assertEqual(edge['count'], 1)
        self.assertEqual(edge['evidence'], ['温管甲/1'])

        model = parse(source)
        facts = evaluate(model)
        edge_row = find(facts['candidate'], '边缘峰')
        # 正确温标下的音分独立重算（恰好等于容差，含等号时保留）
        warm_target = BASE * temp_ratio(WARM)
        warm_cents = cents(edge_row['hz'], warm_target)
        self.assertTrue(isclose(warm_cents, TOLERANCE, abs_tol=1e-9))

        # 冻结温标：沿用基准温度比例 1.0，同一物理录音偏离 29 音分，基音失证。
        frozen = dict(model, ratio=1.0)
        frozen_facts = evaluate(frozen)
        self.assertEqual(frozen_facts['graph']['边缘峰'], [])
        self.assertEqual(find(frozen_facts['candidate'], '边缘峰')['state'], 'outside')
        # 走完整业务链重放冻结口径：可信基音缺失，不给调律偏差。
        frozen_packet = self.wb.analyze(
            {**source, 'conditions': {**source['conditions'], 'temperature': 20.0}})
        self.assertEqual(find(frozen_packet['outputs']['candidate'], '边缘峰')['state'], 'outside')
        tune = find(frozen_packet['outputs']['tune'], '温管甲')
        self.assertIsNone(tune['deviation'])
        self.assertEqual(tune['state'], 'pending')
        self.assertEqual(find(frozen_packet['outputs']['coverage'], '温管甲')['state'], 'missing')

    def test_single_temperature_perturbation_flips_candidate(self):
        # 同一组赫兹，只把温度从 30 改成 20（冻结温标），边缘峰从有候选变为超范围。
        warm = recording(TOLERANCE)
        frozen = {**warm, 'conditions': {**warm['conditions'], 'temperature': 20.0}}
        self.assertEqual(find(analyze(self.wb, warm)['outputs']['candidate'], '边缘峰')['state'], 'candidate')
        self.assertEqual(find(analyze(self.wb, frozen)['outputs']['candidate'], '边缘峰')['state'], 'outside')
        # 其余输入逐字段不变，只动温度
        self.assertEqual(frozen['peaks'], warm['peaks'])
        self.assertEqual(frozen['pipes'], warm['pipes'])
        self.assertNotEqual(frozen['conditions']['temperature'], warm['conditions']['temperature'])

    def test_single_amplitude_perturbation_flips_weak_peak(self):
        # 非基音峰贴近幅值门槛：只扰动一个幅值，弱峰/候选互翻。
        h2_target = 2 * BASE * temp_ratio(WARM)
        peak_hz = hz_for_cents(h2_target, 2.0)
        loud = make_source(PIPES, OPENED, [('二倍频峰', peak_hz, FLOOR + 0.01)])
        weak = make_source(PIPES, OPENED, [('二倍频峰', peak_hz, FLOOR - 0.01)])
        self.assertEqual(find(analyze(self.wb, loud)['outputs']['candidate'], '二倍频峰')['state'], 'candidate')
        weak_packet = analyze(self.wb, weak)
        weak_row = find(weak_packet['outputs']['candidate'], '二倍频峰')
        self.assertEqual(weak_row['state'], 'weak')
        self.assertEqual(weak_row['count'], 0)
        self.assertEqual(weak_packet['outputs']['assign'][0]['state'], 'weak')

    def test_take_uses_its_own_temperature_not_frozen_first_take(self):
        # 复测片段按各自温度独立换算；冻结首批温度会让第二批基音失证。
        target_20 = BASE * temp_ratio(20.0)
        target_30 = BASE * temp_ratio(WARM)
        review = dict(takes=[
            take('首批', 20.0, OPENED, [('首峰', hz_for_cents(target_20, 1.0), 0.8)]),
            take('次批', WARM, OPENED, [('次峰', hz_for_cents(target_30, 1.0), 0.8)]),
        ], stability_limit=5)
        source = make_source(PIPES, OPENED,
                             [('基音', hz_for_cents(target_30, 1.0), 0.8)], review=review)
        packet = analyze(self.wb, source)
        rows = {row['id']: row for row in packet['outputs']['takes']}
        self.assertEqual(rows['首批']['state'], 'single')
        self.assertEqual(rows['次批']['assigned'], 1)
        stability = find(packet['outputs']['stability'], '温管甲')
        self.assertEqual(stability['count'], 3)
        self.assertEqual(stability['state'], 'stable')


if __name__ == '__main__':
    unittest.main()
