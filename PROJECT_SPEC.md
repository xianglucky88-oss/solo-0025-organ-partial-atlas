# 音管泛音归属台：数据规格

## 使用者和目标

管风琴调律助理提交音管名册、基准频率、已开音管集合、温度校准和离线频峰列表，得到带候选歧义的音管归属、基音调律偏差、漏测缺口与复测记录。服务在回环地址启动，仅供本机处理。无需登录，不控制真实设备或放行生产数据。

## 数据合同

频率以赫兹计，音分相对温度换算后的目标频率。样例采用绝对温度平方根近似，非实际调律标准。只对已打开的音管生成候选，且频峰与泛音槽必须相互唯一才可归属，不按最小误差强行匹配。可信基音缺失时不给调律偏差。 把未分配的频峰与候选泛音槽按连通分组；峰数多于槽位数说明不可能全归属，峰数未超槽位数也不保证有唯一解。

`examples/sample.json` 是字段及合法取值示例。`alternate.json` 是同一对象的另一有效输入，`reject.json` 是应被阻止的输入。对象在输入顶层以 `id` 标识；归约行使用原始身份或来源组件组合作为标识。缺测映射为 `null` 或待定状态，不当成数值零。接口接受不超过 512 KiB 的 UTF-8 JSON 对象；重复字段、非法数值或域内冲突返回 400。

成果保存数据合同版本、原始包、各阶段输出、输入摘要和整包摘要。SQLite 通过唯一摘要让同一个有效产物只存一次，错误输入不落库；版本比对只允许同一对象和同一合同版本。校验摘要用于本地内容完整性，不是签名。

## 生产工作流

| 工作流 | 公开入口 | 主要样例观测 |
|---|---|---|
| 环境换算 | `POST /pipeatlas/api/evaluate 的 outputs.target` | 低音甲 的 频率（赫兹） 为 220 |
| 泛音候选 | `POST /pipeatlas/api/evaluate 的 outputs.candidate` | 峰乙 的 数量 为 2 |
| 归属解算 | `POST /pipeatlas/api/evaluate 的 outputs.assign` | 峰甲 的 状态 为 "assigned" |
| 冲突归因 | `POST /pipeatlas/api/evaluate 的 outputs.conflict` | 峰乙 的 状态 为 "contested" |
| 音管缺测 | `POST /pipeatlas/api/evaluate 的 outputs.coverage` | 高音乙 的 状态 为 "ambiguous" |
| 调律核对 | `POST /pipeatlas/api/evaluate 的 outputs.tune` | 独管丙 的 状态 为 "adjust" |
| 复测清单 | `POST /pipeatlas/api/evaluate 的 outputs.revisit` | 背管丁 的 复测顺序 为 1 |
| 频峰争用组 | `POST /pipeatlas/api/evaluate 的 outputs.clusters` | 峰乙 的 数量 为 2 |
| 归属差分 | `POST /pipeatlas/api/compare` | 异版产生差异，同版无差异，跨对象拒绝 |
| 封存版本 | `POST /pipeatlas/api/seal` | 同输入仅写入一次，旧版不随对照改变 |
| 导出交接 | `GET /pipeatlas/api/export/<摘要>` | 下载头有效且内容完整保留历史快照 |
| 作业计划 | `POST /pipeatlas/api/evaluate 的 outputs.program` | 作业计划 的 状态 为 "planned" |
| 音程约束 | `POST /pipeatlas/api/evaluate 的 outputs.intervals` | 甲乙音程 的 预计偏移 为 2 |
| 音管动作 | `POST /pipeatlas/api/evaluate 的 outputs.actions` | 甲管 的 动作音分 为 -2 |
| 计划复验 | `POST /pipeatlas/api/recheck` | 单管都在原允差内而约定音程越界时判不通过 |

## 职责

- `pipeatlas/contract.py`：JSON 与领域字段边界。
- `pipeatlas/domain.py`：解析领域单位与归约事实。
- `pipeatlas/rules.py`：消费归约证据和约束，产生业务判定。
- `pipeatlas/artifacts.py`：成果规范编码、摘要与差分。
- `pipeatlas/archive.py`：SQLite 历史版本与原子封存。
- `pipeatlas/service.py`：串联归约、比对和历史读取。
- `pipeatlas/server.py`：本机 HTTP 及静态界面入口。
- `web/ui.js`：归约阶段、图形、归档与交互。
- `pipeatlas/reconcile.py`：联合原始归约事实与业务判定，推导频峰争用组。
- `pipeatlas/program.py`：音程作业计划、整数档位求解与封存计划复验。

浏览器显示来源和判定结果；发布决定由服务端归约。自动化测试套件留置。每条工作流的有界核对命令见 `.project-runtime.json`。

## 扩展复核

顶层可选 `review`；省略时基础包与旧版逐字段一致。启用时增加下列结果，与原数据共同封存，旧版与增强版可按阶段比对。`examples/review-sample.json` 给出有效输入结构。

- `takes` 复测批次：每批按实际开管名单与当次温度独立归属。
- `isolated` 单管佐证：只接受单管且独占的槽位作为合奏冲突线索。
- `stability` 复测稳定性：只比较可信基音按各自温度修正后的音分。
- `pressure` 槽位拥挤：区分一个频峰的多候选与多个频峰挤同一槽。
- `harmonics` 相对泛音残差：缺同管独占基音时不输出相对残差。
- `handoff` 调律交接：区分合奏未决、单管线索、复测漂移与可信交接。
- `silence` 开管缺测账：只数实际开管的复测，区分缺测与未开。

## 音程作业

顶层可选 `plan`；省略时基础包与旧版逐字段一致。启用时增加下列结果，与原数据共同封存，旧版与增强版可按阶段比对。`examples/plan-sample.json` 给出有效输入结构。

资料员以当前试录为基线，在 `plan` 里登记本次要验的有向音程（`low` 端、`high` 端、相对名册音程允许的差值区间 `lo`–`hi`）、各管可用整数音分动作档位 `steps` 及非零动作代价 `cost`；固定管只登记零动作档位。一条音程的偏移是高端管温度归一后的基音偏差减低端管的同口径偏差，计划中的两端各加自己的动作量，同一支管在这张计划里只有一个动作量。

- `program` 作业计划：优先总动作代价最低、同价再比总绝对动作量小、仍同分按音管标识顺序比较动作序列的完整方案。每条音程分开都能满足但共用一支管后联合无解时，不开部分作业单，指出共同无法成立的一组音程和被它们共用的管；任一端没有可信基音时标明证据不足，不把缺值当零，也不改动已有频峰归属。
- `intervals` 音程约束：各音程的基线偏移与计划预计偏移；证据不足、共同无解、暂缓排定分别标注。
- `actions` 音管动作：每管的计划动作量与实付代价；固定管恒为零动作。

计划封存后，验收员用 `POST /pipeatlas/api/recheck` 提交 `plan` 成果摘要和一场有独立原峰的 `take` 试录，逐条核对原计划要求，并核对参考管实测偏差相对基线是否超出登记允差（封存条件里的调律音分容差）。复验按当次温度归一的读数判定，不用计划中的预测值替代读数；复验条件沿用所选封存计划，而不是届时工作台里最新的音程或允差。`examples/plan-recheck.json` 是可封存的验收样例。
