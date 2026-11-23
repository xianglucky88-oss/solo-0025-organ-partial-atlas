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
- `pipeatlas/bench.py`：试录压差序列归约与负载段风压恢复判定。

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
- `recovery` 风压恢复：按试录压差序列与开管变更分段，基准不全或同刻变更矛盾不给结论；只用最大间隔内插值的真实越线时刻判定首次连续恢复，断档跨潜在恢复时刻给待查。结果仅供离线检查风箱，不用于调音或控制设备。
