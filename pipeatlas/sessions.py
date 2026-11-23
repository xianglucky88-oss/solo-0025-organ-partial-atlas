from math import sqrt

from . import certificate
from .contract import ident, invariant, num, records
from .harmonics import evaluate
from .rules import decide


def read_takes(review, model):
    valid = {p['id'] for p in model['pipes']}
    result = []
    for take in records(review.get('takes', []), '复测片段', 12):
        opened = take.get('opened')
        invariant(isinstance(opened, list) and len(opened) <= len(valid), '复测开管名单无效')
        keys = [ident(key, '复测音管') for key in opened]
        invariant(set(keys) <= valid and len(keys) == len(set(keys)), '复测名单有未知音管或重复')
        peaks = [dict(id=r['id'], hz=num(r.get('hz'), '复测频率', 1, 160000),
                      amplitude=num(r.get('amplitude'), '复测幅值', 0, 100))
                 for r in records(take.get('peaks'), '复测频峰', 100)]
        temperature = num(take.get('temperature'), '复测温度', 0, 40)
        # A chain may set its own amplitude scale; its certificate must match that scale.
        floor = num(take.get('floor', model['floor']), '复测幅值下限', 0, 100)
        cert = certificate.parse(take.get('certificate'), floor, f'复测证书 {take["id"]}')
        # Divide by the original reference scale rather than freezing the first take's temperature.
        configuration = dict(model, opened=set(keys), peaks=peaks, floor=floor, certificate=cert,
                             ratio=sqrt((temperature+273.15)/(model['reference']+273.15)))
        result.append((take['id'], configuration, decide(configuration, evaluate(configuration))))
    return result


def take_scope(takes):
    return [dict(id=key, count=len(conf['opened']), assigned=sum(r['state'] == 'assigned' for r in out['assign']),
                 pending=sum(r['state'] == 'pending' for r in out['tune']), state='single' if len(conf['opened']) == 1 else 'ensemble' if conf['opened'] else 'silent',
                 evidence=sorted(conf['opened'])) for key, conf, out in takes]
