from . import certificate
from .contract import num
from .sessions import read_takes, take_scope
from .take_evidence import isolated_support, stability
from .triage import harmonic_residuals, silent_openings, slot_pressure, tune_handoff


def certify(model, takes):
    result = [certificate.row('基线', model.get('certificate'))]
    result.extend(certificate.row(key, conf.get('certificate')) for key, conf, _ in takes)
    return result


def analyze_review(review, model, facts, outputs):
    takes = read_takes(review, model)
    limit = num(review.get('stability_limit', 5), '复测音分一致限值', 0, 100)
    stable = stability(takes, outputs, limit)
    isolated = isolated_support(takes, outputs)
    return dict(takes=take_scope(takes), isolated=isolated, stability=stable,
                pressure=slot_pressure(facts, outputs), harmonics=harmonic_residuals(outputs),
                handoff=tune_handoff(outputs, stable, isolated),
                silence=silent_openings(takes, outputs, model), certificates=certify(model, takes))
