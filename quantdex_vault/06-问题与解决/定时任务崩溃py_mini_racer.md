# 定时任务崩溃：py_mini_racer

## 现象
任务计划首次运行即崩溃，日志出现 V8 `partition_address_space` FATAL + mini_racer.dll。

## 原因
新浪数据源依赖 py_mini_racer(V8)，多线程并发初始化时原生段错误，把整个流水线带崩。

## 解决
- 更新与流水线解耦：`02b_update_pit.py` 先跑，失败也不影响 `10_daily_run.py`；
- 默认源改 baostock；`sina_fallback=False` 彻底禁用新浪降级。

## 自查
`data/store/logs/daily_run.log` 看是否有 FATAL/^C。
