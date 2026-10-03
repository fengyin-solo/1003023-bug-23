"""门禁管理规则测试：归属授权、只读限制、修复优先、换人留痕、续期幂等。

直接用 FastAPI TestClient 打接口，验证 HTTP 状态码与返回信息。
每个测试用例先重置内存仓库，避免相互污染。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    # 每个用例把内存仓库恢复成种子状态，避免相互污染
    from app.main import app
    from app.store import store

    store.reset()
    return TestClient(app)


WANG = {"X-Operator-Id": "u-wang"}   # 王站管：滨江东信 / 云溪路
CHEN = {"X-Operator-Id": "u-chen"}   # 陈班：云溪路
ZHAO = {"X-Operator-Id": "u-zhao"}   # 赵工：临港工业
LIN = {"X-Operator-Id": "u-lin"}     # 只读


def act(client: TestClient, entry_id: int, action: str, headers: dict, key: str | None = None):
    values: dict = {"action": action}
    if key is not None:
        values["idempotency_key"] = key
    return client.post(f"/api/dooraccess/{entry_id}/actions", json={"values": values}, headers=headers)


# --------------------------------------------------------------- 1. 归属授权
def test_other_site_admin_cannot_touch_record(client: TestClient) -> None:
    """DOOR-0003 属于临港工业基站；王站管管的是滨江东信/云溪路，修复必须打回。"""
    resp = act(client, 3, "修复门禁", WANG)
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "临港工业基站" in detail
    assert "门禁管理员" in detail  # 写明少的是该站点的门禁管理授权


def test_site_admin_can_repair_own_record(client: TestClient) -> None:
    resp = act(client, 3, "修复门禁", ZHAO)
    assert resp.status_code == 200
    assert resp.json()["entry"]["status"] == "已修复"


def test_anonymous_is_treated_as_viewer(client: TestClient) -> None:
    """不带身份头的请求按只读访客处理，三件事一律 403。"""
    resp = act(client, 1, "修复门禁", {})
    assert resp.status_code == 403
    assert "只读" in resp.json()["detail"]


def test_unknown_account_gets_no_permission(client: TestClient) -> None:
    resp = act(client, 1, "记录闯入", {"X-Operator-Id": "u-not-exist"})
    assert resp.status_code == 403


# --------------------------------------------------------------- 2. 只读身份
def test_viewer_can_read_but_cannot_act(client: TestClient) -> None:
    assert client.get("/api/dooraccess/1", headers=LIN).status_code == 200
    resp = act(client, 2, "续期授权", LIN)
    assert resp.status_code == 403


def test_viewer_cannot_edit_detail_fields(client: TestClient) -> None:
    """只读账号点进详情，开门方式不允许改（修复「照样能改」的问题）。"""
    resp = client.patch("/api/dooraccess/1", json={"values": {"开门方式": "人脸"}}, headers=LIN)
    assert resp.status_code == 403
    assert client.get("/api/dooraccess/1").json()["开门方式"] == "指纹+密码"


# --------------------------------------------------------------- 3. 修复优先
def test_repair_wins_over_renewal(client: TestClient) -> None:
    """同一条记录续期与修复先后到达，最终一律按修复算，续期不能改回正常。"""
    entry_id = 2  # 云溪路，授权过期，王站管可管
    renewed = act(client, entry_id, "续期授权", WANG, key="k-1")
    assert renewed.status_code == 200
    repaired = act(client, entry_id, "修复门禁", WANG)
    assert repaired.status_code == 200
    # 修复之后再续期必须被拒
    again = act(client, entry_id, "续期授权", WANG, key="k-2")
    assert again.status_code == 409
    assert "修复优先" in again.json()["detail"]
    final = client.get(f"/api/dooraccess/{entry_id}").json()
    assert final["status"] == "已修复"


def test_renew_refused_when_record_already_repaired(client: TestClient) -> None:
    resp = act(client, 4, "续期授权", WANG, key="k-4")
    assert resp.status_code == 409


# --------------------------------------------------------------- 4. 换人留痕
def test_handover_keeps_site_and_keeps_trace(client: TestClient) -> None:
    """DOOR-0005 属云溪路，王站管把它交给同站点的陈班：站点不变、责任人变更、留痕可查。"""
    resp = client.patch("/api/dooraccess/5", json={"values": {"责任人": "陈班"}}, headers=WANG)
    assert resp.status_code == 200, resp.text
    entry = resp.json()["entry"]
    assert entry["所属站点"] == "云溪路基站"
    assert entry["责任人"] == "陈班"
    traces = entry["history"]
    last = traces[-1]
    assert last["动作"] == "交接责任人"
    assert last["经手人"] == "王站管"
    assert "王站管 → 陈班" in last["说明"]
    # 列表与详情取同一份数据，归属一致
    listed = [r for r in client.get("/api/dooraccess?site=云溪路基站").json()["items"] if r["id"] == 5][0]
    assert listed["责任人"] == "陈班"
    assert listed["所属站点"] == "云溪路基站"


def test_handover_to_other_site_admin_rejected(client: TestClient) -> None:
    """不能把云溪路的记录交给临港的赵工——站点归属不允许漂移。"""
    resp = client.patch("/api/dooraccess/5", json={"values": {"责任人": "赵工"}}, headers=WANG)
    assert resp.status_code == 422
    entry = client.get("/api/dooraccess/5").json()
    assert entry["责任人"] == "陈班"  # 仍是原责任人


def test_site_never_mutable_via_detail(client: TestClient) -> None:
    resp = client.patch("/api/dooraccess/5", json={"values": {"所属站点": "临港工业基站"}}, headers=WANG)
    assert resp.status_code == 422
    assert client.get("/api/dooraccess/5").json()["所属站点"] == "云溪路基站"


def test_other_site_admin_cannot_handover(client: TestClient) -> None:
    resp = client.patch("/api/dooraccess/5", json={"values": {"责任人": "王站管"}}, headers=ZHAO)
    assert resp.status_code == 403


def test_rejected_handover_leaves_no_partial_write(client: TestClient) -> None:
    """合法字段修改与非法跨站点换人一起提交：整体拒绝，不能留下半成功状态。"""
    resp = client.patch(
        "/api/dooraccess/5",
        json={"values": {"开门方式": "虹膜", "责任人": "赵工"}},
        headers=WANG,
    )
    assert resp.status_code == 422
    entry = client.get("/api/dooraccess/5").json()
    assert entry["开门方式"] == "动态密码"
    assert entry["责任人"] == "陈班"
    assert len([trace for trace in entry["history"] if trace["动作"] == "交接责任人"]) == 1


# --------------------------------------------------------------- 5. 续期幂等
def test_duplicate_renewal_writes_once(client: TestClient) -> None:
    """一份授权连点两次（同一幂等键）：授权状态只写一回，续期时间不重写。"""
    entry_id = 2  # 授权过期
    first = act(client, entry_id, "续期授权", WANG, key="dbl-click-key")
    assert first.status_code == 200
    first_time = first.json()["entry"]["最近续期时间"]
    assert first.json()["entry"]["授权状态"] == "已续期"

    second = act(client, entry_id, "续期授权", WANG, key="dbl-click-key")
    assert second.status_code == 200
    assert "重复提交" in second.json()["message"]
    entry = client.get(f"/api/dooraccess/{entry_id}").json()
    assert entry["最近续期时间"] == first_time
    renew_traces = [t for t in entry["history"] if t["动作"] == "续期授权"]
    # 种子里没有对 id=2 的新续期（仅有 3 月一次历史续期），本次去重后应只多一条
    assert len(renew_traces) == 2
    assert entry["renewal_keys"] == {"dbl-click-key": first_time}


def test_renew_requires_expired_status(client: TestClient) -> None:
    """状态正常的记录不能续期，避免无意义重复写。"""
    resp = act(client, 1, "续期授权", WANG, key="k-x")
    assert resp.status_code == 409
    assert "无需重复续期" in resp.json()["detail"]


def test_renew_without_key_still_state_guarded(client: TestClient) -> None:
    """即便没有幂等键，状态门禁也保证第二次续期不会再写。"""
    first = act(client, 2, "续期授权", WANG)
    assert first.status_code == 200
    second = act(client, 2, "续期授权", WANG)
    assert second.status_code == 409


# --------------------------------------------------------------- 6. 闯入与细节
def test_mark_intrusion_then_repair(client: TestClient) -> None:
    intrude = act(client, 1, "记录闯入", WANG)
    assert intrude.status_code == 200
    assert intrude.json()["entry"]["status"] == "非法闯入"
    repaired = act(client, 1, "修复门禁", WANG)
    assert repaired.json()["entry"]["status"] == "已修复"
    # 已修复的不能重复登记闯入翻案
    again = act(client, 1, "记录闯入", WANG)
    assert again.status_code == 409


def test_duplicate_intrusion_rejected(client: TestClient) -> None:
    resp = act(client, 3, "记录闯入", ZHAO)
    assert resp.status_code == 409


def test_unknown_action_rejected(client: TestClient) -> None:
    resp = act(client, 1, "删除门禁", WANG)
    assert resp.status_code == 400


def test_authorization_status_not_directly_editable(client: TestClient) -> None:
    """授权状态只能由续期动作流转，详情里直接改一律拒绝。"""
    resp = client.patch("/api/dooraccess/2", json={"values": {"授权状态": "有效"}}, headers=WANG)
    assert resp.status_code == 422


def test_detail_edits_leave_trace_with_operator(client: TestClient) -> None:
    resp = client.patch("/api/dooraccess/1", json={"values": {"开门方式": "人脸+指纹"}}, headers=WANG)
    assert resp.status_code == 200
    last = resp.json()["entry"]["history"][-1]
    assert last["动作"] == "修改详情"
    assert last["经手人"] == "王站管"


def test_session_endpoint_lists_operators(client: TestClient) -> None:
    data = client.get("/api/session", headers=LIN).json()
    assert data["current"]["name"] == "林只读"
    assert {op["id"] for op in data["operators"]} == {"u-wang", "u-chen", "u-zhao", "u-lin"}


# --------------------------------------------------------------- 7. 并发对撞
def test_concurrent_renew_vs_repair_always_ends_repaired(client: TestClient) -> None:
    """续期与修复同时到达，无论谁先拿到锁，终态必须是已修复。"""
    import threading

    outcomes: list[tuple[int, str]] = []

    def do_renew() -> None:
        r = act(client, 2, "续期授权", WANG, key="race-renew")
        outcomes.append((r.status_code, "续期"))

    def do_repair() -> None:
        r = act(client, 2, "修复门禁", WANG)
        outcomes.append((r.status_code, "修复"))

    t1 = threading.Thread(target=do_renew)
    t2 = threading.Thread(target=do_repair)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert client.get("/api/dooraccess/2").json()["status"] == "已修复"
    # 修复一定成功；续期要么先落（200），要么被修复压掉（409），但绝不允许把终态改回去
    repair_codes = [code for code, name in outcomes if name == "修复"]
    assert repair_codes == [200]
    renew_codes = [code for code, name in outcomes if name == "续期"]
    assert renew_codes[0] in (200, 409)


def test_concurrent_duplicate_renewal_writes_once(client: TestClient) -> None:
    """同一幂等键的续期并发提交，只有一份真正写入。"""
    import threading

    statuses: list[int] = []
    barrier = threading.Barrier(2)

    def do_renew() -> None:
        barrier.wait()  # 尽量让两个请求同一时刻发出
        statuses.append(act(client, 2, "续期授权", WANG, key="same-key").status_code)

    threads = [threading.Thread(target=do_renew) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entry = client.get("/api/dooraccess/2").json()
    assert list(entry["renewal_keys"]) == ["same-key"]
    assert len([trace for trace in entry["history"] if trace["动作"] == "续期授权"]) == 2
    assert sorted(statuses) == [200, 200]  # 第二份走幂等返回，仍是 200 但不再写入
