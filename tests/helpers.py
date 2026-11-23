"""离线回归共用工具。

本模块用独立公式重算温标换算与音分，不复用生产代码
``pipeatlas.harmonics`` / ``pipeatlas.domain`` 的实现，
从而在测试里提供一份独立核对来源。
"""
import copy
from math import log2, sqrt

from pipeatlas.archive import Archive
from pipeatlas.server import CONFIG
from pipeatlas.service import Workbench

REFERENCE = 20.0
WARM = 30.0
COOL = 15.0
TOLERANCE = 18.0
PASSBAND = 5.0
FLOOR = 0.2

# 独立计算的物理常量（与 domain.py 的近似保持同一口径，但单独推导）
KELVIN_ZERO = 273.15


def temp_ratio(temperature, reference=REFERENCE):
    """绝对温度平方根近似，独立于生产代码重算。"""
    return sqrt((KELVIN_ZERO + temperature) / (KELVIN_ZERO + reference))


def cents(actual, target):
    """独立音分公式：1200 * log2(实测 / 目标)。"""
    return 1200.0 * log2(actual / target)


def hz_for_cents(target, cent_offset):
    """由目标频率与音分偏差反推实测频率。"""
    return target * 2.0 ** (cent_offset / 1200.0)


def make_conditions(temperature=WARM, tolerance=TOLERANCE, passband=PASSBAND, floor=FLOOR):
    return dict(reference=REFERENCE, temperature=temperature,
                tolerance=tolerance, passband=passband, floor=floor)


def make_source(pipes, opened, peaks, temperature=WARM, review=None,
                subject='离线回归对象', tolerance=TOLERANCE, passband=PASSBAND, floor=FLOOR):
    source = dict(
        id=subject,
        conditions=make_conditions(temperature, tolerance, passband, floor),
        pipes=[dict(id=p[0], base=p[1], maximum=p[2]) for p in pipes],
        opened=list(opened),
        peaks=[dict(id=p[0], hz=p[1], amplitude=p[2]) for p in peaks],
    )
    if review is not None:
        source['review'] = review
    return source


def take(take_id, temperature, opened, peaks):
    return dict(id=take_id, temperature=temperature, opened=list(opened),
                peaks=[dict(id=p[0], hz=p[1], amplitude=p[2]) for p in peaks])


def workbench(db_path):
    return Workbench(Archive(db_path), CONFIG)


def analyze(workbench, source):
    return workbench.analyze(copy.deepcopy(source))


def find(rows, row_id):
    return next(row for row in rows if row['id'] == row_id)


def only(rows):
    assert len(rows) == 1, f'期望恰好一条记录，实际 {len(rows)} 条'
    return rows[0]
