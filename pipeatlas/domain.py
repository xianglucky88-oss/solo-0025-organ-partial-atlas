from math import sqrt
from . import certificate
from .contract import ident, invariant, num, obj, records


def parse(source):
    conditions = obj(source.get('conditions'), '温度读数')
    reference = num(conditions.get('reference'), '基准温度', 0, 40)
    temperature = num(conditions.get('temperature'), '当前温度', 0, 40)
    tolerance = num(conditions.get('tolerance'), '候选音分容差', 0.1, 50)
    passband = num(conditions.get('passband'), '调律音分容差', 0.1, tolerance)
    floor = num(conditions.get('floor'), '可信幅值下限', 0, 100)
    ratio = sqrt((273.15 + temperature) / (273.15 + reference))
    pipes = []
    for pipe in records(source.get('pipes'), '音管', 24, 1):
        base = num(pipe.get('base'), '音管基准频率', 16, 12000)
        maximum = num(pipe.get('maximum'), '泛音槽上限', 1, 12)
        invariant(type(maximum) is int, '泛音槽上限需为整数')
        pipes.append(dict(id=pipe['id'], base=base, maximum=maximum))
    opened = source.get('opened')
    invariant(isinstance(opened,list) and 0 < len(opened) <= len(pipes), '开管名单条数不合法')
    opened = [ident(item, '开管标识') for item in opened]
    invariant(len(opened)==len(set(opened)) and set(opened) <= {p['id'] for p in pipes},'开管名单不可重复或引用未知音管')
    peaks = []
    for p in records(source.get('peaks'), '频峰', 360):
        peaks.append(dict(id=p['id'],hz=num(p.get('hz'),'频率',1,160000),amplitude=num(p.get('amplitude'),'幅值',0,100)))
    cert = certificate.parse(source.get('certificate'), floor)
    return dict(pipes=pipes, opened=set(opened), peaks=peaks, reference=reference, ratio=ratio, floor=floor,
                tolerance=tolerance, passband=passband, certificate=cert)
