"""模型快照（撤销用）测试：候选字段 dict/FieldCandidate 两种形态都能往返。"""

from scansplit.model import Group


def test_group_snapshot_dict_candidates_roundtrip():
    """OCR 命名后候选以 dict 存入，快照必须能序列化（回归：曾 AttributeError）。"""
    g = Group(start=1, end=2, title="关于开展教学检查的通知", unit="教务处")
    g.title_candidates = [
        {"text": "关于开展教学检查的通知", "confidence": 0.9, "source": "标题", "reason": ""}
    ]
    g.unit_candidates = [
        {"text": "教务处", "confidence": 0.8, "source": "落款", "reason": "位于日期上方"}
    ]
    g.need_confirm = False
    d = g.snapshot()
    assert d["title_candidates"][0]["text"] == "关于开展教学检查的通知"
    g2 = Group.restore(d)
    assert g2.title_candidates[0]["text"] == "关于开展教学检查的通知"
    assert g2.unit_candidates[0]["text"] == "教务处"
    assert g2.need_confirm is False


def test_group_snapshot_fieldcandidate_roundtrip():
    from scansplit.ocrfields import FieldCandidate

    g = Group(start=3, end=3)
    g.title_candidates = [FieldCandidate("重要通知", 0.85, "标题", reason="位于页面上部")]
    g.unit_candidates = [FieldCandidate("校长办公室", 0.77, "文头", reason="红头/页眉区域")]
    g2 = Group.restore(g.snapshot())
    assert g2.title_candidates[0]["text"] == "重要通知"
    assert g2.title_candidates[0]["confidence"] == 0.85
    assert g2.unit_candidates[0]["text"] == "校长办公室"
    assert g2.unit_candidates[0]["source"] == "文头"


def test_group_snapshot_mixed_candidates():
    """同一组内混入 dict 与 FieldCandidate（如人工编辑与 OCR 混合场景）不崩溃。"""
    from scansplit.ocrfields import FieldCandidate

    g = Group(start=1, end=1)
    g.title_candidates = [FieldCandidate("A", 0.9, "标题"), {"text": "B", "confidence": 0.5, "source": "标题"}]
    d = g.snapshot()
    assert [c["text"] for c in d["title_candidates"]] == ["A", "B"]
    g2 = Group.restore(d)
    assert [c["text"] for c in g2.title_candidates] == ["A", "B"]


def test_page_state_split_before_roundtrip_and_compat():
    """v1.7：split_before 快照往返；旧版本快照（无该键）兼容。"""
    from scansplit.model import PageState

    ps = PageState(src_index=3)
    ps.split_before = True
    ps2 = PageState.restore(ps.snapshot())
    assert ps2.split_before is True
    # v1.6 旧快照无 split_before 键 → 默认 False，不崩
    old = {"src_index": 1, "rotation": 90, "blank": None, "separator": False}
    assert PageState.restore(old).split_before is False
