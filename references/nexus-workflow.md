# Nexus 接入、复制与批处理

## 环境与副本

已知环境为 Windows、Nexus 2.16 自带 Python 3.7.3、NumPy 1.18.5、pyCGM2 的 BTK。依赖不随 skill 分发；其他版本需重新验证。单文件脚本可单独复制到英文目录供 Nexus 加载，副本工具和测试不参与原生 Pipeline。

先保存当前需要保留的编辑。源应是完整会话目录，含 C3D、ENF、已标定 VSK 等；不要只复制 C3D。下面为 PowerShell 示例，按现场修改路径：

```powershell
& 'C:\Program Files\Vicon\Nexus2.16\Python\python.exe' 'C:\ViconWork\nexus-gap-fill\scripts\stage_session.py' --source 'D:\Capture\Session01' --destination 'C:\ViconWork\Session01'
```

默认仅返回计划，不创建目标。检查源/目标和数量后，给同一命令加 `--copy`。工具拒绝已存在的目标、源目标重叠、文件/目录链接及含非 ASCII 的相对名称；逐文件 SHA256 核对，并检查复制中源列表/内容未变化。

`ngf-copy-manifest.json` 状态必须为 `verified_copy`。失败目标可能保留诊断，状态为 `failed_copy_do_not_use`；不自动删除/合并/覆盖，重新选择新目标。复制只复制字节，**不重写 ENF/VSK/MP 或其他文件的内部绝对路径引用**。复制后复核后续 Pipeline 引用的静态、模型和脚本路径。

源父目录含中文可以复制到英文目标；源文件/子目录名称含非 ASCII 时停止，应使用 Nexus 支持的命名/另存流程并核对内部引用，不做批量拼音替换。

## Nexus 内运行

1. Offline 打开英文副本，启用一个受试者，检查 VSK 和 ROI。
2. Python 页选择单文件脚本；参数 `preflight` 只读检查路径，留空检查并生成流程。
3. Current Pipeline → Refresh List → 选择输出的 `NGF_SINGLE_...` → Run。
4. 不输入 `run-current`：此发布没有该入口，没有嵌套 RunPipeline。
5. 核对新 C3D/CSV/JSON；只有 `export_verified` 表示独立 C3D 核对也完成。

移动脚本或更改配置/缓存后重新准备，不只手改旧 Pipeline 的一部分路径。CONFIG 包含缺口上限、备用开关和 `protected_roots`；共享版不硬编码个人原始数据盘符，使用者可自行配置需保护目录。

## 批处理

每条保存试验都须有正确 ROI，或已裁剪到所需范围；脚本不判断亮灯事件。Data Management 标记后 Batch Processing，先验收一条副本。刚体组和目标点取决于本条数据；相同布局不一定可沿用同一 profile，不满足预检查时重新准备并分组运行。错误后先排查，不单独运行后续导出。

0.4.0 提供外部单进程队列 `NexusBatch.py`，限定同会话、同标定 VSK、已标记保存的 C3D，见 [高效执行流程](efficient-workflow.md)。从 Nexus 之外运行；启动时一次确认保存与骨架，原生阶段使用 native-only Pipeline，同一进程调用检查/备用代码。GUI 与外部两种多条路径均尚未完成真实 Nexus 现场批量验收。
