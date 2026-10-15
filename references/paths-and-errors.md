# 路径策略与故障定位

## ASCII 范围

检查输入拼写、完整绝对路径和解析后的物理路径；每个父目录及相关文件名须为可打印 ASCII。字母、数字、空格和普通 ASCII 标点可用；中文、重音字母、其他文字、emoji、全角和控制字符不可用。

`C:\ViconWork\Session01` 合格；`C:\Data\试验\Session01`、`C:\Users\某用户\Documents\Session01` 不合格；英文链接解析到中文目录同样不合格。

检查当前脚本、解释器、试验路径/名称、受试者 VSK、当前会话相关采集文件、缓存、profile 和 Nexus Private Pipeline 目录。运行后续 Pipeline 前，另行检查它引用的静态、模型、脚本、输出路径。此脚本无法推断其他流程所有外部依赖，路径通过不保证其他算法/标定均正确。

## 错误定位

| 错误 | 操作 |
|---|---|
| `ASCII_PATH_REQUIRED` | 日志列出问题路径，先复制到英文位置；`\u....` 是转义后的字符，便于旧控制台显示。 |
| `ASCII_RELATIVE_NAME_REQUIRED` | 源文件/子目录名称含非 ASCII，目标尚未创建；使用受支持命名流程并复核引用，不随意批量改 VSK/ENF 名。 |
| `This input folder is protected` | CONFIG 配置了原始目录保护；打开副本，不只移动脚本；共享版无个人盘符预设。 |
| `Python Script Execution Failed` | Nexus 通用信息；读取当前脚本对应缓存中同一时间的 `last_error.log`，不能仅凭截图猜缺依赖。 |
| `Nexus is not connected` | 核对 Offline 状态、运行中的 Nexus、匹配 Python/SDK。 |
| `Rigid groups failed this trial's precheck` | 当前数据不能直接沿用该组，重新准备，核对可见性/贴点；不跳过错误执行后续步骤。 |
| 找不到新 `_NGF.c3d` | 核对导出和时间，2.16 实际文件名为 `trial._NGF.c3d`，点由导出器加入。 |
| `Exported ... mismatch` | 保留现场日志，停止后续模型计算；不自动放宽容差或宣称已完成。 |

缓存默认 LOCALAPPDATA 下 `NexusGapFillSingle/<安装路径散列>`。只有缓存需要变更时可用 CONFIG 的 `cache_root` 指定英文绝对根，然后重新准备。若 APPDATA 因 Windows 用户名含非 ASCII，Nexus 自己的 Private Pipeline 目录仍不合格；不要只给 Python 伪造 APPDATA，让两者读不同配置。应在满足英文路径的运行环境中使用并核对配置。

不自动移动/重命名源数据。错误日志路径也不合格时仅保留标准错误输出。本规则是共享工作流的现场兼容要求，不是厂家对所有 Unicode 的全面限制。
