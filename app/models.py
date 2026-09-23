"""Pydantic 请求/响应模型（同时驱动 OpenAPI 文档）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ReactionRequest(BaseModel):
    reactants: list[str] = Field(
        ...,
        min_length=1,
        description="反应物化学式，如 [\"KMnO4\", \"HCl\"]；电荷写作 SO4^2-、Fe3+、e-",
        examples=[["H2", "O2"]],
    )
    products: list[str] = Field(
        ...,
        min_length=1,
        description="生成物化学式，如 [\"KCl\", \"MnCl2\", \"Cl2\", \"H2O\"]",
        examples=[["H2O"]],
    )

    @field_validator("reactants", "products")
    @classmethod
    def _strip(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError("化学式必须是字符串")
            stripped = value.strip()
            if not stripped:
                raise ValueError("化学式不能为空字符串")
            cleaned.append(stripped)
        return cleaned


class SpeciesCoefficient(BaseModel):
    input: str = Field(description="客户端提交的原始化学式")
    normalized: str = Field(description="规范化后的化学式")
    coefficient: int = Field(description="最小互质正整数系数")


class CoefficientBlock(BaseModel):
    reactants: list[SpeciesCoefficient]
    products: list[SpeciesCoefficient]
    vector: list[int] = Field(description="按 reactants + products 顺序的系数向量")


class NormalizedReaction(BaseModel):
    reactants: list[str]
    products: list[str]
    equation: str = Field(description="规范化的化学方程式文本")


class BalanceResponse(BaseModel):
    reaction_id: str
    cached: bool = Field(description="本次结果是否命中幂等缓存")
    normalized: NormalizedReaction
    coefficients: CoefficientBlock
    balanced: bool
    elements: list[str]
    charge_balance: dict[str, Any]


class ElementRow(BaseModel):
    element: str
    reactant_total: int
    product_total: int
    difference: int = Field(description="反应物合计 - 生成物合计；0 表示守恒")
    balanced: bool


class ChargeRow(BaseModel):
    reactant_total: int
    product_total: int
    difference: int
    balanced: bool


class SideSpecies(BaseModel):
    input: str
    normalized: str
    coefficient: int


class StoichView(BaseModel):
    coefficients_assumed: int | None = Field(
        description="该视图使用的统一假设系数；平衡视图省略此字段"
    )
    species: dict[str, list[SideSpecies]]
    elements: list[ElementRow]
    charge: ChargeRow


class AnalyzeResponse(BaseModel):
    reaction_id: str
    cached: bool
    normalized: NormalizedReaction
    raw: StoichView = Field(description="按单位系数统计的原始差异（配平前）")
    balanced: StoichView | None = Field(
        default=None, description="配平后的守恒视图；无法配平时为 null"
    )
    balanced_ok: bool
    error: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: dict[str, Any]


class VariantRequest(ReactionRequest):
    """``/balance/variants`` 请求：在 ReactionRequest 之上加枚举参数。"""

    max_coefficient: int = Field(
        ...,
        ge=2,
        le=30,
        description="允许的最大系数（含端点，范围 2～30）",
    )
    limit: int = Field(
        ...,
        ge=1,
        le=100,
        description="最多返回的配平方案数（含端点，范围 1～100）",
    )


class VariantItem(BaseModel):
    coefficients: CoefficientBlock = Field(
        description="按 reactants + products 顺序的一个互质正整数解"
    )
    sum: int = Field(description="该方案的系数总和（主排序键）")
    equation: str = Field(description="该方案对应的规范化化学方程式")


class VariantsResponse(BaseModel):
    reaction_id: str
    normalized: NormalizedReaction
    max_coefficient: int
    limit: int
    nullity: int = Field(description="守恒矩阵零空间维数（1～3）")
    count: int = Field(description="本次实际返回的方案数")
    total: int = Field(description="枚举到的全部方案数（截取前）")
    truncated: bool = Field(description="方案总数超过 limit、已截取时为 true")
    variants: list[VariantItem]


# ------------------------------------------------------------ 限量试剂计量
class StoichAmount(BaseModel):
    species: str = Field(
        ...,
        min_length=1,
        description="反应物化学式；必须与规范化后的反应物一一对应（等价写法可识别）",
        examples=["H2"],
    )
    moles: str = Field(
        ...,
        min_length=1,
        description="投料摩尔数的非负十进制字符串（如 \"1.5\"、\"0\"、\"1e-3\"），"
        "不要传 JSON 数字以避免浮点误差",
        examples=["2.0"],
    )

    @field_validator("species", "moles")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("必须是字符串")
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空白字符串")
        return stripped


class StoichiometryRequest(ReactionRequest):
    """``/stoichiometry/batch`` 请求：反应式 + 每种反应物的投料摩尔数。"""

    amounts: list[StoichAmount] = Field(
        ...,
        min_length=1,
        description="每种反应物一条投料记录；数量必须恰好等于规范化反应物数量，"
        "且 species 一一对应（无缺失、重复、未知物种）",
    )


class ReactionExtent(BaseModel):
    value: str = Field(description="反应进度 ξ = min(投料量 ÷ 系数)，十进制字符串")
    unit: str = Field(default="mol", description="反应进度以 mol 计（按本反应配平式）")


class StoichReactantResult(BaseModel):
    input: str = Field(description="客户端提交的原始化学式")
    normalized: str = Field(description="规范化后的化学式")
    coefficient: int = Field(description="最小互质正整数配平系数")
    fed: str = Field(description="投料摩尔数（回显）")
    consumed: str = Field(description="按反应进度计算的消耗量（mol）")
    remaining: str = Field(description="剩余量 = 投料 - 消耗（mol）")
    is_limiting: bool = Field(description="是否为并列限量试剂之一")


class StoichProductResult(BaseModel):
    input: str
    normalized: str
    coefficient: int
    theoretical_yield: str = Field(description="理论产量（mol）")


class StoichiometryResponse(BaseModel):
    reaction_id: str
    cached: bool = Field(description="配平结果是否命中幂等缓存")
    normalized: NormalizedReaction
    reaction_extent: ReactionExtent
    limiting_reactants: list[str] = Field(
        description="全部并列限量试剂的规范化化学式（投料比恰好等于系数比时多于一个）"
    )
    reactants: list[StoichReactantResult]
    products: list[StoichProductResult]


# ------------------------------------------------------------ 反向投料规划
class ReversePlanTarget(StoichAmount):
    """反向规划的一条目标产物记录；复用非负十进制字符串摩尔数校验。"""

    species: str = Field(
        ...,
        min_length=1,
        description="目标生成物化学式；必须是规范化生成物之一（等价写法可识别），可只提交部分生成物",
        examples=["H2O"],
    )


class ReversePlanRequest(ReactionRequest):
    """``/stoichiometry/plan`` 请求：反应式 + 一个或多个生成物的目标摩尔数。"""

    targets: list[ReversePlanTarget] = Field(
        ...,
        min_length=1,
        description="目标生成物记录，至少一条；species 必须是规范化生成物"
        "（无重复、未知物种），不必覆盖全部生成物",
    )


class PlanReactantResult(BaseModel):
    input: str = Field(description="客户端提交的原始化学式")
    normalized: str = Field(description="规范化后的化学式")
    coefficient: int = Field(description="最小互质正整数配平系数")
    required: str = Field(
        description="满足全部目标所需的理论投料量 = 反应进度 × 系数（mol）"
    )


class PlanTargetResult(BaseModel):
    input: str = Field(description="客户端提交的原始化学式")
    normalized: str = Field(description="规范化后的化学式")
    coefficient: int = Field(description="最小互质正整数配平系数")
    target: str = Field(description="客户端请求的目标摩尔数（回显）")
    achieved: str = Field(
        description="按最小反应进度该产物的实际达成量 = 反应进度 × 系数（mol）"
    )
    excess: str = Field(description="超额量 = 达成量 − 目标量（mol），驱动产物为 0")
    is_driving: bool = Field(
        description="是否为决定最小反应进度的目标产物之一"
        "（目标量 ÷ 系数取得最大值，可并列多个）"
    )


class PlanProductResult(BaseModel):
    input: str
    normalized: str
    coefficient: int
    achieved: str = Field(
        description="按最小反应进度该生成物的实际达成量 = 反应进度 × 系数（mol）"
    )


class ReversePlanResponse(BaseModel):
    reaction_id: str
    cached: bool = Field(description="配平结果是否命中幂等缓存")
    normalized: NormalizedReaction
    reaction_extent: ReactionExtent
    driving_targets: list[str] = Field(
        description="全部并列决定最小反应进度的目标产物规范化化学式"
        "（目标比恰好等于系数比时多于一个）"
    )
    reactants: list[PlanReactantResult] = Field(description="每种反应物的理论需求量")
    targets: list[PlanTargetResult] = Field(
        description="客户端提交的各目标产物：目标量、达成量与超额量"
    )
    products: list[PlanProductResult] = Field(description="全部生成物的达成量")


# ------------------------------------------------------------ 连续反应推演
class SequenceStepRequest(ReactionRequest):
    """连续推演中的一步反应；本身就是一条完整的反应物/生成物反应式。"""


class InventoryEntry(StoichAmount):
    """连续推演的一条初始库存记录；摩尔数为非负十进制字符串。"""

    species: str = Field(
        ...,
        min_length=1,
        description="库存物种化学式；等价写法（括号风格、空白、电荷写法不同）"
        "会合并为同一库存物种，摩尔数相加",
        examples=["H2"],
    )
    moles: str = Field(
        ...,
        min_length=1,
        description="初始库存摩尔数的非负十进制字符串（如 \"1.5\"、\"0\"、\"1e-3\"），"
        "不要传 JSON 数字以避免浮点误差",
        examples=["2.0"],
    )


class SequenceRequest(BaseModel):
    """``/stoichiometry/sequence`` 请求：按顺序执行的多步反应与初始物种库存。"""

    steps: list[SequenceStepRequest] = Field(
        ...,
        min_length=1,
        description="按顺序执行的反应步骤；每步都必须能解析并得到唯一配平",
    )
    initial_inventory: list[InventoryEntry] = Field(
        ...,
        description="初始物种摩尔库存；等价化学式合并为同一物种（摩尔数相加），"
        "未列出的物种库存视为 0",
    )


class SequenceInventoryItem(BaseModel):
    normalized: str = Field(description="规范化化学式")
    moles: str = Field(description="库存摩尔数（非负十进制字符串）")


class SequenceReactantResult(BaseModel):
    input: str = Field(description="客户端提交的原始化学式")
    normalized: str = Field(description="规范化后的化学式")
    coefficient: int = Field(description="最小互质正整数配平系数")
    before: str = Field(description="该步开始前的库存摩尔数")
    consumed: str = Field(description="本步消耗量 = 反应进度 × 系数（mol）")
    after: str = Field(description="该步结束后的库存摩尔数")
    is_blocking: bool = Field(
        description="是否为本步的阻塞反应物之一（零进度时库存为 0 的全部反应物）"
    )


class SequenceProductResult(BaseModel):
    input: str
    normalized: str
    coefficient: int
    before: str = Field(description="该步开始前的库存摩尔数（含上一步产物）")
    produced: str = Field(description="本步产出量 = 反应进度 × 系数（mol）")
    after: str = Field(description="该步结束后的库存摩尔数")


class SequenceStepResult(BaseModel):
    index: int = Field(description="从 0 开始的步骤序号（与请求 steps 顺序一致）")
    reaction_id: str = Field(description="该步反应的幂等 ID（与 /balance 共享签名）")
    cached: bool = Field(description="该步配平结果是否命中幂等缓存")
    normalized: NormalizedReaction
    reaction_extent: ReactionExtent
    blocked: bool = Field(description="该步是否因库存不足以零进度继续")
    blocking_reactants: list[str] = Field(
        description="零进度时库存为 0 的全部反应物规范化化学式；正常进度时为空"
    )
    reactants: list[SequenceReactantResult] = Field(
        description="各反应物该步的步前库存、消耗与步后库存"
    )
    products: list[SequenceProductResult] = Field(
        description="各生成物该步的步前库存、产出与步后库存"
    )
    inventory: list[SequenceInventoryItem] = Field(
        description="该步结束后的全部物种库存快照（最后一步即 final_inventory）"
    )


class SequenceResponse(BaseModel):
    steps: list[SequenceStepResult] = Field(description="按顺序的各步推演结果")
    final_inventory: list[SequenceInventoryItem] = Field(
        description="全部步骤执行完毕后的最终物种库存"
    )
