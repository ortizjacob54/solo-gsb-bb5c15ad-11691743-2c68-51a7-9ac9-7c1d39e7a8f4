"""POST /api/v1/stoichiometry/sequence 的 HTTP API 测试（连续反应推演）。

测试反应均选用其它测试文件未提交过的方程式（SO2/SO3 制硫酸、NO 氧化、
H2S 燃烧链、PCl5/PCl3 转化、Na2O2 与水、SiCl4 水解等），避免幂等缓存
造成跨文件的顺序依赖。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PATH = "/api/v1/stoichiometry/sequence"


def _inv(data):
    return {entry["normalized"]: entry["moles"] for entry in data["final_inventory"]}


# ------------------------------------------------------------ 成功路径
def test_two_step_chain_consumes_and_carries_inventory():
    # 2SO2 + O2 -> 2SO3；SO3 + H2O -> H2SO4
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["SO2", "O2"], "products": ["SO3"]},
                {"reactants": ["SO3", "H2O"], "products": ["H2SO4"]},
            ],
            "initial_inventory": [
                {"species": "SO2", "moles": "4"},
                {"species": "O2", "moles": "10"},
                {"species": "H2O", "moles": "3"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["steps"]) == 2
    s1, s2 = data["steps"]
    assert s1["step"] == 1
    assert s1["normalized"]["equation"] == "O2 + 2SO2 -> 2SO3"
    assert s1["reaction_extent"] == {"value": "2", "unit": "mol"}
    assert s1["blocked"] is False
    assert s1["blocking_reactants"] == []
    r1 = {r["normalized"]: r for r in s1["reactants"]}
    assert r1["SO2"]["before"] == "4"
    assert r1["SO2"]["consumed"] == "4"
    assert r1["SO2"]["remaining"] == "0"
    assert r1["O2"]["before"] == "10"
    assert r1["O2"]["consumed"] == "2"
    assert r1["O2"]["remaining"] == "8"
    p1 = {p["normalized"]: p for p in s1["products"]}
    assert p1["SO3"]["before"] == "0"
    assert p1["SO3"]["produced"] == "4"
    assert p1["SO3"]["after"] == "4"

    # 第二步以上一步结束后的库存继续
    assert s2["step"] == 2
    assert s2["normalized"]["equation"] == "H2O + SO3 -> H2SO4"
    assert s2["reaction_extent"]["value"] == "3"  # H2O 限量
    r2 = {r["normalized"]: r for r in s2["reactants"]}
    assert r2["SO3"]["before"] == "4"
    assert r2["SO3"]["consumed"] == "3"
    assert r2["SO3"]["remaining"] == "1"
    assert r2["H2O"]["before"] == "3"
    assert r2["H2O"]["remaining"] == "0"
    p2 = {p["normalized"]: p for p in s2["products"]}
    assert p2["H2SO4"]["before"] == "0"
    assert p2["H2SO4"]["produced"] == "3"
    assert p2["H2SO4"]["after"] == "3"

    assert _inv(data) == {
        "H2O": "0",
        "H2SO4": "3",
        "O2": "8",
        "SO2": "0",
        "SO3": "1",
    }


def test_blocked_step_runs_at_zero_extent_and_lists_all_blockers():
    # 第一步 4NH3 + 5O2 -> 4NO + 6H2O：NH3=4、O2=5 恰好完全反应并产出 NO；
    # 第二步 2NO + O2 -> 2NO2：第一步已把 O2 耗尽，因此该步以零进度继续，
    # O2 为唯一阻塞反应物，第一步产出的 4 mol NO 原样保留。
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["NH3", "O2"], "products": ["NO", "H2O"]},
                {"reactants": ["NO", "O2"], "products": ["NO2"]},
            ],
            "initial_inventory": [
                {"species": "NH3", "moles": "4"},
                {"species": "O2", "moles": "5"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    s1, s2 = data["steps"]
    assert s1["reaction_extent"]["value"] == "1"
    assert s2["reaction_extent"]["value"] == "0"
    assert s2["blocked"] is True
    assert s2["blocking_reactants"] == ["O2"]
    # 零进度：所有消耗/产出均为 0，库存原样
    for r in s2["reactants"]:
        assert r["consumed"] == "0"
        assert r["remaining"] == r["before"]
    for p in s2["products"]:
        assert p["produced"] == "0"
        assert p["after"] == p["before"]
    # NO 仍保留第一步产出的 4 mol
    assert _inv(data) == {"H2O": "6", "NO": "4", "NO2": "0", "NH3": "0", "O2": "0"}


def test_multiple_missing_reactants_are_all_blocking():
    # 库存只有惰性物种，两个反应物都缺失 -> 全部列为阻塞物
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["PCl3", "Cl2"], "products": ["PCl5"]}],
            "initial_inventory": [{"species": "Ar", "moles": "2"}],
        },
    )
    assert resp.status_code == 200
    step = resp.json()["steps"][0]
    assert step["reaction_extent"]["value"] == "0"
    assert step["blocked"] is True
    assert step["blocking_reactants"] == ["Cl2", "PCl3"]
    blockers = {r["normalized"] for r in step["reactants"] if r["is_blocking"]}
    assert blockers == {"Cl2", "PCl3"}
    # 初始库存物种保留；反应物种以 0 出现在最终库存
    assert _inv(resp.json()) == {"Ar": "2", "Cl2": "0", "PCl3": "0", "PCl5": "0"}


def test_zero_stock_reactant_blocks_even_if_others_available():
    # SiCl4 + 2H2O -> SiO2 + 4HCl：SiCl4 库存为 0（但显式给出）
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["SiCl4", "H2O"], "products": ["SiO2", "HCl"]}],
            "initial_inventory": [
                {"species": "SiCl4", "moles": "0"},
                {"species": "H2O", "moles": "10"},
            ],
        },
    )
    step = resp.json()["steps"][0]
    assert step["reaction_extent"]["value"] == "0"
    assert step["blocking_reactants"] == ["SiCl4"]
    h2o = next(r for r in step["reactants"] if r["normalized"] == "H2O")
    assert h2o["is_blocking"] is False
    assert h2o["remaining"] == "10"


def test_equivalent_formulas_merge_into_one_inventory_species():
    # 等价写法（括号风格）在初始库存合并，产物以等价写法给出时也并入同一键
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["H2S", "O2"], "products": ["S", "H2O"]}],
            "initial_inventory": [
                {"species": "H2S", "moles": "1"},
                {"species": "[H]2S", "moles": "1"},   # 等价 H2S
                {"species": "O2", "moles": "0.5"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # 初始库存合并为两条：H2S=2、O2=0.5
    initial = {e["normalized"]: e["moles"] for e in data["initial_inventory"]}
    assert initial == {"H2S": "2", "O2": "0.5"}
    # 2H2S + O2 -> 2S + 2H2O：O2 限量，ξ = 0.5/1 = 0.5
    step = data["steps"][0]
    assert step["reaction_extent"]["value"] == "0.5"
    assert _inv(data) == {"H2O": "1", "H2S": "1", "O2": "0", "S": "1"}


def test_reactant_supplied_in_steps_consumes_from_pooled_stock():
    # 2Na2O2 + 2H2O -> 4NaOH + O2：初始 NaOH 已有库存，产物在其上累加
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["Na2O2", "H2O"], "products": ["NaOH", "O2"]}],
            "initial_inventory": [
                {"species": "Na2O2", "moles": "1"},
                {"species": "H2O", "moles": "10"},
                {"species": "NaOH", "moles": "0.25"},
                {"species": "Ne", "moles": "7"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    step = data["steps"][0]
    assert step["normalized"]["equation"] == "2H2O + 2Na2O2 -> 4NaOH + O2"
    assert step["reaction_extent"]["value"] == "0.5"  # Na2O2 限量
    naoh = next(p for p in step["products"] if p["normalized"] == "NaOH")
    assert naoh["before"] == "0.25"
    assert naoh["produced"] == "2"
    assert naoh["after"] == "2.25"
    # 惰性旁观者 Ne 原样保留
    assert _inv(data) == {
        "H2O": "9",
        "NaOH": "2.25",
        "Na2O2": "0",
        "Ne": "7",
        "O2": "0.5",
    }


def test_nonterminating_extent_uses_exact_rational_arithmetic():
    # 两步独立反应验证精确分数在库存中无损传递：
    # 第一步 PCl5 + H2O -> POCl3 + 2HCl，各 1 mol -> ξ=1；
    # 第二步 3H2 + N2 -> 2NH3，各 1 mol -> H2 限量，ξ=1/3，产出 2/3 NH3。
    # 1/3、2/3 均为无限小数，必须按 28 位有效数字输出且不出现二进制浮点尾巴。
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["PCl5", "H2O"], "products": ["POCl3", "HCl"]},
                {"reactants": ["H2", "N2"], "products": ["NH3"]},
            ],
            "initial_inventory": [
                {"species": "PCl5", "moles": "1"},
                {"species": "H2O", "moles": "1"},
                {"species": "H2", "moles": "1"},
                {"species": "N2", "moles": "1"},
            ],
        },
    )
    assert resp.status_code == 200
    s1, s2 = resp.json()["steps"]
    assert s1["reaction_extent"]["value"] == "1"
    assert s2["reaction_extent"]["value"] == (
        "0.3333333333333333333333333333"  # 1/3，28 位有效数字
    )
    nh3 = next(p for p in s2["products"] if p["normalized"] == "NH3")
    assert nh3["produced"] == "0.6666666666666666666666666667"  # 2/3


def test_three_step_chain_with_middle_blocked_step_continues():
    # 第一步 2H2S + 3O2 -> 2SO2 + 2H2O；第二步 SO2 阻塞反应（缺 H2 不反应）；
    # 第三步 2SO2 + O2 -> 2SO3 继续使用第一步产出的 SO2
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["H2S", "O2"], "products": ["SO2", "H2O"]},
                {"reactants": ["SO2", "H2"], "products": ["S", "H2O"]},
                {"reactants": ["SO2", "O2"], "products": ["SO3"]},
            ],
            "initial_inventory": [
                {"species": "H2S", "moles": "2"},
                {"species": "O2", "moles": "5"},
            ],
        },
    )
    assert resp.status_code == 200
    steps = resp.json()["steps"]
    assert steps[0]["reaction_extent"]["value"] == "1"
    assert steps[1]["reaction_extent"]["value"] == "0"
    assert steps[1]["blocking_reactants"] == ["H2"]
    # 第二步零进度，SO2 库存保持 2；第三步 O2 剩余 2 mol，ξ=1
    assert steps[2]["reaction_extent"]["value"] == "1"
    final = _inv(resp.json())
    assert final["SO2"] == "0"
    assert final["SO3"] == "2"
    assert final["H2"] == "0"
    assert final["O2"] == "1"  # 第一步消耗 3、第三步消耗 1，共 4


def test_step_reaction_ids_match_balance_endpoint():
    balance = client.post(
        "/api/v1/balance",
        json={"reactants": ["O2", "SO2"], "products": ["SO3"]},
    ).json()
    seq = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["SO2", "O2"], "products": ["SO3"]}],
            "initial_inventory": [
                {"species": "SO2", "moles": "2"},
                {"species": "O2", "moles": "2"},
            ],
        },
    ).json()
    assert seq["steps"][0]["reaction_id"] == balance["reaction_id"]
    assert seq["steps"][0]["normalized"]["equation"] == balance["normalized"]["equation"]


def test_results_stable_across_step_and_inventory_order():
    def post(step_order, inv_order):
        return client.post(
            PATH,
            json={
                "steps": [
                    {"reactants": r, "products": p} for r, p in step_order
                ],
                "initial_inventory": [
                    {"species": s, "moles": m} for s, m in inv_order
                ],
            },
        ).json()

    steps_a = [
        (["SO2", "O2"], ["SO3"]),
        (["SO3", "H2O"], ["H2SO4"]),
    ]
    steps_b = [
        (["O2", "SO2"], ["SO3"]),
        (["H2O", "SO3"], ["H2SO4"]),
    ]
    inv_a = [("SO2", "4"), ("H2O", "3"), ("O2", "10")]
    inv_b = [("O2", "10"), ("SO2", "4"), ("H2O", "3")]
    a = post(steps_a, inv_a)
    b = post(steps_b, inv_b)

    assert [s["reaction_id"] for s in a["steps"]] == [
        s["reaction_id"] for s in b["steps"]
    ]
    assert [s["reaction_extent"] for s in a["steps"]] == [
        s["reaction_extent"] for s in b["steps"]
    ]
    assert a["final_inventory"] == b["final_inventory"]


# ------------------------------------------------------------ 结构化错误
def test_parse_error_in_step_is_annotated_with_step_index():
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["H2S", "O2"], "products": ["SO2", "H2O"]},
                {"reactants": ["Zz", "H2"], "products": ["S", "H2O"]},
            ],
            "initial_inventory": [
                {"species": "H2S", "moles": "2"},
                {"species": "O2", "moles": "5"},
                {"species": "H2", "moles": "5"},
            ],
        },
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "FORMULA_PARSE_ERROR"
    assert error["details"]["step"] == 2
    assert error["details"]["step_index"] == 1


def test_non_unique_balance_in_step_reuses_structured_error():
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["PCl3", "Cl2"], "products": ["PCl5"]},
                {"reactants": ["P", "Cl2"], "products": ["PCl3", "PCl5"]},
            ],
            "initial_inventory": [
                {"species": "PCl3", "moles": "1"},
                {"species": "Cl2", "moles": "10"},
                {"species": "P", "moles": "10"},
            ],
        },
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "NON_UNIQUE_BALANCE"
    assert error["details"]["step"] == 2
    assert error["details"]["step_index"] == 1


def test_duplicate_species_in_step_is_conflict_with_step_annotation():
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["PCl3", "PCl3"], "products": ["PCl5"]},
            ],
            "initial_inventory": [{"species": "PCl3", "moles": "1"}],
        },
    )
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "DUPLICATE_SPECIES"
    assert error["details"]["step"] == 1
    assert error["details"]["step_index"] == 0


def test_unparseable_inventory_species_is_parse_error():
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["PCl3", "Cl2"], "products": ["PCl5"]}],
            "initial_inventory": [
                {"species": "Xy", "moles": "1"},
                {"species": "PCl3", "moles": "1"},
            ],
        },
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "FORMULA_PARSE_ERROR"
    assert error["details"]["field"] == "initial_inventory.species"
    assert error["details"]["input"] == "Xy"


@pytest.mark.parametrize("bad", ["-1", "abc", "NaN", "Infinity", "1e10001"])
def test_invalid_inventory_moles_is_invalid_request(bad):
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["PCl3", "Cl2"], "products": ["PCl5"]}],
            "initial_inventory": [
                {"species": "PCl3", "moles": bad},
                {"species": "Cl2", "moles": "1"},
            ],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert body["error"]["details"]["invalid_moles"] == [
        {"species": "PCl3", "moles": bad}
    ]


def test_inventory_moles_must_be_string_not_json_number():
    resp = client.post(
        PATH,
        json={
            "steps": [{"reactants": ["PCl3", "Cl2"], "products": ["PCl5"]}],
            "initial_inventory": [
                {"species": "PCl3", "moles": 0.5},
                {"species": "Cl2", "moles": "1"},
            ],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "payload",
    [
        # 缺 steps
        {"initial_inventory": [{"species": "H2", "moles": "1"}]},
        # 空 steps
        {"steps": [],
         "initial_inventory": [{"species": "H2", "moles": "1"}]},
        # 空 initial_inventory
        {"steps": [{"reactants": ["H2"], "products": ["H2"]}],
         "initial_inventory": []},
    ],
)
def test_request_shape_validation(payload):
    resp = client.post(PATH, json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_first_step_failure_short_circuits_later_steps():
    # 第一步即无法配平：不应执行第二步，也不返回 steps
    resp = client.post(
        PATH,
        json={
            "steps": [
                {"reactants": ["Au"], "products": ["He"]},
                {"reactants": ["H2", "O2"], "products": ["H2O"]},
            ],
            "initial_inventory": [
                {"species": "Au", "moles": "1"},
                {"species": "H2", "moles": "2"},
                {"species": "O2", "moles": "1"},
            ],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "IMPOSSIBLE_BALANCE"
    assert body["error"]["details"]["step"] == 1
    assert "steps" not in body
