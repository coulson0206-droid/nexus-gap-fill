# 模型识别与三维显示连线（0.3.0）

这是显示 sticks 的准备工具，不是关节建模器或自动贴标签工具。补点仍按已标定 VSK 的物理标记—体段归属运行，显示连线不参与刚体或运动学约束。工具无需联网，不上传轨迹或 VSK。

## Nexus 中使用

1. 在英文 ASCII 路径的独立试验副本中，保存当前轨迹和受试者编辑，启用一个受试者。保留完整 skill 文件夹，包含 `scripts/NexusModelLinks.py` 和 `models/registry.json`；新工具不是原补点单文件的一部分。
2. Python 页选择 `NexusModelLinks.py`，参数留空，Run Script。它只读当前 SDK 状态和磁盘已保存 VSK，扫描本机 Nexus `ModelTemplates`、pyCGM2 `Install/vst`，在英文 LOCALAPPDATA 的 `NexusModelLinks/NML_.../` 生成副本与报告。原 VSK 和当前轨迹不改写。Windows 用户名导致缓存路径含中文时，用 `--output C:\ViconWork\ModelLinks` 指定英文缓存；脚本/解释器/试验等路径仍须全部通过检查。
3. 看输出的 `status`、`detection`、`output_vsk` 和 `report_path`。`vsk_copy_verified` 表示新文件校验通过；`applied_in_nexus` 固定为 false。`already_complete` 表示匹配模板没有需要补加的线，通常无需再次导入。
4. 若生成了需要使用的副本，在 Pipeline Available Operations 中选 **File Import → Import VSK**，指定 `output_vsk`，单独运行该 operation。确认作用于原受试者、点名正确，并在三维视图查看显示。文件保留原受试者 basename，位于新的目录。遇到当前版本的导入选项不明确，先查该版本界面，不删除原受试者来强行导入。
5. 如果生成后又修改过受试者参数，先保存再重新生成；不要导入过时的副本。软件内完成显示核查后，再按原补点流程运行 `NexusGapFill_Single.py`。

Nexus 2.16 SDK 的公开 Python 包无设置显示 sticks 的方法；脚本不能绕过禁止嵌套 RunPipeline/OpenTrial/SaveTrial 的要求。本版未自动注册猜测参数的 Import VSK Pipeline。导入是用户已接受的一次软件操作，尚未做该 GUI 导入的现场验收。

## 离线命令

使用匹配的 Python 运行（本工具只依赖标准库；接入 Nexus 时需 SDK）：

```text
python NexusModelLinks.py --list-models
python NexusModelLinks.py --vsk C:\ViconWork\Session01\Subject.vsk
python NexusModelLinks.py --vsk C:\ViconWork\Session01\Subject.vsk --write --output C:\ViconWork\ModelLinks
python NexusModelLinks.py --vsk C:\ViconWork\Session01\Subject.vsk --template C:\ViconWork\Templates\MyLayout.vst --write --output C:\ViconWork\ModelLinks
python NexusModelLinks.py --vsk C:\ViconWork\Session01\Subject.vsk --template-root C:\ViconWork\Templates
```

`--no-default-roots` 禁用自动扫描安装目录；可重复 `--template-root`。自动扫描只读 VST，不收集其他受试者 VSK；VSK 显示定义需显式 `--template`。自定义标记别名/左右命名不能自动映射，先由用户提供使用精确点名的显示定义。输出总是新的 `NML_...` 子目录，不覆盖源或上一份结果。

离线省略 `--write` 时只在标准输出给计划，不创建文件。未解决模板/元数据歧义时退出码为 2，不生成可导入 VSK；解析/路径等错误退出码为 1。状态为 `existing_links_only_no_matching_template` 时保留已有自定义线，未发现可供补加的模板，不等于已覆盖该模型全部连线。

## 支持范围与证据

注册表有 13 个身份条目，表示可识别的命名，**不是 13 个已验证计算算法**。同一条目可有多种身体范围或贴点变体：

