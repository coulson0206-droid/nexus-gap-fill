# Nexus Gap Fill Skill

Windows / Vicon Nexus 动捕轨迹自动补点 skill，采用 MIT 许可。**全部运行路径须为英文 ASCII，包含父目录和相关文件名。** 先用 Nexus 原生算法，再对剩余缺口估算并逐帧标注 LOW。

**0.4.0 优化执行流程**：按名单复制所需试验；同会话同骨架准备一次；新增外部单进程队列，减少 Python operation 启动请求；只写变化轨迹，CSV 生成一次；逐条计时，超时停止并记录现场。外部队列当前完成合成 SDK/真实 BTK 验证，尚未实测完整真实 Nexus 批量耗时。见 [高效执行与批处理说明](references/efficient-workflow.md)。

**0.3.0 新增模型识别和三维显示连线准备**：支持 Plug-in Gait、CGM1/CGM2、Oxford Foot、Hybrid CAST/Visual3D 模板身份，自动发现本机 VST，也可显式指定自定义 VST/VSK。生成只修改显示 sticks 的已标定 VSK 副本，再由用户在 Nexus 导入一次；不改骨架或关节。没有可靠定义时报告候选/缺少模板，不承诺覆盖所有公开模型。

面向已有标定 VSK、正确标记、一个启用受试者的试验。已知运行环境为 Nexus 2.16；其他版本需验证。不是 Vicon 官方插件，不保证所有标记布局/动作的重建精度。

## 安装与调用

将完整 `nexus-gap-fill` 文件夹放入 Codex 的 skills 目录，例如 `~/.codex/skills/nexus-gap-fill`，按客户端要求刷新或重新开始会话后调用：

```text
使用 $nexus-gap-fill，先检查英文路径和模型身份，在副本上生成显示连线并补点，保留原始数据和逐帧可靠性标注。
```

若安装路径含中文或其他非 ASCII，供 Nexus 执行的脚本须先复制到英文运行目录，例如 `C:\ViconWork\nexus-gap-fill`。试验、解释器、缓存及 Nexus 配置目录也必须通过检查。

仅在 Nexus 内使用时，可单独取 `scripts/NexusGapFill_Single.py`：打开英文路径副本、设置 ROI，Python 页加载该文件、参数留空运行；再 Refresh List，选择生成的 `NGF_SINGLE_...` Pipeline 运行。无需原多文件程序。Nexus 不允许在脚本内嵌套 RunPipeline，所以是这两步。

NumPy、Nexus SDK、pyCGM2/BTK 需安装在匹配的 Nexus Python 环境；本包不分发这些依赖或厂家软件。

## 自动识别与显示连线

在 Nexus 保存试验副本和受试者编辑后，Python 页加载 `scripts/NexusModelLinks.py`，空参数运行。保留整个 skill 文件夹供工具读取 `models/registry.json`。检查输出的模型身份和 `output_vsk`；在 **File Import → Import VSK** 载入生成的同名 VSK，再检查三维显示。原 VSK 不覆盖，尚未在 GUI 自动导入；保存后又有参数改动须重新生成。

注册表有13个身份条目，连线来自本机/用户合法取得的实际模板，未附带厂家模板。XML `VERSION` 不代表计算模型版本；相同点名无法分辨所有 CGM 版本。当前版本不根据距离猜连线，不把显示线当作补点刚体。详细支持表、离线命令和歧义处理见 [模型与连线说明](references/models-and-links.md)。

## 包内容与检查

- `SKILL.md`：执行准则、路径规则与数据保护。
- `scripts/`：单文件补点代码、可选按名单复制工具、模型连线工具、Nexus 之外运行的同会话队列。
- `models/registry.json`：有来源的模型身份注册表；不包含厂家或受试者模板。
- `references/`：操作、错误排查、算法与证据边界。
- `tests/`：合成/文件往返/显示连线保护测试，不使用现场受试者数据。
- `validation.json`：此次发布的自动化检查记录。

在包目录内，用配置了上述依赖的 Nexus Python 运行：

```text
python -X utf8 -m unittest discover -s tests -v
```

共享包已完成64项自动化测试，包含模拟两条队列和真实 BTK 文件核对；没有重新运行真实试验，不能据此承诺批量用时。0.3.0 的模型/连线检查属于继承证据，Import VSK 仍未软件内完整验收。补点数学核心未改；早期多文件实现曾完整试跑一条独立 Nexus 副本。详见验证说明。实际长缺口开发回测最大误差约125 mm，所有备用结果仍标 LOW。

完整发布内容采用 [MIT](LICENSE)。不包含受试者数据、VSK、个人现场日志或厂商 DLL；来源/依赖仍适用各自许可。下载/解压 skill 不代表已上传任何现场数据。
