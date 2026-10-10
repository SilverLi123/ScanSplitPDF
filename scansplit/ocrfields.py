"""从 OCR 行提取文档标题与发文单位（几何特征 + 公文格式启发式）。

所有候选均来自 OCR 实际识别文字；识别不到就返回空并标记"待确认"，绝不编造。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ocr import OcrLine

# 常见公文标题格式
TITLE_PATTERNS = [
    re.compile(r"^关于.+的(通知|通报|报告|请示|批复|函|意见|方案|计划|总结|规定|办法|细则|公告|通告|决定|命令|纪要|安排)$"),
    re.compile(r"^\d{4}年(度)?.{0,30}(通知|通报|报告|方案|计划|总结|规定|办法|公告|安排)$"),
    re.compile(r"^第.+[次届].+会议(纪要|决议)$"),
    re.compile(r"^.+工作(报告|方案|计划|总结|要点|安排)$"),
    re.compile(r"^.{2,40}(申报书|登记表|审批表|备案表|统计表|汇总表|一览表|明细表|清单)$"),
    re.compile(r"^.{2,40}(协议|合同|承诺书|责任书|申请书|证明|说明)$"),
]

UNIT_SUFFIX = (
    "学院|大学|学校|委员会|办公室|管理处|处|部|局|厅|中心|医院|公司|集团"
    "|研究院|研究所|总队|支队|分局|支行|协会|基金会|居委会|村委会|镇政府"
    "|乡政府|县政府|区政府|市政府|省政府|管委会|指挥部|领导小组|教研室"
    "|实验室|工作站|基地|馆|站|所|科|室|组"
)
UNIT_PATTERN = re.compile(rf"^[^\s，。；：、]{{2,30}}({UNIT_SUFFIX})$")
# 红头格式："XX大学文件" / "XX学院通知"
HEADER_PATTERN = re.compile(
    rf"^([^\s，。；：、]{{2,30}}(?:{UNIT_SUFFIX}))(?:文件|通知|函|简报|红头|便函)$"
)
# 正文句中单位名（如"经教务处、学生处协商"）。
# 泛化后缀（科/室/组/实验室/教研室等）在句中太容易误报，仅保留明确的机构后缀。
BODY_SUFFIX = (
    "学院|大学|学校|委员会|办公室|管理处|处|部|局|厅|中心|医院|公司|集团"
    "|研究院|研究所|总队|支队|分局|支行|协会|基金会|居委会|村委会|政府"
    "|管委会|指挥部|领导小组"
)
BODY_UNIT_RE = re.compile(rf"[^\s，。；：、]{{2,20}}(?:{BODY_SUFFIX})")
LEAD_PARTICLES = ("经", "由", "对", "向", "与", "和", "及", "请", "会同", "联合")
# 常见场所名词（虽以"室/处"结尾但通常不是发文单位）
PLACE_STOPWORDS = {
    "会议室", "教室", "档案室", "收发室", "值班室", "接待室", "休息室",
    "储藏室", "盥洗室", "阅览室", "打印室", "复印室", "机房", "库房",
}

DATE_PATTERN = re.compile(r"^[\d〇○零一二三四五六七八九十]{4}年[\d〇○零一二三四五六七八九十]{1,2}月[\d〇○零一二三四五六七八九十]{1,3}日?$")
PAGE_NO_PATTERN = re.compile(r"^[-—\s]*\d{1,4}[-—\s]*$")
RECIPIENT_PATTERN = re.compile(r"(主送|抄送|致|呈|报：|送：)|^各.{0,20}[：:]$")
# 表格/表单里的明确单位标注行："填报单位：教务处"、"部门：教务办公室"。
# 这类行含"："无法被 UNIT_PATTERN 整行匹配，但比落款几何推断更可靠。
UNIT_LABELS = (
    "填报单位|填表单位|制表单位|编制单位|经办单位|报送单位|申报单位|负责单位"
    "|统计单位|承办单位|拟稿单位|呈报单位|上报单位|部门|部内|单位名称"
)
LABELED_UNIT_RE = re.compile(
    rf"^({UNIT_LABELS})[：:]\s*([^\s，。；：、]{{2,30}})$"
)


@dataclass
class FieldCandidate:
    text: str
    confidence: float
    source: str  # 文头 | 落款 | 正文 | 标题
    reason: str = ""

    def to_dict(self) -> dict:
        return {"text": self.text, "confidence": round(self.confidence, 3),
                "source": self.source, "reason": self.reason}

    @staticmethod
    def from_dict(d: dict) -> "FieldCandidate":
        return FieldCandidate(d.get("text", ""), d.get("confidence", 0.0),
                              d.get("source", ""), d.get("reason", ""))


def _strip_leading_particles(s: str) -> str:
    """去掉单位名前的常见虚词（"经教务处" → "教务处"）。"""
    for p in LEAD_PARTICLES:
        if s.startswith(p) and len(s) > len(p):
            s = s[len(p):]
            break
    return s


def _is_date_or_pageno(line: OcrLine) -> bool:
    t = line.text.strip()
    return bool(DATE_PATTERN.match(t) or PAGE_NO_PATTERN.match(t))


def _title_pattern_score(text: str) -> float:
    for pat in TITLE_PATTERNS:
        if pat.match(text.strip()):
            return 1.0
    return 0.25


def _title_line_score(line: OcrLine, img_w: float, img_h: float,
                      max_h: float) -> float:
    """标题行得分：字号、居中、位置、格式、OCR 置信度。

    字号按页面高度的绝对比例评估（标题通常 ≥4% 页高），
    避免"全页无大字号时正文行被归一化成标题字号"。
    """
    if _is_date_or_pageno(line):
        return 0.0
    text = line.text.strip()
    h_score = min(line.height / max(img_h * 0.04, 1.0), 1.0)
    center_score = 1.0 - min(abs(line.cx - img_w / 2.0) / (img_w / 2.0), 1.0)
    pos_score = 1.0 if line.cy < img_h * 0.45 else 0.4
    fmt = _title_pattern_score(text)
    if re.match(r"^[一二三四五六七八九十\d]+[、.．）)]", text):
        fmt *= 0.3  # 序号行不是标题
    n = len(text)
    len_score = 1.0 if 4 <= n <= 50 else (0.5 if 2 <= n < 4 else 0.2)
    return (0.35 * h_score + 0.25 * center_score + 0.15 * pos_score
            + 0.15 * fmt + 0.10 * len_score) * (0.6 + 0.4 * line.score)


def _join_multiline_title(lines: list[OcrLine], img_w: float) -> str:
    """相邻、字号相近、水平居中对齐的行拼接为多行标题。"""
    joined: list[str] = []
    for i, line in enumerate(lines):
        joined.append(line.text)
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            gap = nxt.y0 - line.y1
            same_h = abs(nxt.height - line.height) <= max(line.height, nxt.height) * 0.35
            aligned = abs(nxt.cx - line.cx) <= img_w * 0.15
            if gap > max(line.height, nxt.height) * 1.2 or not (same_h and aligned):
                break
    return "".join(joined)


def extract_title(lines: list[OcrLine], img_w: float, img_h: float) -> list[FieldCandidate]:
    """标题候选：页面上方区域中标题特征最强的行（支持多行拼接）。"""
    if not lines:
        return []
    max_h = max(l.height for l in lines)
    scored = sorted(
        lines,
        key=lambda l: _title_line_score(l, img_w, img_h, max_h),
        reverse=True,
    )
    out: list[FieldCandidate] = []
    seen: set[str] = set()
    for line in scored[:8]:
        score = _title_line_score(line, img_w, img_h, max_h)
        if score < 0.25:
            continue
        # 多行拼接：找与它相邻的同行特征行
        neighbors = [l for l in lines if l is not line
                     and abs(l.cy - line.cy) <= max(line.height, l.height) * 2.0
                     and abs(l.cx - line.cx) <= img_w * 0.15
                     and abs(l.height - line.height) <= max(line.height, l.height) * 0.35]
        merged = line.text
        if neighbors:
            below = [l for l in neighbors if l.y0 >= line.y1]
            if below:
                merged = line.text + min(below, key=lambda l: l.y0).text
        if merged in seen:
            continue
        seen.add(merged)
        conf = min(0.98, max(score, line.score * 0.9))
        out.append(FieldCandidate(
            text=merged,
            confidence=round(conf, 3),
            source="标题",
            reason=f"字号 {line.height:.0f}px、居中偏差 {abs(line.cx - img_w/2):.0f}px、位于页面上部",
        ))
        if len(out) >= 5:
            break
    return out


def _unit_score_last_page(line: OcrLine, img_w: float, img_h: float) -> float | None:
    """末页落款区单位名得分；不合适返回 None。"""
    text = line.text.strip()
    if not text or _is_date_or_pageno(line):
        return None
    if not UNIT_PATTERN.match(text):
        return None
    if RECIPIENT_PATTERN.search(text):
        return None  # 收文单位/称呼行
    if text in PLACE_STOPWORDS:
        return None
    if line.cy < img_h * 0.65:
        return None
    rightness = min(line.cx / img_w, 1.0)
    return line.score * (0.6 + 0.4 * rightness)


def extract_unit_first_page(lines: list[OcrLine], img_w: float, img_h: float) -> list[FieldCandidate]:
    """首页单位候选：文头（红头/页眉）优先，正文提及降权。

    红头格式如"XX大学文件"；正文提及如"经教务处、学生处协商"。
    """
    out: list[FieldCandidate] = []
    for line in lines:
        text = line.text.strip()
        if not text or _is_date_or_pageno(line):
            continue
        if RECIPIENT_PATTERN.search(text):
            continue  # 收文单位/称呼行
        if text in PLACE_STOPWORDS:
            continue
        m = LABELED_UNIT_RE.match(text)
        if m and UNIT_PATTERN.match(m.group(2)) and m.group(2) not in PLACE_STOPWORDS:
            out.append(FieldCandidate(m.group(2), round(line.score * 0.95, 3), "文头",
                                      reason="表格填报单位标注"))
            continue
        if line.cy <= img_h * 0.12:
            m = HEADER_PATTERN.match(text)
            if m:
                out.append(FieldCandidate(m.group(1), round(line.score * 0.95, 3), "文头",
                                          reason="红头/页眉区域"))
                continue
            if UNIT_PATTERN.match(text):
                out.append(FieldCandidate(text, round(line.score * 0.9, 3), "文头",
                                          reason="页眉区域"))
                continue
        for m in BODY_UNIT_RE.findall(text):
            cand = _strip_leading_particles(m)
            if cand and len(cand) >= 2:
                out.append(FieldCandidate(cand, round(line.score * 0.6, 3), "正文",
                                          reason="首页正文提及"))
    out.sort(key=lambda c: c.confidence, reverse=True)
    return out[:6]


def extract_unit_last_page(lines: list[OcrLine], img_w: float, img_h: float) -> list[FieldCandidate]:
    """末页单位候选：落款优先（日期行上方的单位名）。"""
    dates = [l for l in lines if DATE_PATTERN.match(l.text.strip())]
    out: list[FieldCandidate] = []
    for line in lines:
        text = line.text.strip()
        if not text or _is_date_or_pageno(line):
            continue
        above_date = any(line.y1 <= d.y0 + 5 for d in dates)
        m = LABELED_UNIT_RE.match(text)
        if m and line.cy >= img_h * 0.3:
            val = m.group(2)
            if UNIT_PATTERN.match(val) and val not in PLACE_STOPWORDS:
                conf = line.score * (1.15 if above_date else 1.05)
                out.append(FieldCandidate(
                    val, round(min(conf, 0.99), 3), "落款",
                    reason="填报单位标注" + ("（位于日期上方）" if above_date else ""),
                ))
            continue
        s = _unit_score_last_page(line, img_w, img_h)
        if s is None:
            continue
        if not above_date and (line.cy < img_h * 0.8 or min(line.cx / img_w, 1.0) < 0.5):
            # 无日期锚定时只认右下角，防表格末页的单元格（如"使用单位"列值）误当落款
            continue
        conf = s * (1.15 if above_date else 1.0)
        out.append(FieldCandidate(
            line.text, round(min(conf, 0.99), 3), "落款",
            reason="位于日期上方" if above_date else "页面下方区域",
        ))
    out.sort(key=lambda c: c.confidence, reverse=True)
    return out[:6]


def merge_unit_candidates(first: list[FieldCandidate], last: list[FieldCandidate],
                          seal: list[FieldCandidate] | None = None) -> list[FieldCandidate]:
    """合并：落款 > 文头 > 印章 > 正文；同文本取高分并保留来源标注。"""
    merged: dict[str, FieldCandidate] = {}
    order = {"落款": 0, "文头": 1, "印章": 2, "正文": 3}
    for c in list(last) + list(first) + list(seal or []):
        cur = merged.get(c.text)
        if cur is None or order.get(c.source, 9) < order.get(cur.source, 9):
            merged[c.text] = c
        elif cur.source == c.source and c.confidence > cur.confidence:
            merged[c.text] = c
    out = sorted(merged.values(), key=lambda c: c.confidence, reverse=True)
    return out[:8]


def direction_quality(lines_by_angle: dict[int, list[OcrLine]]) -> dict[int, dict]:
    """各角度识别质量：行数 + 平均置信度。用于横向表格方向辅助判断。"""
    out = {}
    for angle, lines in lines_by_angle.items():
        n = len(lines)
        avg = sum(l.score for l in lines) / n if n else 0.0
        out[angle] = {
            "n_lines": n,
            "avg_score": round(avg, 3),
            "quality": round(avg * min(n, 20) / 20.0, 3),
        }
    return out


def line_orientation(lines: list[OcrLine]) -> tuple[str, float]:
    """按检测框长宽比统计文字行方向。

    返回 (类别, 多数占比)。类别：horizontal / vertical / mixed / none。
    det 框的四点序是纯几何的（与文字是否倒置无关），因此只能区分
    "内容正放（横行为主）"与"内容侧放（竖行为主）"，不能区分 0/180
    或 90/270——后者需交给禁用 cls 的 OCR 质量对比（见 autoname）。
    """
    if not lines:
        return "none", 0.0
    n_v = sum(1 for l in lines if l.height > l.width)
    frac = n_v / len(lines)
    if frac >= 0.6:
        return "vertical", frac
    if frac <= 0.4:
        return "horizontal", 1.0 - frac
    return "mixed", max(frac, 1.0 - frac)


def best_direction(qualities: dict[int, dict]) -> tuple[int | None, float]:
    """最高质量方向；若各方向都无文字则返回 None。"""
    best_angle, best = None, None
    for angle, q in qualities.items():
        if q["n_lines"] == 0:
            continue
        if best is None or q["quality"] > best:
            best_angle, best = angle, q["quality"]
    return best_angle, (best if best is not None else 0.0)


def title_likeness(lines: list[OcrLine], img_w: float, img_h: float) -> float:
    """页面"像文档首页"的程度（用于拆分边界双重验证）。"""
    if not lines:
        return 0.0
    max_h = max(l.height for l in lines)
    best = max(_title_line_score(l, img_w, img_h, max_h) for l in lines)
    return round(best, 3)
