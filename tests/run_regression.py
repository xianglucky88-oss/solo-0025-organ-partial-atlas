"""离线回归入口：运行温标换算、候选唯一性与复测交接整条链。

用法：python3 -m tests.run_regression
仅依赖标准库 unittest，使用临时成果库，不写入 .local，也不启动网络端口。
"""
import sys
import unittest

from . import test_candidate_uniqueness, test_legacy_artifacts, test_stability_handoff, test_temperature_scale


def load_suite():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for module in (test_temperature_scale, test_candidate_uniqueness,
                   test_stability_handoff, test_legacy_artifacts):
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


def main():
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(load_suite())
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