| 家族/条目 | 自动识别依据 | 连线来源与限制 |
| --- | --- | --- |
| Plug-in Gait | 明确 PlugInGait MODEL/template | 本机 FullBody/LowerBody/Functional VST；这些可共享 MODEL，须完整点集匹配。SACR/PSIS 变体不可混套。 |
| CGM1.0、CGM1.1 | 明确带版本的元数据 | 本机或用户指定定义；不能凭共同点集确定计算版本。 |
| pyCGM2 CGM1 medial、KADmed | 对应模板 MODEL | 识别模板身份，不把这两个模板分别宣称为 1.0/1.1。 |
| CGM2.1–2.6 | 对应 CGM21–26 或带小数的元数据 | 本机匹配 VST，FullBody/LowerLimb/额外 PIG 变体分开匹配。CGM2.6 本机未找到定义，只有命名支持，需用户提供匹配模板。 |
| Oxford Foot Model | OxfordFootModel/明确 OFM 元数据 | 左、右、双侧定义须靠点集分辨；不由同名 MODEL 猜左右。 |
| Hybrid CAST / Visual3D | 对应 Nexus Hybrid CAST 模板元数据 | 只覆盖该模板家族；不能代表所有 Visual3D/CAST 自定义模型。 |
| 其他自定义模型 | VSK MODEL 与本机/指定 VST/VSK | 不强制归入注册表；完全匹配的自描述模板可自动生成，未知且无定义时不猜。 |

2026-10-15 本机只读发现 88 份安装 VST（含不同安装位置/版本的重复），成功读取显示定义；命中 10 个注册表条目。本机没有带明确 CGM1.0、CGM1.1 或 CGM2.6 身份的模板。这个数量是现场覆盖证据，不是全市场完整性或其他电脑安装数量。

模型报告优先保留 VSK MODEL，Nexus template 显示另一已知版本/家族则阻止生成。`marker_layout_candidate_profiles` 仅为同点集候选，不是识别结论或概率。`calibration_verified=false` 提醒模板名称不能证明已完成标定或动态模型计算。

自动来源必须 MODEL 与完整 MarkerSet 相同，多份来源的边和颜色还须一致。否则 `needs_template` 或 `ambiguous_display_definitions`，由用户选择准确的模板。显式选择模板时可只取目标存在的端点，缺失端点列入 `skipped_edges_missing_target_markers`；已知家族/版本冲突仍拒绝。

## 文件保护与边界

- 目标必须是完整可解析、UTF-8、无 DTD/实体、无命名空间的 VSK；无法解析就停止，不“修复”标定数据。点名区分大小写，不能有空名/重复名或无效 Stick。
- 补加 display Stick，已有线、颜色、未知属性均保留；不移除用户自定义边。模板的 Skeleton/Parameters/TargetSet 不进入目标。
- 实际 XML 写入只替换/新增 MarkerSet 内的 Sticks 字节范围，其余字节原样保留；再次解析比较非 Sticks 结构，验证输出与原文件哈希。源在计划后变化则中止。重复执行写入不同目录。
- 部分厂家安装 VST 在显示区以外有 XML 格式错误。本机 Hybrid CAST 示例缺少属性间空格；本工具可只提取严格可解析的独立 MarkerSet 与根 MODEL 读取连线，并在来源 warning 明确标注。不会修复或导入该 VST，也不会用这种宽松方式接受目标标定 VSK。
- SDK GetMarkerNames 可能不含没有轨迹的静态点，因此核对磁盘/当前点集时也纳入 GetSegmentDetails 的物理点。点集相同仍不能证明所有未保存标定参数相同，必须先保存再生成。
- 不下载/再分发厂家模板。注册表只提供命名识别和来源链接，运行时读取本机合法取得的定义。MIT 只覆盖本项目源码和说明，厂商/第三方文件保留原许可。

## 来源

检索日期 2026-10-15，主源与本机 Nexus 2.16 XML/SDK 交叉核对：

- [Vicon native skeleton templates](https://www.vicon.com/wp-content/uploads/2022/08/Vicon-Nexus-guide-1_native-skeleton-templates.pdf)：厂家说明的常用模板家族。
- [Vicon Models and Scripts](https://www.vicon.com/software/models-and-scripts/)：公开模型/脚本入口，公开下载不作为再分发许可。
- [Oxford Foot Model](https://www.vicon.com/software/oxford-foot-model/)：官方模型入口。
- [pyCGM2 作者仓库](https://github.com/pyCGM2/pyCGM2) 与 [项目模型文档](https://pycgm2.netlify.app/)：CGM 版本与模板来源。
- [Nexus 2.16 Pipeline tools](https://vicon-help.atlassian.net/wiki/spaces/Nexus216/pages/11614296)：Import VSK operation。
- [Nexus 2.16 Python commands](https://vicon-help.atlassian.net/wiki/spaces/Nexus216/pages/11613654/Python%2Bcommands%2Bfor%2Buse%2Bwith%2BNexus)：SDK 工作流；具体方法另核对本机 viconnexusapi。
