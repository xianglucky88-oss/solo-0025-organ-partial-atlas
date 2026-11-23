# 音管泛音归属台

管风琴调律助理使用浏览器处理音管名册、基准频率、已开音管集合、温度校准和离线频峰列表，交付带候选歧义的音管归属、基音调律偏差、漏测缺口与复测记录。全部内置数据为合成样例，不连接生产设备或在线服务。

## 本机运行

Python 3.11 或以上（含标准库 SQLite），不需要下载包。现代浏览器负责界面；Node.js 仅用于构建时检查 JavaScript 语法。

```sh
python3 -m pipeatlas.server
```

访问 `http://127.0.0.1:8862`。默认把封存成果写入 `.local/pipeatlas.sqlite`；可传 `--port`、`--db` 覆盖。界面先显示合成首例；可切换对照、导入 JSON、查看证据明细、封存成果、比较同对象版本或下载指定版本。

## 手动核对

```sh
python3 -m pipeatlas.check_flow target
python3 -m pipeatlas.check_flow clusters
python3 -m pipeatlas.check_flow export
```

这些命令在临时成果库和临时回环端口运行实际 HTTP 接口，分别核对样例、对照和拒绝路径。`examples` 保存业务示例及可人工核算的观测点。完整命令见 `.project-runtime.json`；自动化测试套件留置，不包含私有验收测试、缺陷任务或参考解答。

### 扩展复核

使用「复核样例」，随后在「扩展复核」切换各项结果。有效输入见 `examples/review-sample.json` 与 `examples/review-alternate.json`。例如运行 `python3 -m pipeatlas.check_flow takes` 可核对派生结果、拒绝非法复核和基础版本兼容性。

### 试录分频带比对

使用「试录样例」，在「扩展复核」查看「试录分频带比对」与「试录证据段」。有效输入见 `examples/rehearsal-sample.json` 与 `examples/rehearsal-alternate.json`：

```sh
python3 -m pipeatlas.check_flow rehearsal
python3 -m pipeatlas.check_flow rehearsal_evidence
```

近席与远席按共同有证半开时间段对齐，线性功率先换算再扣背景；覆盖比例与净能量总和之比在同一共同支持范围上分别给出，超限、覆盖不足与有证通过分别说明。
