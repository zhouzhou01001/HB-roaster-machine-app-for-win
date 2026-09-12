from __future__ import annotations

from typing import Dict, Iterable, List

from .compare import roast_landmarks


def analyze_roast(samples: Iterable[Dict], events: Iterable[Dict], actions: Iterable[Dict]) -> List[str]:
    sample_list, event_list, action_list = list(samples), list(events), list(actions)
    if len(sample_list) < 12:
        return ["样本不足，至少采集 12 个点后再进行曲线分析。"]
    findings: List[str] = []
    bt_ror = [float(row["bt_ror"]) for row in sample_list if row.get("bt_ror") is not None]
    if bt_ror:
        crashes = sum(1 for left, right in zip(bt_ror, bt_ror[1:]) if left - right > 5.0)
        flicks = sum(1 for left, right in zip(bt_ror, bt_ror[1:]) if right - left > 5.0)
        findings.append(f"BT RoR 范围：{min(bt_ror):.1f} 至 {max(bt_ror):.1f} C/min。")
        if crashes:
            findings.append(f"检测到 {crashes} 次较明显的 RoR 下坠，建议结合人工操作记录复盘。")
        if flicks:
            findings.append(f"检测到 {flicks} 次较明显的 RoR 上扬，建议检查末段火力和风门变化。")
    landmarks = roast_landmarks(event_list)
    if landmarks["development_ratio"] is not None:
        findings.append(f"发展阶段：{landmarks['development_s']:.1f}s，占总烘焙时间 {landmarks['development_ratio']:.1f}%。")
    else:
        findings.append("尚未完整记录 FC_START 与 DROP，无法计算发展比例。")
    findings.append(f"已记录 {len(event_list)} 个事件、{len(action_list)} 条人工操作，可在回放和对比窗口中同步查看。")
    return findings
