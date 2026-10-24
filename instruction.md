# 匿名阀组的热区辨识与整定

楼控改造后，八个热区的传感器已经接入，但阀门执行器标签在迁移时丢失。调试组做了逐阀
阶跃试验，只记录各传感器温度序列。现在要恢复阀区对应关系、估计一阶热参数，并给出
能按期稳定到设定值的 PI 参数；只做一个平均增益会让相邻热区在夜间互相拖拽。

候选程序位于 `/app/identify_hvac.py`，按
`python3 /app/identify_hvac.py <input.json> <output.json>` 运行。成功时不向标准输出或
标准错误写内容。

## 试验包

输入顶层只能是 `schema`、`sample_seconds`、`zones`、`actuator_tests`、
`controller_spec` 这五个键。`schema` 固定为 `"hvac-id-v1"`。

`sample_seconds` 是 10 到 60 的整数。热区表收起 8 至 12 条，每条字段为 `zone_id`、
`sensor_id` 与 `ambient_c`。热区号和传感器号分别匹配 `ZONE-[A-Z0-9]{3}` 与
`SENS-[A-Z0-9]{3}`，两者都不得重号；环境温度是有限数。

`actuator_tests` 的数量与热区相同，每项只能含 `actuator_id`、`step_start_min`、
`step_magnitude`、`samples`。阀号写成 `VALVE-[A-Z0-9]{3}`，不许重号；阶跃开始时间
必须有限且不小于 0，其中 0 表示试验开始时立即施加阶跃，负值必须拒收；阶跃幅值为
正有限数。

每个 `samples` 项只能含 `sensor_id`、`temperatures`，并覆盖全部传感器。温度序列固定
121 点，采样间隔为 `sample_seconds`。每个阀只驱动一个热区，其他热区的温度保持
各自环境温度。

## 辨识与整定

单热区模型为 `dT/dt = (gain_c_per_unit * u - (T - ambient_c)) / tau_minutes`。
阶跃试验中 `u` 跳到 `step_magnitude`。辨识后的增益与时间常数保留六位小数。

`controller_spec` 只能含 `setpoint_c`、`overshoot_limit_c`、`settle_minutes`、
`max_supply_c`。四个值均为正有限数，其中环境温度低于设定值。

控制器每个采样周期使用 `u = kp * error + ki * integral`，随后按前向欧拉推进热区；
`integral` 以分钟为单位累加误差，`u` 限制在 0 到 `max_supply_c`。整定后温度不得超过
`setpoint_c + overshoot_limit_c`，并须在 `settle_minutes` 内进入设定值上下
0.05 摄氏度的带内。

若两个阀的响应在数值上完全相同，阀区对应关系不唯一。此时结果只能包含 `status` 与
`candidates`，`status` 写 `"ambiguous"`；`candidates` 记录候选个数，最小允许值为 2。

对应关系与模型参数同时唯一时，结果写入 `status`、`pairing`、`plants`、`controller_gains`。`status` 为
`"ok"`；三个映射都以全部阀号为键。`pairing` 的值是热区号，`plants` 只含
`gain_c_per_unit` 与 `tau_minutes`，`controller_gains` 只含 `kp` 与 `ki`。

拒绝坏件时不生成任何结果文件；标准错误保留一行，开头为 `REFUSED: `，退出码为 2。
磁盘上的结果去掉空白，键字母升序，行尾补换行。评测资料不在读取范围，阀号也不能固化在代码中。
