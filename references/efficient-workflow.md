# 0.4.0 执行流程与耗时记录

目标是减少重复准备和调度开销，保持补点算法、上限、数据冻结及 LOW 标注规则。**外部队列完成了合成 SDK/真实 BTK 文件验证，尚未完整运行真实 Nexus 多条试验；不承诺每条耗时或实际加速倍数。**

## 一次准备

1. 确定同一会话、同一受试者、同一已标定 VSK 的试验名单，各条明确原帧号 ROI，确认是否接受 LOW。按不同标定/会话分组。
2. 保存当前需要保留的编辑，复用已有独立英文副本。新建时只复制选中试验及共享 VSK/MP/模型定义，不必复制其他试验和旧导出：

```text
python stage_session.py --source D:\Capture\Session01 --destination C:\ViconWork\Session01 --trial "walking 01" --trial "walking 02" --copy
```

默认省略 `--copy` 只给计划，省略 `--trial` 保持原完整会话模式。选择模式含精确试验名的 C3D、Trial.enf、X2D/X1D/XCP、system/history、digitaldevices.xml 和该试验视频；含共享 VSK/VST/MP/model/pyCGM2/mkr/非设备 XML/Session.enf。不包含其他试验 C3D/原始记录、旧 NGF 导出、TRC/MOT/CSV 或子目录。复制与 SHA256 校验在准备时完成，不自动改内部绝对引用；若后续其他 Pipeline 需要额外静态/视频/自定义依赖，另外明确准备，不擅自猜测依赖。

3. 原始 X2D 可以选择复制，但批处理入口要求已重建、正确标记且保存的 C3D。先在副本用 Nexus 原生流程完成这项准备，不能将整条未标记数据交给补点器。确认输入不是以前未注明的估算结果。
4. 对这组 VSK 运行一次 NexusModelLinks。已有完整线则跳过生成和导入；确有缺线时生成副本，在 Nexus 导入一次。确认加载的是正确的已标定 VSK，避免依赖同名骨架缓存。**点名/模板名一致不能证明全部骨架参数一致。** 如果保存后参数不一致，先解决加载问题，工具不会通过重新标定或覆盖源 VSK 来消除差异。
5. 保存副本设置，在 Nexus 打开首个待处理试验，确认骨架，暂停人工编辑。此确认只适用于这一组，模型/会话改变后需重新准备。

## 建立清单与运行

以下为示意帧号，实际使用用户确认的原帧号。外部入口从 Windows 命令行或外部程序启动，**不能在 Nexus Python 页执行 NexusBatch.py**。需完整 skill 的 scripts 文件夹、Nexus Python/SDK/NumPy/pyCGM2 BTK。

```text
python NexusBatch.py plan --manifest C:\ViconWork\jobs.queue.json --session C:\ViconWork\Session01 --subject Subject --trial "walking 01:100:1800" --trial "walking 02:120:1700" --allow-low
python NexusBatch.py run --manifest C:\ViconWork\jobs.queue.json --setup-confirmed --timeout 60
python NexusBatch.py status --manifest C:\ViconWork\jobs.queue.json
```

不写 `--allow-low` 时保留原生未补缺口。清单生成后记录选中 C3D 与 VSK 哈希；运行开始一次检查这些输入，不重复哈希整个原始会话。每阶段仍做参与路径、当前试验/ROI、数据冻结等检查，VSK 文件状态改变则重新哈希并停止不一致的队列。模板扫描和显示连线准备不在逐条循环内。

`--setup-confirmed` 表示操作者已完成前述保存和标定骨架核对；代理不能在证据不足时自行添加这个标记。它不是自动模型验证或代替用户接受 LOW。清单只支持一个会话和受试者，不自动导入/重标定 VSK、不自动识别亮灯边界。

执行时在一个 Python 进程和 SDK 连接内解析本项目刚生成的 Pipeline：原生操作注册为可复用的 native-only Pipeline，Python begin/checkpoint/finish/verify-export 直接在外部进程调用。没有在 Nexus Python 步骤内部再调用 RunPipeline，也没有用后台进程绕过 SDK 的嵌套限制。

每条仍完整验证新 C3D，保留原帧号、原有效值、原生结果、ROI 外数据和 LOW CSV。不会 SaveTrial 覆盖基线 C3D/VSK；只有上一条 `export_verified`、输出哈希匹配，才关闭**程序自己处理且已另存的**试验并打开下一条。最后一条留在软件中供检查；用户在运行期间不能另外编辑试验。

## 进度、超时与恢复

`jobs.queue.state.json` 记录当前试验、phase、每阶段秒数、SDK 调用数、输出哈希、已完成及未开始条目；状态读取无需连接 Nexus 或重读全部轨迹。

- `complete`：清单各条新 C3D 已独立验证；仍需查看剩余缺口和 LOW，不能等同精度保证。
- `needs_attention`：停止队列，保留当前软件状态及文件。不自动重试 OpenTrial/RunPipeline，也不关闭半条试验或继续下一条。SDK 超时不等于操作已取消；先在 Nexus 查看是否还在运行/是否有保存弹窗。
- `NGF_BATCH.lock` 位于 Nexus Private 配置父目录，避免重复队列。正常完成会移除；异常保留。确认旧外部进程已停止、Nexus 已空闲并查看日志后，才可移除这个锁文件。不要为了提速忽略保存或安全提示。

`--resume` 仅按哈希跳过已验证条目、执行尚未开始条目。恢复前手动打开首个未开始试验并重新确认准备。若发现半条正在运行/需处理，工具拒绝自动续跑；先检查现场，必要时从独立的基线副本重做该条。不会把半条旧状态当作新的原始输入，也不自动删除备份。

## 已测范围

- 合成两条队列：原生调用为模拟，Python 检查/备用代码实际执行，C3D 用真实 BTK 写出/重读。验证已完成导出后才关闭、输入/ROI 外冻结、LOW、计时、超时停止、不盲重试和重复队列锁。
- 合成 2009 帧、47 标记、1 标记变化：SDK 写请求从47次降到1次；完全无变化时0次。Python 参数转换时间有记录，未加入真实 SDK 延迟，不能当作整条 Nexus 加速倍数。
- 当前开发会话只读复制计划：完整91文件约384 MB，选一条试验及共享定义14文件约50 MB，计划复制字节减少87%。没有重新复制或用它运行真实试验，也没有测量实际复制用时。
- 逐帧 CSV 生成一次，再按字节复制到交付位置；两份内容相同。数学核心函数与0.3.0相同。

以前的50项测试不能证明真实批量吞吐量。新队列仍需在代表性 Nexus 副本完成现场验收，再扩大到整组；单条软件原入口继续可用。SDK 语义依据本机 Nexus 2.16 `ViconNexus.py` 与 [官方 Python 接入说明](https://vicon-help.atlassian.net/wiki/spaces/Nexus216/pages/11613654/Python%2Bcommands%2Bfor%2Buse%2Bwith%2BNexus)。
