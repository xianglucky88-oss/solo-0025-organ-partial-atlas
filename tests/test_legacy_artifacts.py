"""原样例复核与封存产物的离线回归。

链条收尾：
- examples 下全部样例（含复核样例与拒绝样例）可重跑；
- 封存幂等、错误输入不落库、导出保留历史快照与下载头；
- 同版无差异、异版有差异、跨对象拒绝；
- 增强复核不改变基础口径，且随整包完整封存回读。
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeatlas.archive import Archive
from pipeatlas.artifacts import compare, verify_packet
from pipeatlas.contract import ContractError, decode
from pipeatlas.server import CONFIG
from pipeatlas.service import Workbench

from .helpers import workbench

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples'

EXAMPLE_FILES = ('sample.json', 'alternate.json', 'reject.json',
                 'review-sample.json', 'review-alternate.json', 'review-reject.json')


def load(name):
    return decode((EXAMPLES / name).read_bytes())


class ExampleRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wb = workbench(self.tmp.name + '/legacy.sqlite')

    def test_all_example_files_still_parse(self):
        for name in EXAMPLE_FILES:
            with self.subTest(name=name):
                data = load(name)
                self.assertEqual(data['id'], '北廊风箱')

    def test_valid_examples_evaluate_and_keep_observations(self):
        observations = json.loads((EXAMPLES / 'observations.json').read_text(encoding='utf8'))
        for name in ('sample.json', 'alternate.json'):
            data = load(name)
            packet = self.wb.analyze(data)
            for stage, spec in observations.items():
                offset = 3 if name == 'alternate.json' else 0
                row_id, field, expected = spec[offset], spec[offset + 1], spec[offset + 2]
                if field is None:
                    self.assertNotIn(row_id, [r['id'] for r in packet['outputs'][stage]])
                    continue
                row = next(r for r in packet['outputs'][stage] if r['id'] == row_id)
                self.assertEqual(row[field], expected, f'{name} {stage}.{row_id}.{field}')

    def test_review_examples_match_review_observations(self):
        observations = json.loads((EXAMPLES / 'review-observations.json').read_text(encoding='utf8'))
        for name, offset in (('review-sample.json', 0), ('review-alternate.json', 3)):
            packet = self.wb.analyze(load(name))
            for stage, spec in observations.items():
                row_id, field, expected = spec[offset], spec[offset + 1], spec[offset + 2]
                if field is None:
                    self.assertNotIn(row_id, [r['id'] for r in packet['outputs'][stage]])
                    continue
                row = next(r for r in packet['outputs'][stage] if r['id'] == row_id)
                self.assertEqual(row[field], expected, f'{name} {stage}.{row_id}.{field}')

    def test_reject_examples_raise_400_and_never_seal(self):
        archive = Archive(Path(self.tmp.name) / 'reject.sqlite')
        wb = Workbench(archive, CONFIG)
        for name in ('reject.json', 'review-reject.json'):
            with self.subTest(name=name):
                with self.assertRaises(ContractError) as caught:
                    wb.analyze(load(name))
                self.assertEqual(caught.exception.status, 400)
                with self.assertRaises(ContractError):
                    wb.execute('seal', load(name))
        self.assertEqual(archive.list(), [])

    def test_review_does_not_change_baseline_outputs(self):
        base = self.wb.analyze(load('sample.json'))
        enhanced = self.wb.analyze(load('review-sample.json'))
        for stage, rows in base['outputs'].items():
            self.assertEqual(enhanced['outputs'][stage], rows, f'复核改变了基础阶段 {stage}')
        optional = {op['id'] for op in CONFIG['operations'] if op.get('optional')}
        self.assertTrue(optional <= set(enhanced['outputs']))

    def test_seal_is_idempotent_and_export_preserves_snapshot(self):
        data = load('sample.json')
        first = self.wb.execute('seal', data)
        second = self.wb.execute('seal', data)
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(first['digest'], second['digest'])
        self.assertEqual(first['sequence'], second['sequence'])
        packet = self.wb.read(first['digest'])
        verify_packet(packet)
        self.assertEqual(packet, self.wb.analyze(data))
        revisions = self.wb.archive.list()
        self.assertEqual(len(revisions), 1)
        # 导出走归档读取，内容与封包逐字节一致
        export = self.wb.archive.read(first['digest'])
        self.assertEqual(export, packet)

    def test_compare_same_version_no_diff_other_version_differs(self):
        left = self.wb.execute('seal', load('sample.json'))
        right = self.wb.execute('seal', load('alternate.json'))
        self.assertEqual(compare(self.wb.read(left['digest']),
                                 self.wb.read(left['digest']), CONFIG['operations'])['rows'], [])
        diff = compare(self.wb.read(left['digest']),
                       self.wb.read(right['digest']), CONFIG['operations'])
        self.assertTrue(diff['rows'])
        with self.assertRaises(ContractError) as caught:
            other = dict(load('alternate.json'), id='另一对象')
            other_seal = self.wb.execute('seal', other)
            compare(self.wb.read(left['digest']),
                    self.wb.read(other_seal['digest']), CONFIG['operations'])
        self.assertEqual(caught.exception.status, 409)

    def test_review_packet_round_trips_through_archive(self):
        sealed = self.wb.execute('seal', load('review-sample.json'))
        reread = self.wb.read(sealed['digest'])
        verify_packet(reread)
        for stage in ('takes', 'isolated', 'stability', 'pressure', 'harmonics', 'handoff', 'silence'):
            self.assertIn(stage, reread['outputs'])
        self.assertEqual(reread['source'], load('review-sample.json'))


if __name__ == '__main__':
    unittest.main()
