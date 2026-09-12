# Superpowers 审查整改记录

日期：2026-09-12。当前源码版本：1.0。

本记录说明本次缺陷修复及验收边界，不替代 `框架冻结基线-0.0.3`，也不构成新的框架冻结或正式发布批准。冻结设计与模块大规模拆分仍须项目所有者审批。

## 审查结论核对

- “没有 Modbus 实现”不能证明 HB Model S 通信错误。本次保留已核实的 Model S 文本命令、1200/3400 分组读取和物理重排，没有改成 Modbus，也没有以 USB 芯片名称推断协议。
- 原代码在重连耗尽时实际会重置状态；上一轮“状态完全不重置”的描述不准确。真正的问题是这会丢失恢复本锅所需的状态。本次改为保留状态并暂停采集，提供重新连接入口。
- 历史测试报告的不同数量不能证明同一源码状态。本轮仅认可当前源码运行产生且哈希匹配的证据。

## 整改对应表

| 审查问题 | 实施内容 | 主要文件 / 测试 |
|---|---|---|
| 重连后时间轴回退、丢样 | 保存连续采集时间偏移，重连第一帧按单调时钟补偿断线时长；拒绝未重连情况下的时间回退 | `app/ui/main_window.py`；`test_review_workflow_regressions.py` |
| 中止后丢失本锅状态 | 状态机增加采集暂停/恢复方法；保留本锅、事件、入豆零点；已有开始按钮变为重新连接 | `app/roast/workflow.py`；`app/ui/main_window.py` |
| 开始采集与正式记录混用 | 采集数据进入独立有限窗口；开始烘焙后才积累预热预览；入豆清除预览并重置正式 ROR | `app/ui/main_window.py`；三阶段及生命周期测试 |
| 入豆后等待采样时计时停滞、排豆后计时变化 | 真机正式计时使用单调时钟，独立定时刷新；排豆后显示固定结束时间；关闭窗口停止定时器 | `app/ui/main_window.py` |
| 断线跨段 ROR 异常尖峰 | 恢复点标记 `gap_before`；计算及修改窗口重算都从断点重新积累，曲线和 PDF 不跨缺失段连线 | `app/roast/ror.py`；`app/ui/curve_widget.py`；`app/export/chart_pdf.py` |
| CHAN 确认无效仍继续读取 | 缺失、错误或不完整确认时拒绝该帧；第二组失败不会发出混合样本 | `app/device/serial_manager.py`；`test_review_data_regressions.py` |
| 通道配置歧义及旧三路缺少第四路 | 源、目标唯一性校验；旧三路配置在内存补 CH4；保持已有前三路映射；配置编辑显示错误 | `app/device/channel_mapping.py`；`config/channel.json` |
| 第四路 ROR 缺少曲线和 CSV | 新增排风 ROR 曲线、显隐与线宽支持；CSV 追加第四路和断线字段；保留旧列顺序 | `app/ui/curve_widget.py`；`app/export/csv_export.py` |
| CSV 往返丢失扩展字段 | 导入保留第四路、ROR、人工参数、时间语义及断点；主窗口和曲线恢复旧表原始载荷中的扩展字段 | `app/roast/importer.py`；`test_review_export_regressions.py` |
| 新归档无法通过旧读取入口打开 | 统一历史枚举与读取，显式区分 archive/legacy ID；打开、导出、对比接入明确来源 | `app/database/database.py`；`app/ui/main_window.py` |
| 新归档信息编辑和参考保存误用旧表 | 原子更新新两表元数据；参考载荷记录来源，新档案参考不关联同号旧记录 | `app/database/database.py`；`app/roast/reference_profile.py` |
| 空档案替换当前数据库 | 候选数据库读取成功后再替换；空档案或读取失败保留当前数据库及显示数据 | `app/ui/main_window.py`；`test_review_workflow_regressions.py` |
| 非有限时间导致异常、归档输入缺少校验 | 拒绝 NaN/Infinity、负时间、样本非递增、无温度样本、非法/重复事件及无效元数据 | `app/roast/sample_quality.py`；`app/database/database.py` |
| 多文件导出留下半套结果 | CSV/JSON/报告全部暂存后替换，捕获失败时回滚；回滚失败保留备份位置供恢复 | `app/export/atomic.py`；`test_write_preview_contract.py` |
| 导出预览列数写死 | 实际列数由 CSV 字段定义生成，标明预览只显示核心字段 | `app/ui/main_window.py` |
| 温度和 ROR 横轴细微偏移 | 同一绘图区域直接同步精确范围，避免多图联动的像素对齐偏移 | `app/ui/curve_widget.py`；`test_chart_sync.py` |
| PDF 字体方块及文本裁切 | 离屏 Windows 显式加载系统字体；按文字宽度安排图例；调整标题与目标标注空间 | `app/export/chart_pdf.py`；`test_release_increment_baseline.py` |
| pytest 函数漏测 | 发布入口统一 pytest，收集 TestCase 与顶层函数；拒绝空收集、失败、跳过、预期失败和筛除用例 | `scripts/run_release_tests.py`；`tests/conftest.py` |
| 版本、源码与测试证据漂移 | 从 APP_VERSION 生成 Windows 资源；每次构建运行新测试，绑定源码/配置/测试/依赖哈希和独立运行 ID | `scripts/build_exe.py`；`scripts/run_release_tests.py` |
| 缺少 EXE 冒烟门禁 | 支持隔离数据目录的 EXE 限时启动检查；交付组装要求当前 EXE 对应、至少 10 秒的启动证据 | `scripts/build_exe.py`；`scripts/assemble_delivery.py` |
| 依赖未锁定及旧 spec 混用 | 精确锁定运行、构建、测试依赖；活动构建生成独立 spec 和版本资源；历史 spec 保留但不作为活动构建入口 | `requirements*.txt`；`scripts/build_exe.py` |
| 路径降级诊断被清空 | 多次路径查询保留并去重问题，显式清理接口用于新一轮诊断 | `app/utils/paths.py` |

