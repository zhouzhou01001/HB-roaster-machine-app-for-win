# 周周 ROAST 烘焙软件

当前源码版本：`1.0`。已完成锅次恢复和 Qt 启动修复，并生成本地 EXE 交付包；公开仓库不包含本地二进制交付包。

2026-09-12 锅次恢复版全量测试 245/245 通过，EXE 已通过离屏主界面就绪及持续启动检查；未完成真实设备连续两锅验收。这是发布前的本地测试结果，不代表 GitHub CI 已运行。此前整改记录见 [Superpowers 审查整改记录](docs/Superpowers审查整改记录-2026-09-12.md)。

## 开源许可

本项目原创代码采用 GNU Affero General Public License version 3（SPDX: `AGPL-3.0-only`），完整条款见 [LICENSE](LICENSE)。程序不提供任何担保，详见许可证。第三方依赖保留其各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

公开仓库：https://github.com/zhouzhou01001/HB-roaster-machine-app-for-win

授权码、机器绑定和源码加密目前仅为讨论方案，尚未实现。

本项目是面向 HB Model S / HB-M2SG 手动咖啡烘焙机的 Windows 桌面采集、记录、分析与归档软件。程序只读取设备温度；燃气、风门和滚筒转速由操作员手动记录，不向烘焙机下发自动控制命令。

## 核心流程

1. `开始采集`：连接串口并持续显示实时温度，不建立正式烘焙记录。
2. `开始烘焙`：建立本锅临时会话，显示入豆前预热曲线，等待入豆。
3. `入豆`：计时归零，重新初始化正式 RoR，开始写入本锅正式数据。
4. `排豆`：停止本锅正式记录，但设备采集可继续，用于观察排豆后温度。
5. `结束/归档`：确认预览后将锅次快照原子写入 `.hbroast` SQLite 数据库。

## 当前能力

- HB Model S 串口采集、模拟采集、断线提示与重连。
- 豆温、环境/炉温、进风温、排风温及对应 RoR 实时显示。
- BT、ET、IT、XT、RoR、燃气、风门、转速和事件统一曲线。
- 事件记录、取消和重新记录；入豆和排豆不可重复。
- 曲线显隐、线宽、坐标范围与刻度设置；滚轮不改变坐标轴。
- 左右纵轴永久不显示负刻度，正式曲线横轴从入豆 `00:00` 开始。
- CSV、JSON、报告与曲线 PDF 导出。
- 历史锅次回放、对比、参考曲线与调试诊断包。
- Windows 单文件 EXE，不依赖目标电脑安装 Python 或 VS Code。

## 开发运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 自动化测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/run_release_tests.py
```

以本次源码运行生成的 `release/evidence/test-report-1.0.json` 为准；历史版本的通过报告不能证明当前源码。详细范围见 [TESTING.md](TESTING.md)。

## 构建 EXE

```powershell
python -m pip install -r requirements-build.txt -r requirements-dev.txt
python scripts/build_exe.py --smoke-exe
```

产物由 `app/version.py` 决定，当前目标为 `dist/烘焙软件-1.0.exe`。

构建前会运行完整测试；`--check-only` 仅检查依赖、版本模板和完整测试。版本资源由 `version_info.txt` 模板生成到独立构建目录。

## 生成完整交付包

```powershell
python scripts/assemble_delivery.py
```

产物：

- `release/烘焙软件-1.0-完整交付包/`
- `release/烘焙软件-1.0-完整交付包.zip`
- `release/烘焙软件-1.0-完整交付包.zip.sha256`

以上为构建目标，不代表产物已经生成。组装必须具备对应版本文档、当前源码测试报告和当前 EXE 启动证据。

## 运行数据位置

默认保存在 `%APPDATA%\烘焙软件\`；若不可写，路径模块会回退到可写临时目录并记录问题。主要内容包括数据库、通道配置和 `data/debug_logs` 调试日志。

## 目录边界

| 目录 | 职责 |
|---|---|
| `app/device` | 串口、协议解析和通道映射 |
| `app/roast` | 状态机、RoR、事件、质量门、预测、回放和比较 |
| `app/database` | SQLite 迁移、旧数据兼容和锅次原子归档 |
| `app/ui` | PySide6 主窗口、曲线和业务面板 |
| `app/export` | CSV、JSON、报告和 PDF |
| `app/debug` | 会话调试日志与诊断包 |
| `scripts` | 构建、几何探针、日志分析和交付组装 |
| `tests` | 自动化行为与发布契约 |
| `docs/交付快照-0.0.5` | 历史版本交付文档，不能作为 1.0 发布证据 |
| `docs/框架冻结基线-0.0.3` | 已冻结设计基线，不得无审批改写 |

## 重要边界

Model S 保留已确认的 `CHAN;1200`、`CHAN;3400` 分组读取及物理通道重排。串口芯片名称不能证明设备使用 Modbus。本轮保留已有前三路映射，为旧三路配置补齐第四路，并拒绝重复映射；真机显示仍需与机身面板复核。冻结设计文档的修改须经项目所有者审批。

