---
name: nexus-gap-fill
description: "在 Windows Vicon Nexus 中为已有标定 VSK 的试验补点，识别模型/贴点模板并生成三维显示连线副本。支持 Plug-in Gait、CGM、Oxford Foot、Hybrid CAST/Visual3D 及本机自定义 VST/VSK。先检查全英文 ASCII 路径，再原生优先补点并逐帧标注 LOW。用于单条/批量补点、模型识别、显示连线和 Python 接入；不计算关节角。"
license: MIT
metadata:
  version: "0.3.0"
---

# Nexus Gap Fill

面向 Windows、Nexus 2.16、已正确标记且已有标定 VSK 的试验。每条只启用一个受试者，单位为 mm。按 VSK 标记—体段关系适配不同点名，不代表任意布局的准确性均已验证。这是社区实现，不是 Vicon 官方发布；代码和说明采用 [MIT](LICENSE)，依赖项不随包分发。

## 模型识别与显示连线

执行补点前，先用 [scripts/NexusModelLinks.py](scripts/NexusModelLinks.py) 读取已保存 VSK、本机模板与 Nexus 模板信息。离线默认只读；在 Nexus Python 页空参数运行时生成独立的连线 VSK 副本，不改变当前试验。完整命令、导入方式、支持范围见 [references/models-and-links.md](references/models-and-links.md)，机器可读注册表为 [models/registry.json](models/registry.json)。只使用原单文件补点代码时，这个可选工具不成为其依赖。

- 注册表覆盖 Plug-in Gait、CGM1/CGM2、Oxford Foot、Hybrid CAST/Visual3D 模板家族；发现本机 VST，并允许用户指定其他 VST/VSK。注册表列名不等于已安装模板、动态计算模型已验证或所有公开模型均覆盖。每次报告本机实际找到/缺少的定义。
- 优先使用 VSK 的 `MODEL` 元数据，交叉检查 Nexus template。XML `VERSION="3.2"` 是文件格式标记，不能当作 CGM2.3。识别到模板身份不能证明先前使用的动态运算 Pipeline。同点名不能分辨所有 CGM 计算版本，标定状态仍需单独确认。
- 自动选连线模板须 `MODEL` 和完整物理标记集合一致；多份定义的连线/颜色有冲突就停止自动选择。未知模型可用相同自描述模板；没有定义则列候选，不用最近距离或名称相似度猜人体连线。用户明确选定模板时只使用同名端点，不自动重命名、补建点或用左右镜像替代。
- 只在新副本中补加缺少的 `MarkerSet/Sticks`，保留已有连线和颜色，验证其余字节、骨架、参数、目标/约束不变，核对原文件哈希。**显示线不能当作体段、关节或刚体约束输入补点。** 不用其他模板的 Skeleton/TargetSet 覆盖已标定 VSK。
- 连线工具依赖磁盘已保存状态；先保存副本的受试者编辑再生成，生成后又编辑则重新生成。工具检测到加载标记与磁盘 VSK 不一致时停止，但不能检测所有未保存参数。
- SDK 没有设置显示 sticks 的接口。生成后在 Nexus 的 **File Import → Import VSK** 选生成的同名 VSK（位于新目录），核对是原受试者及三维连线；不替换为未标定 VST，不创建新骨架。本版采用“自动生成，软件导入一次”的方式。不得在 Nexus 脚本中嵌套调用运行/重载，或声称 `vsk_copy_verified` 表示已在软件显示。

模板来自本机或用户合法取得的文件，运行时读取；共享包不附带厂家 VST/VSK。公开可下载不自动等于允许再分发。不要自动下载、上传或扫描其他受试者 VSK。

## 路径是执行前提

**运行补点及后续 Pipeline 前，所有实际参与运行的路径必须是全英文 ASCII，包含每一级父目录和相关文件名。** 不只检查最后一层目录。

- 检查试验副本、C3D/ENF/VSK/MP/XCP、脚本、Python 解释器、缓存、profile/Pipeline 和 Nexus Private Pipeline 目录。后续 Pipeline 另外引用的静态、模型、脚本和导出路径也要检查。
- “纯英文”在此指可打印 ASCII：英文字母、数字、空格和普通 ASCII 路径符号可用。中文、重音字母、其他文字、emoji、全角字符和控制字符不可用。检查原路径和解析后的物理路径，不能用英文链接绕过中文父目录。
- 示例 `C:\ViconWork\Session01\walking_01.c3d`。Windows 用户名含中文可能让 APPDATA/LOCALAPPDATA 含中文；仅换试验目录仍不满足要求。不要擅自修改用户名、全局环境变量或 Nexus 配置位置。
- 不满足就停止 Pipeline，指出具体路径，先准备英文副本。**不自动改名原始目录、试验、受试者或标记。** 移动 `.py` 不会改变 Nexus 已打开试验的路径。

严格路径规则来自本共享工作流的现场兼容性要求，不宣称 Nexus 的所有功能都完全不支持 Unicode。故障时读 [references/paths-and-errors.md](references/paths-and-errors.md)。