## 验收证据

最终发布检查命令：`python scripts/build_exe.py --check-only`。

本次结果：PASS，发现并执行 232 项，232 项通过，失败/错误/跳过均为 0，用时 84.007 秒。运行 ID：`574000e0649545d2a6c0e87bc5698a10`。源码检查前后哈希一致：`3B7BBF47232CEA9CB3442140194B121471A3AA1940D124A9F9338E46C29A8035`。

结果以 `release/evidence/test-report-1.0.json` 及其中 run_id 对应的独立运行目录为准。报告记录源码检查前后哈希、发现数量、执行数量、失败/错误/跳过数量和测试输出哈希。失败运行保留，后续成功运行不会改变其独立目录证据。

Qt 测试统一使用真实系统字体，并在测试边界执行延迟删除与循环垃圾回收，避免循环回收重入尚未构造完成的 Qt 图形对象。各测试仍负责自己的窗口清理，不通过全局关闭窗口隐藏应用缺陷。

PDF 测试检查实际导出、渲染、曲线彩色像素及中文/数字字体支持。离屏几何与 PDF 检查不等同于真实显示器全面验收。

## 尚需完成与保留边界

| 优先级 | 事项 | 状态 |
|---|---|---|
| 发布前 | 在真实 HB Model S 上核对四路温度、固件 CHAN 确认、断线恢复与连续两锅 | 本轮未执行真机采集 |
| 发布前 | 60 分钟持续采集及实际 720P/1080P/高 DPI 显示验收 | 待实机测试 |
| 发布前 | 当前版本 EXE 构建、真实 EXE 启动检查和完整交付 ZIP | 本轮只运行 check-only，未生成新 EXE/ZIP |
| 发布前 | 编制并审定 `docs/交付快照-1.0` | 尚未生成，组装会明确阻止缺少该输入的发布 |
| 后续架构审批 | 拆分 MainWindow 采集、会话、归档和导出协调职责，进一步消除旧展示阶段与业务状态的重复表达 | 保持现有模块依赖，本轮仅将直接重置移回状态机方法 |
| 后续构建优化 | 精简 PyInstaller 体积及跨 Windows 版本环境验证 | 本轮未做体积优化 |

多文件导出提供捕获异常后的恢复，不保证断电时多个文件同时提交。历史数据库仍保留旧表，通过显式来源兼容读取，不进行破坏性迁移。
