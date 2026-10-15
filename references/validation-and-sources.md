# 验证边界与来源

## 已有证据

早期多文件实现曾在独立跳跃副本完整执行 Nexus 2.16 原生+备用+导出核对：120 Hz、47 动态目标点、1760 帧；1548 缺失点帧，当前上限下原生补入 69、备用 1479、剩余 0。4 个无轨迹静态点明确排除，源 C3D/VSK 哈希未变。BTK 最大单分量写出/读回差约 0.0051 mm，仅代表序列化一致性。

6 条历史快照曾回放检查冻结/补齐，不是6条重新运行原生。不同真实布局/受试者及 GUI 多条批处理未现场验收。

早期单文件通过14项测试，含真实 BTK I/O、数据冻结、ROI/帧映射、未知点、改名布局、空参数准备。公开版增加路径/复制测试，结果见根目录 validation.json。本次没有对另一组真实试验重新完整跑公开版；上述完整软件运行属于继承证据。

## 人工遮挡开发回测

隐藏值先从学习/推断输入移除，以下仅备用算法案例；覆盖一名受试者、一套布局，曾用于边界偏差修正，不是独立最终测试，也不是原生优先综合准确率。

| 动作、目标与缺口 | RMSE mm | 95分位 mm | 最大 mm |
|---|---:|---:|---:|
| 步行，骨盆前点内部30帧 | 0.79 | 1.44 | 1.53 |
| 步行，足跟内部90帧 | 16.52 | 25.45 | 26.56 |
| 跳跃，双骨盆前点内部60帧 | 25.05 | 36.53 | 37.24 |
| 跳跃，双骨盆前点开头300帧 | 50.55 | 99.50 | 124.65 |

实际缺口无真值，关节角误差未验证。平滑、骨架一致、零缺口和小模型方差都不证明准确；所有备用仍标 LOW。在匹配动作、贴点、缺口长度/位置和同时遮挡模式的独立实测片段上验证。

## 来源与许可

- [Vicon 原生补点 FAQ](https://www.vicon.com/support/faqs/what-gap-filling-algorithms-are-used-nexus-2/)：原生算法条件，以安装版本为准。
- [Nexus 2.16 Pipeline operations](https://vicon-help.atlassian.net/wiki/spaces/Nexus216/pages/11614296)。
- [Set up Python for Nexus](https://vicon-help.atlassian.net/wiki/spaces/Nexus216/pages/11612178/Set%2Bup%2BPython%2Bfor%2Buse%2Bwith%2BNexus)。
- [Calibrate a labeling skeleton](https://vicon-help.atlassian.net/wiki/spaces/Nexus216/pages/11602491/Calibrate%2Ba%2Blabeling%2Bskeleton)：VSK 标定与只采集静态或生物力学静态计算有区别。
- 本机 Nexus 2.16 viconnexusapi/ViconNexus.py：RunPipeline/OpenTrial/SaveTrial 不可从 Nexus 正执行的脚本嵌套调用。本包不复制 SDK。
- Burke & Lasenby (2016), [DOI 10.1016/j.jbiomech.2016.04.016](https://doi.org/10.1016/j.jbiomech.2016.04.016)；[作者 MoGapFill](https://github.com/mgb45/MoGapFill)。独立实现低维 Kalman 思路，不复制/捆绑其代码或声称数值等价。
- ASCII 规则来自用户现场兼容要求，由路径守卫执行，未写成厂家的全面 Unicode 限制。

原创代码和说明 MIT；厂商软件、SDK、第三方库和来源页面适用各自许可。无厂商二进制、受试者数据、个人路径或现场原始日志。