## 准备与执行

1. 确认目标试验、ROI、已标定 VSK 和一个启用受试者，并按上节读取模型身份。采集过静态或跑过 CGM/PiG 静态计算，不能单独证明 labeling VSK 已标定。按事件截取时先确认事件和帧号；脚本不自动识别标定架亮灯。
2. 保留原始数据，确认已有独立副本，或用 [scripts/stage_session.py](scripts/stage_session.py) 创建英文副本并核对哈希；默认只给计划，`--copy` 才复制。源父目录可含中文，复制后的相对目录/文件名不可含非 ASCII。工具不重写文件内部路径引用，复制后复核外部引用。命令和界面操作见 [references/nexus-workflow.md](references/nexus-workflow.md)。
3. 沿用当前会话已确认的低可靠策略。对其他用户，若尚未决定是否允许 LOW 写回，先澄清，或将脚本 CONFIG 的 `allow_low_reliability_completion` 设为 False，保留原生未补缺口；不能把原作者对特定数据的授权视为共享用户的授权。
4. 使用当前 Nexus 匹配的 Python，检查 NumPy、viconnexusapi、pyCGM2 的 BTK。先读取现场状态；切换试验前确认当前编辑已保存，不推断“无未保存修改”。
5. Nexus Offline 打开副本、设置 ROI。Python 页选择 [scripts/NexusGapFill_Single.py](scripts/NexusGapFill_Single.py)，参数留空，点击 Run Script，检查路径并注册 `NGF_SINGLE_...`。准备步骤不改变轨迹。参数 `preflight` 可只读检查路径。若 skill 安装目录含非 ASCII，先复制整个 skill 或单文件脚本到英文目录再加载。
6. Current Pipeline → Refresh List → 选择刚生成的名称 → 运行完整流程。不要跳过前面的快照/检查而只执行后续补点或导出。
7. 检查 `_NGF.summary.json`，只有 `status=export_verified` 才报告完整完成；核对剩余缺口、LOW 明细、排除点和源文件保护。`remaining=0` 不证明准确性。

SDK 禁止从 Nexus 正在执行的脚本嵌套调用 RunPipeline、OpenTrial、SaveTrial。必须用外层原生 Pipeline 顺序调度，不能承诺在 Run Script 内嵌套一键跑完整流程，也不通过后台进程绕过。

多条试验可在 Data Management 标记后 Batch Processing；先验证一条副本。同布局也要核对刚体预检查、目标集合和 ROI，不满足时重新准备；不同布局分开生成。失败时停止该试验后续操作，报告已完成/未完成项。GUI 多条批处理尚未现场验收。

## 保持算法与数据约束

- 原生优先：Woltring → 适用体段刚体 → Pattern → 原 VSK Kinematic Fit/Fill；默认上限 0.05、0.25、0.10、0.25 秒。原生未补入可能来自配置上限，不能说 Nexus 在其他设置下绝对无解。
- 备用只填仍缺失的 ROI 采样：刚体扩展 → 独立实现的低维 PCA/Kalman/RTS → 回归 → 相对偏移/时间插值/最近观测保持。每个备用采样均标 LOW；模型方差不是位置误差或置信保证。
- 冻结输入已有有效值及各阶段已接受的原生结果，恢复 ROI 外数据。输入已有值可能含以前补点，标 UNKNOWN_INPUT_ORIGIN，不自动当作相机实测。
- 目标为加载记录中至少出现一次的动态物理标记。静态专用点、无轨迹或整条从未出现的点列出，不写零冒充完成。同体段归属不自动证明刚体，须检查实际距离稳定性与几何。
- 不重做静态标定。轨迹变更后旧角度/力矩等模型输出标无效，后续重跑匹配动态模型；本 skill 不计算关节角。
- 输出新的 `trial._NGF.c3d`、`trial_NGF.provenance.csv`、`trial_NGF.summary.json`，保存输入快照和备份；原 C3D/VSK 不覆盖。Nexus 2.16 会在 Postfix 前加点。已有同名 NGF 导出先备份。
- CSV 每个 ROI 帧/目标点一行，保留原帧号、坐标、来源、方法、LowReliability 和 Reliability。C3D 普通有效标志不能表达可靠性，不得丢弃伴随 CSV 后声称数据可靠。

配置/实现见 [references/algorithm.md](references/algorithm.md)。精度与证据见 [references/validation-and-sources.md](references/validation-and-sources.md)：实际缺口无真值，长缺口开发回测曾出现约 125 mm 最大误差。

## 交付与共享

报告实际路径、ROI、缺失/原生/备用/剩余数量、LOW 文件及验证状态；区分代码测试、历史快照回放和软件完整试跑。

发布只包含源码、合成测试和脱敏说明，不含受试者 C3D/VSK/测量参数、设备序列号、个人路径、厂家 DLL 或许可证。使用本机试验进一步验证；不自动上传数据或创建公开仓库，不把现场经验包装成厂家承诺。
