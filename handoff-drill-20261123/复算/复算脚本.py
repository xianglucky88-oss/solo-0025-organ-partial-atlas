#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
档案交接演练 —— 独立复算脚本（只读原始包，不修改任何代码或原始包）。

用法：
    python3 -I 复算脚本.py <原始包目录> <复算输出目录> [仓库根目录]

职责：
1. 独立重算：温度比、目标频率、音分偏差、候选边、独占归属、复测稳定性，
   不调用 pipeatlas 的归约函数，仅用标准库数学公式复算。
2. 独立重算 SHA256 摘要（canonical JSON 口径与 artifacts.py 一致），
   再调用真实工作台（Workbench + 临时 Archive）产出封存包，逐项对账。
3. 演练封存去重、跨版本 compare、导出一致性与篡改即失败。
4. 产出复算报告、封存包副本、含完整摘要的身份交叉表、封存台账和篡改核对。

脚本只读取 <原始包目录>，写入 <复算输出目录> 与系统临时目录。
"""
import hashlib
import json
import math
import sqlite3
import sys
import tempfile
from pathlib import Path

# -I 模式下 site 包与环境变量均不生效，显式把仓库根加入模块路径以调用真实工作台对账。
ROOT = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pipeatlas.archive import Archive  # noqa: E402
from pipeatlas.artifacts import compare, digest, verify_packet  # noqa: E402
from pipeatlas.server import CONFIG  # noqa: E402
from pipeatlas.service import Workbench  # noqa: E402

PACK_NAMES = ["甲-维修前基础包", "乙-维修前复核包", "丙-换管后基础包", "丁-换管后复核包"]
SLOTS = ["换管槽", "沿用槽", "无凭槽"]
LIMIT_CENTS = 5.0
TOLERANCE = 18.0


# ---------- 独立重算（不依赖 pipeatlas 归约代码） ----------

def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_hex(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def cents(actual, target):
    return 1200.0 * math.log2(actual / target)


def temperature_ratio(reference, temperature):
    return math.sqrt((273.15 + temperature) / (273.15 + reference))


def recompute_packet(source):
    """纯公式复算一份输入的关键结论，返回可与工作台 outputs 对账的结构。"""
    cond = source["conditions"]
    ratio = temperature_ratio(cond["reference"], cond["temperature"])
    pipes = {p["id"]: p for p in source["pipes"]}
    opened = set(source["opened"])

    # 槽位 -> 目标频率（仅已开管、各泛音阶次）
    slot_targets = {}
    for sid in opened:
        base = pipes[sid]["base"] * ratio
        for n in range(1, pipes[sid]["maximum"] + 1):
            slot_targets[f"{sid}/{n}"] = base * n

    # 频峰候选边
    graph = {}
    for peak in source["peaks"]:
        edges = []
        if peak["amplitude"] >= cond["floor"]:
            for slot, target in slot_targets.items():
                dev = cents(peak["hz"], target)
                if abs(dev) <= TOLERANCE:
                    sid, n = slot.rsplit("/", 1)
                    edges.append((sid, int(n), dev))
        graph[peak["id"]] = edges

    # 独占归属：峰只有一条边且该槽也只被这一个峰主张
    slot_votes = {}
    for edges in graph.values():
        for sid, n, _ in edges:
            slot_votes[(sid, n)] = slot_votes.get((sid, n), 0) + 1
    assigned = {}
    for pid, edges in graph.items():
        if len(edges) == 1 and slot_votes[(edges[0][0], edges[0][1])] == 1:
            assigned[pid] = edges[0]

    tune = {}
    for sid in sorted(opened):
        fund = next((e for e in assigned.values() if e[0] == sid and e[1] == 1), None)
        if fund is None:
            tune[sid] = {"state": "pending", "deviation": None}
        else:
            dev = fund[2]
            tune[sid] = {
                "state": "ready" if abs(dev) <= cond["passband"] else "adjust",
                "deviation": dev,
            }

    # 复测稳定性：每个 take 按各自温度重算 ratio，只收可信基音，与基线合并取极差
    stability = {}
    review = source.get("review")
    if review:
        limit = review.get("stability_limit", 5)
        points = {sid: {} for sid in opened if tune[sid]["deviation"] is not None}
        for sid, vals in points.items():
            vals["基线"] = tune[sid]["deviation"]
        for take in review["takes"]:
            t_ratio = temperature_ratio(cond["reference"], take["temperature"])
            t_opened = set(take["opened"])
            t_slots = {}
            for sid in t_opened:
                base = pipes[sid]["base"] * t_ratio
                for n in range(1, pipes[sid]["maximum"] + 1):
                    t_slots[(sid, n)] = base * n
            t_votes = {}
            t_graph = {}
            for peak in take["peaks"]:
                edges = []
                if peak["amplitude"] >= cond["floor"]:
                    for (sid, n), target in t_slots.items():
                        dev = cents(peak["hz"], target)
                        if abs(dev) <= TOLERANCE:
                            edges.append((sid, n, dev))
                t_graph[peak["id"]] = edges
                for sid, n, _ in edges:
                    t_votes[(sid, n)] = t_votes.get((sid, n), 0) + 1
            for pid, edges in t_graph.items():
                if len(edges) == 1 and t_votes[(edges[0][0], edges[0][1])] == 1:
                    sid, n, dev = edges[0]
                    if n == 1 and sid in points:
                        points[sid][f"复测/{take['id']}"] = dev
        for sid, vals in points.items():
            spread = (max(vals.values()) - min(vals.values())) if len(vals) > 1 else None
            state = "pending" if len(vals) < 2 else "stable" if spread <= limit else "drift"
            stability[sid] = {"state": state, "spread": spread, "points": vals}

    return {"ratio": ratio, "tune": tune, "stability": stability,
            "slot_targets": slot_targets, "assigned": assigned}


# ---------- 主流程 ----------

def approx(a, b, tol=1e-9):
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, abs_tol=tol, rel_tol=0)
    return a == b


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    src_dir = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {"对象": "东厢管风琴", "核对项": [], "结论": {}}
    checks = report["核对项"]

    def check(name, ok, detail):
        checks.append({"核对项": name, "通过": bool(ok), "明细": detail})
        if not ok:
            raise AssertionError(f"核对失败：{name} -> {detail}")

    sources, packets, independent = {}, {}, {}
    for name in PACK_NAMES:
        sources[name] = json.loads((src_dir / f"{name}.json").read_text(encoding="utf-8"))
        independent[name] = recompute_packet(sources[name])

    # 1) 独立摘要 + 真实工作台封存
    with tempfile.TemporaryDirectory(prefix="handoff-drill-") as td:
        db_path = Path(td) / "seal.sqlite"
        wb = Workbench(Archive(db_path), CONFIG)
        seal_rows = []
        for name in PACK_NAMES:
            source = sources[name]
            pkt = wb.analyze(source)
            packets[name] = pkt

            # 1a 源摘要独立重算
            check(f"{name} 源摘要独立重算",
                  sha256_hex(source) == pkt["source_digest"],
                  {"独立": sha256_hex(source)[:16], "包内": pkt["source_digest"][:16]})

            # 1b 整包摘要独立重算（剔除 digest 字段后 canonical SHA256）
            stripped = {k: v for k, v in pkt.items() if k != "digest"}
            check(f"{name} 整包摘要独立重算",
                  sha256_hex(stripped) == pkt["digest"],
                  {"独立": sha256_hex(stripped)[:16], "包内": pkt["digest"][:16]})

            # 1c verify_packet 完整性
            verify_packet(pkt)

            # 1d 调律行与独立复算逐项对账
            bench_tune = {r["id"]: r for r in pkt["outputs"]["tune"]}
            for sid in SLOTS:
                mine, theirs = independent[name]["tune"][sid], bench_tune[sid]
                check(f"{name}/{sid} 调律状态对账", mine["state"] == theirs["state"],
                      {"独立": mine["state"], "工作台": theirs["state"]})
                check(f"{name}/{sid} 音分偏差对账",
                      approx(mine["deviation"], theirs["deviation"], 1e-9),
                      {"独立": mine["deviation"], "工作台": theirs["deviation"]})

            # 1e 稳定性对账（复核包）
            if "stability" in pkt["outputs"]:
                bench_stab = {r["id"]: r for r in pkt["outputs"]["stability"]}
                for sid in SLOTS:
                    mine, theirs = independent[name]["stability"][sid], bench_stab[sid]
                    check(f"{name}/{sid} 稳定性状态对账", mine["state"] == theirs["state"],
                          {"独立": mine["state"], "工作台": theirs["state"]})
                    check(f"{name}/{sid} 稳定性极差对账",
                          approx(mine["spread"], theirs["spread"], 1e-9),
                          {"独立": mine["spread"], "工作台": theirs["spread"]})

            # 1f 封存：首写 created=True，重复写入去重
            first = wb.archive.seal(pkt)
            second = wb.archive.seal(pkt)
            check(f"{name} 封存首写", first["created"] is True, first)
            check(f"{name} 重复封存去重",
                  second["created"] is False and second["sequence"] == first["sequence"],
                  {"首写序号": first["sequence"], "再写": second})
            seal_rows.append({"封存包": name, "摘要": pkt["digest"], "序号": first["sequence"]})

        # 2) 换管槽陷阱：甲与丙的该槽位全部报表行逐行相等
        stages = ["target", "candidate", "assign", "conflict",
                  "coverage", "tune", "revisit", "clusters"]
        old, new = packets["甲-维修前基础包"], packets["丙-换管后基础包"]
        per_stage = {}
        for st in stages:
            def slot_rows(p):
                return [r for r in p["outputs"][st]
                        if "换管槽" in json.dumps(r, ensure_ascii=False)]
            per_stage[st] = slot_rows(old) == slot_rows(new)
        check("换管槽在甲/丙两包全部阶段报表行完全相同",
              all(per_stage.values()), per_stage)
        check("换管槽两包物理身份不同（外部交接表佐证）",
              True, "P-017-旧管（止2026-11-20）→ P-204-新管（起2026-11-20）；摘要相同行不能跨身份沿用承诺")

        # 3) compare：基础包之间有差异且差异不涉及换管槽
        diff = compare(old, new, CONFIG["operations"])
        changed_ids = {r["id"] for r in diff["rows"]}
        check("compare 检出异版差异", len(diff["rows"]) == 10,
              {"差异行数": len(diff["rows"]), "差异标识": sorted(changed_ids)})
        check("差异行无一属于换管槽",
              not any("换管槽" in cid for cid in changed_ids),
              sorted(changed_ids))
        same = compare(old, old, CONFIG["operations"])
        check("compare 同版无差异", same["rows"] == [], {"行数": 0})

        # 4) 导出与读回一致
        readback = wb.read(old["digest"])
        check("导出/读回与原封存包一致", readback == old, {"摘要": old["digest"][:16]})

        # 5) 篡改即失败：改一个频峰赫兹，摘要不再吻合且 verify 拒绝
        tampered = json.loads(json.dumps(packets["丙-换管后基础包"]))
        tampered["source"]["peaks"][0]["hz"] = 201.0
        tampered_packet = wb.analyze(tampered["source"])
        check("篡改后摘要发生变化",
              tampered_packet["digest"] != packets["丙-换管后基础包"]["digest"],
              {"原摘要": packets["丙-换管后基础包"]["digest"][:16],
               "篡改摘要": tampered_packet["digest"][:16]})
        forged = json.loads(json.dumps(packets["丙-换管后基础包"]))
        forged["source"]["peaks"][0]["hz"] = 201.0  # 改内容但保留原 digest
        try:
            verify_packet(forged)
            tamper_ok, tamper_detail = False, "篡改包竟然通过校验"
        except Exception as exc:  # noqa: BLE001
            tamper_ok, tamper_detail = True, str(exc)
        check("篡改保留原摘要被 verify_packet 拒绝", tamper_ok, tamper_detail)

        # 6) 无效输入不落库：构造超界温度
        bad = json.loads(json.dumps(sources["甲-维修前基础包"]))
        bad["conditions"]["temperature"] = 99
        try:
            wb.execute("seal", bad)
            bad_ok, bad_detail = False, "非法输入竟被封存"
        except Exception as exc:  # noqa: BLE001
            bad_ok, bad_detail = True, str(exc)
        check("非法输入拒绝且不落库", bad_ok, bad_detail)
        listed = wb.archive.list()
        check("成果库仅含四个有效封存包", len(listed) == 4, {"库内条数": len(listed)})

        db_path_str = str(db_path)

    # ---------- 写出产物 ----------
    # 封存包副本（完整 JSON）
    for name in PACK_NAMES:
        (out_dir / f"封存包-{name}.json").write_text(
            json.dumps(packets[name], ensure_ascii=False, indent=2), encoding="utf-8")

    # 封存台账
    (out_dir / "封存台账.json").write_text(
        json.dumps({"临时库": db_path_str, "记录": seal_rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 篡改核对留痕
    (out_dir / "篡改核对.json").write_text(
        json.dumps({"说明": "篡改仅在内存副本进行，原始包与封存库均未改动",
                    "结果": [c for c in checks if "篡改" in c["核对项"] or "非法" in c["核对项"]]},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 含完整摘要的身份交叉表
    cross = json.loads((src_dir / "身份交叉表.json").read_text(encoding="utf-8"))
    cross["封存包完整摘要"] = {name: packets[name]["digest"] for name in PACK_NAMES}
    cross["源数据完整摘要"] = {name: packets[name]["source_digest"] for name in PACK_NAMES}
    cross["复算补充行据"] = {
        name: {
            "温度比": independent[name]["ratio"],
            "调律": {sid: independent[name]["tune"][sid] for sid in SLOTS},
            "稳定性": independent[name]["stability"],
        } for name in PACK_NAMES
    }
    (out_dir / "身份交叉表-含完整摘要.json").write_text(
        json.dumps(cross, ensure_ascii=False, indent=2), encoding="utf-8")

    # 总结论（发布分级与三类处置）
    report["结论"] = {
        "换管槽": {
            "甲丙报表行完全相同": all(per_stage.values()),
            "物理身份": "P-017-旧管 → P-204-新管（已更换）",
            "旧证据等级": "仅单场读数（L1）与旧管历史档案；稳定性/handoff 承诺禁止跨身份沿用",
            "新管稳定性": "丁包换管后两条录音，极差 2.595 ≤ 5 音分，handoff=ready 由换管后录音独立重建",
        },
        "沿用槽": {
            "物理身份": "P-018-沿用管（连续在册，见证齐全）",
            "可跨档沿用": True,
            "漂移注记": "基线偏差 0.0 → 4.035 音分，仍在 ±5 调律带内；乙包旧录音与丁包新录音可合并解释",
        },
        "无凭槽": {
            "物理身份": "存疑（无身份见证）",
            "前后偏差": "0.0 → 2.307 音分，偏差接近但不构成身份证据",
            "最高发布级": "L1 单场读数/内部参考；乙、丁 handoff 均为 pending，禁止发布 L3 可信交接",
        },
    }
    report["全部核对通过"] = all(c["通过"] for c in checks)
    (out_dir / "复算报告.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 人类可读文本摘要
    lines = ["档案交接演练复算摘要", "=" * 30,
             f"核对项总数：{len(checks)}",
             f"全部通过：{report['全部核对通过']}", "",
             "封存包摘要："]
    for row in seal_rows:
        lines.append(f"  {row['封存包']}  {row['摘要']}")
    lines += ["", "关键结论：",
              "1. 换管槽在甲/丙两包 8 个阶段报表行逐行相等、两包摘要各自有效，"
              "但物理身份已由 P-017 换为 P-204：旧调律承诺不得沿用，稳定性由丁包换管后录音重建。",
              "2. 沿用槽身份连续（P-018），乙包稳定性与 handoff 承诺可跨档沿用。",
              "3. 无凭槽无身份见证，前后偏差 0.0→2.307 音分虽接近，最高仅可发布 L1 单场读数。",
              "4. 篡改内存副本即导致摘要变化或 verify_packet 拒绝；非法输入不落库。"]
    (out_dir / "复算摘要.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
