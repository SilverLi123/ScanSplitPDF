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
    "|研究院|研究所|科学院|设计院|院"
    "|总队|支队|分局|支行|协会|基金会|居委会|村委会|镇政府"
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
    """末页单位候选：落款优先（**紧邻**日期行上方的单位名）。

    v1.9-nightly 修复（P1，最小改动）：旧逻辑 `above_date = 任意行 y1 <= 日期 y0`
    只要"在日期上方**任意**距离"就算落款锚定，导致**表格中间的单元格**（如承办部门
    列的"设备处"，位于页面中部）被当成落款，压过真正的紧邻落款（"人事处"直接位于
    日期上一行）。改为按"与日期的垂直距离"分级：真正落款是**离日期最近**的单位行。
    """
    dates = [l for l in lines if DATE_PATTERN.match(l.text.strip())]

    def _gap(line: OcrLine) -> float | None:
        """该行到其**下方最近日期**的垂直间距；无日期在其下方则 None（=非日期锚定）。"""
        below = [d for d in dates if line.y1 <= d.y0 + 5]
        if not below:
            return None
        return min(d.y0 - line.y1 for d in below)

    # 日期锚定行的"紧邻"门槛：单位名与日期通常相隔不超过约 1 行高（~0.06 页高）。
    near_gap = img_h * 0.06
    out: list[FieldCandidate] = []
    for line in lines:
        text = line.text.strip()
        if not text or _is_date_or_pageno(line):
            continue
        gap = _gap(line)
        above_date = gap is not None            # 在某个日期上方（任意距离）
        near_date = above_date and gap <= near_gap  # 紧邻日期上方 = 真落款
        m = LABELED_UNIT_RE.match(text)
        if m and line.cy >= img_h * 0.3:
            val = m.group(2)
            if UNIT_PATTERN.match(val) and val not in PLACE_STOPWORDS:
                # 明确标注行（"填报单位：X"）本身就强，紧邻日期再加权
                conf = line.score * (1.15 if near_date else (1.05 if above_date else 1.0))
                out.append(FieldCandidate(
                    val, round(min(conf, 0.99), 3), "落款",
                    reason="填报单位标注" + ("（紧邻日期上方）" if near_date
                                          else ("（日期上方）" if above_date else "")),
                ))
            continue
        s = _unit_score_last_page(line, img_w, img_h)
        if s is None:
            continue
        if near_date:
            # 紧邻日期上方：最强落款信号
            conf = s * 1.15
            reason = "紧邻日期上方"
        elif above_date:
            # 在日期上方但不紧邻（多为表格中部单元格）：显著降权，且须在页面下半部右侧
            if line.cy < img_h * 0.72 or min(line.cx / img_w, 1.0) < 0.45:
                continue
            conf = s * 0.75
            reason = "日期上方较远（疑似表格单元格，降权）"
        elif line.cy < img_h * 0.8 or min(line.cx / img_w, 1.0) < 0.5:
            # 无日期锚定时只认右下角，防表格末页的单元格（如"使用单位"列值）误当落款
            continue
        else:
            conf = s * 1.0
            reason = "页面下方区域"
        out.append(FieldCandidate(
            line.text, round(min(conf, 0.99), 3), "落款", reason=reason,
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
    """各角度识别质量（v1.9 A1）。

    目标：在四方向 OCR 结果中选出**真正被正确回正**的角度，而不是行数多或有
    几行高分的方向。

    旧公式 `avg * min(n,20)/20` 只看行数与平均分，无法区分"真文本"与"竖条碎片
    误读"，导致 90°/180° 系统性混淆（C 样本 p19/41/45/59 真实 90° 被判 180°）。

    实测反复暴露的关键事实（决定本公式形态）：
      - **正确方向会读到真实的标题 / 表头等"实质文本行"**——长、宽、高置信度；
      - **错误方向只能读到零散短碎片**（表格被逐列纵读，全是 1~2 字单元格值）。
      因此最强判别信号不是"行数"或"平均分"（两者在伪方向上都会虚高），而是
      **是否存在实质文本行**（长行 + 高置信度 + 主要连成横向）。

    公式：
      quality = avg * line_count_factor * orientation_factor * substance_factor

      - line_count_factor = min(n,20)/20（保留原行数奖励，设上限防爆行刷分）；
      - orientation_factor = 0.25 + 0.75 * horizontal_frac：横排行（宽≥高）占比。
        正常文档正放时绝大多数行为横向；竖条误读时占比骤降，被压低。这是区分
        "真横排" 与 "竖排/纵读伪方向" 的几何先验。
      - substance_factor = 0.45 + 0.55 * substance：**实质文本行占比**。
        实质行 = 文本 ≥4 字符 且 置信度 ≥0.6（真实的题名、表头、正文句）。
        仅当某方向能读出成规模实质行时才给满权；纯碎片方向被压到 0.45。
        **这是区分"正确方向（有真实标题）"与"伪方向（只有短碎片）"的关键**。
        相比此前用"极短行惩罚"，改为"实质行奖励"更稳健：表格本身就有大量短
        单元格值，惩罚短行会误伤**正确**的表格方向（stress81 曾因此 6 页翻转）。

    quality 落在 [0,1]；同时输出各分项与 raw_quality 便于诊断。
    """
    out: dict[int, dict] = {}
    for angle, lines in lines_by_angle.items():
        n = len(lines)
        avg = sum(l.score for l in lines) / n if n else 0.0
        raw = avg * min(n, 20) / 20.0
        quality = 0.0
        horiz = 0.0
        substance = 0.0
        orient_factor = 0.0
        if n:
            # 横排行占比（宽≥高）：正放文字几乎全是横向检测框
            n_h = sum(1 for l in lines if l.width >= l.height)
            horiz = n_h / n
            orient_factor = 0.25 + 0.75 * horiz
            # 实质文本行占比：≥4 字符且高置信度（题名/表头/正文句）
            n_sub = sum(
                1 for l in lines
                if len(l.text.strip()) >= 4 and l.score >= 0.6
            )
            substance = n_sub / n
            substance_factor = 0.45 + 0.55 * substance
            quality = raw * orient_factor * substance_factor
        out[angle] = {
            "n_lines": n,
            "avg_score": round(avg, 3),
            "raw_quality": round(raw, 3),
            "horizontal_frac": round(horiz, 3),
            "orientation_factor": round(orient_factor, 3),
            "substance_frac": round(substance, 3),
            "quality": round(quality, 3),
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
    """最高质量方向；若各方向都无文字则返回 None。

    v1.9 A1：加入**几何行向先验**作为平局裁决。四方向 quality 相同或接近时，
    优先选择"检测框行向与页面一致"的角度（正放内容横行为主），避免 90°/180°
    因质量接近而随机选错。
    """
    usable = {a: q for a, q in qualities.items() if q.get("n_lines", 0) > 0}
    if not usable:
        return None, 0.0
    # 主排序：quality；平局用"横排行占比"（越大越像正放）
    def _key(item):
        a, q = item
        return (q["quality"], q.get("horizontal_frac", 0.0))
    best_angle, best_q = max(usable.items(), key=_key)
    return best_angle, best_q["quality"]


def direction_confidence(qualities: dict[int, dict]) -> float:
    """方向判定置信度（v1.9 A1）：最佳与次佳质量差距 + 绝对质量。

    返回 [0,1]。用于 UI 提示"方向是否可信"，而非仅给一个角度。
    - 无可用方向 → 0.0；
    - 仅一个方向有文字 → 较高置信度（但设上限，避免单信号过度自信）；
    - 多方向竞争：按 quality 差距归一化。
    """
    usable = sorted(
        (q["quality"] for q in qualities.values() if q.get("n_lines", 0) > 0),
        reverse=True,
    )
    if not usable:
        return 0.0
    if len(usable) == 1:
        return round(min(0.6 + usable[0] * 0.4, 0.95), 3)
    top, second = usable[0], usable[1]
    gap = top - second
    margin = gap / top if top > 0 else 0.0
    return round(min(0.5 + 0.5 * margin, 0.99) * (0.7 + 0.3 * min(top, 1.0)), 3)


def title_likeness(lines: list[OcrLine], img_w: float, img_h: float) -> float:
    """页面"像文档首页"的程度（用于拆分边界双重验证）。"""
    if not lines:
        return 0.0
    max_h = max(l.height for l in lines)
    best = max(_title_line_score(l, img_w, img_h, max_h) for l in lines)
    return round(best, 3)


# -- 文档首页结构特征（v1.9 阶段 A0，用于拆分上下文判定） ---------------------- #

# 序号小节标题（"一、相关情况说明"/"2. 检查内容"）——文档中段的强特征。
SECTION_HEAD_RE = re.compile(r"^[一二三四五六七八九十\d]+[、.．）)]")
# 明确的"续"标记（续表 / 续页 / （续）/（续表）/(续页)）——内部继续的强特征。
# 只认**独立**的"续"标记：需紧邻"表/页"或位于括号内，绝不匹配正文中的"后续/继续"等。
# v1.9-nightly 扩展：补入**括号形式的附件/附表续接标记**（（统计附表）/（附表）/（附页）/（附件））。
# 依据：这类页面是**同一份文档的表尾续接页**（如 G 套 p30/93），虽重印居中大字标题，
# 却是内部继续，不应被当作新文档起点；其标题是"前页标题 +（统计附表）"的续接形式。
# 仅匹配**括号内**的附X，绝不匹配正文里"附件：xxx"等普通提及。
CONTINUATION_RE = re.compile(
    r"续\s*[表页]|[（(]\s*续\s*[）)]|第\s*\d+\s*页\s*之?\s*续|[（(]\s*(?:统计|数据)?\s*附\s*[表页件]\s*[）)]")
# 主送机关/称谓行（"各有关单位："、"各学院、各部门："）——公文首页正文起始特征。
RECIPIENT_LINE_RE = re.compile(r"^各.{0,16}[：:]$")
# 文头（发文机关标识，如"XX大学文件"、"后勤保障处文件"）。
# 真文头是**纯机关名**（可带 文件/函/简报/通知 后缀），不含阿拉伯数字/年份/书名号，
# 也不含标题动词（关于/的/报告/方案等）。据此排除"2026年度实验室"这类含数字的行。
_HEADER_LINE_RE = re.compile(
    rf"^(?![^\s]{{0,6}}[\d〇○])[^\s，。；：、]{{2,20}}(?:{UNIT_SUFFIX})(?:文件|函|简报|通知)?$")
_TITLE_VERB_RE = re.compile(r"关于|的通知|的报告|的方案|的请示|的批复|工作总结|实施方案")


def first_page_structure(lines: list[OcrLine], img_w: float, img_h: float) -> dict:
    """判断"这一页的顶部结构是否像一份文档的首面"。

    只做**结构**判断（不依赖具体文字内容），输出可解释的信号供上下文判定组合：
      - has_title   ：顶部带（y<0.32h）存在"大字号 + 居中"的标题行（排除序号行/日期/页码）；
      - title_is_max：该标题行是否为全页最大字号行（首页标题通常最大）；
      - has_header  ：标题之上存在文头（发文机关标识）；
      - has_recipient：标题之后紧跟主送机关/称谓行（"各XX："）；
      - has_section ：顶部带存在序号小节标题（"一、"/"2."）——续页常见；
      - has_continuation：存在"续表/续页/（续）"标记——内部继续的强特征；
      - title_text  ：标题行文字（便于人工核对）。

    返回 dict；lines 为空时各布尔量为 False。
    """
    out = {
        "has_title": False, "title_is_max": False, "has_header": False,
        "has_recipient": False, "has_section": False, "has_continuation": False,
        "title_text": "",
    }
    if not lines:
        return out
    hs = [l.height for l in lines]
    med_h = sorted(hs)[len(hs) // 2]
    max_h = max(hs)
    top = sorted([l for l in lines if l.y0 < img_h * 0.32], key=lambda l: l.y0)

    # 字号门槛：相对页高的**绝对**底线（标题通常 ≥2% 页高），
    # 仅在页内行数足够（正文占多数）时再叠加"显著大于中位字号"的相对约束。
    # 行数很少（如首页仅标题+一行正文）时中位数会被标题拉高，此时不加相对门槛，
    # 否则真标题反而过不了自己的阈值（实测 doc2_p1 首页即为此类）。
    abs_floor = img_h * 0.02
    if len(lines) >= 5:
        h_floor = max(med_h * 1.25, abs_floor)
    else:
        h_floor = abs_floor
    cand = [l for l in top
            if l.height >= h_floor
            and abs(l.cx - img_w / 2) < img_w * 0.22
            and not _is_date_or_pageno(l)
            and not SECTION_HEAD_RE.match(l.text.strip())]
    # 文头（"XX处文件"）与标题常同排；文头**本身不等于标题**（续页也会重印文头）。
    # 只在存在"非文头"的标题候选时才认标题，避免把纯文头页误判为文档首面。
    def _is_header(l: OcrLine) -> bool:
        t = l.text.strip()
        return bool(_HEADER_LINE_RE.match(t)) and not _TITLE_VERB_RE.search(t)

    non_header = [l for l in cand if not _is_header(l)]
    titled = [l for l in non_header if _title_pattern_score(l.text.strip()) >= 1.0]
    pool = titled or non_header
    title = max(pool, key=lambda l: l.height) if pool else None
    if title is not None:
        out["has_title"] = True
        # 多行标题（如"2026年度实验室"+"安全检查报告"）：取同字号、居中、相邻的行拼接
        group = [l for l in pool
                 if abs(l.height - title.height) <= max(title.height, 1.0) * 0.35
                 and abs(l.cx - img_w / 2) < img_w * 0.22
                 and abs(l.y0 - title.y0) < img_h * 0.12]
        group.sort(key=lambda l: l.y0)
        out["title_text"] = "".join(l.text.strip() for l in group) or title.text.strip()
        out["title_is_max"] = title.height >= max_h - 1.0
    out["has_header"] = any(_is_header(l) for l in top)
    out["has_recipient"] = any(RECIPIENT_LINE_RE.match(l.text.strip()) for l in top)
    out["has_section"] = any(SECTION_HEAD_RE.match(l.text.strip()) for l in top)
    out["has_continuation"] = any(CONTINUATION_RE.search(l.text.strip()) for l in top)
    return out

