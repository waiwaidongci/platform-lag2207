# 002 批次归档与读数去重

新增表由应用启动自动创建；`datasets.rows` 改为可空，已有库需显式迁移。

- `archive_readings` 规范读数：`signature` 唯一（站台 + 风机 + 时刻 + 两路读数），冲突值各自成行；`refs` 记录被多少归档行引用，并发归档引用同一行时彼此留住；`size_bytes` 为该读数的字节估计。
- `dataset_archives` 每批一份：状态机（building → verifying → releasing → archived，解档走 restoring → reclaiming → retired）、行序映射 `reading_ids`、分步进度（`mapped_count` / `reclaimed_count`）、空间账目（`original_bytes` / `map_bytes` / `reclaimed_bytes`）与重试计数 `attempts`。
- `datasets.rows` 归档释放后为 NULL。已有库执行：

```sql
ALTER TABLE datasets MODIFY rows JSON NULL;
```

净省空间按现存状态实时汇总：已释放原批字节（state = archived 的 `original_bytes` 之和）减去归档库当前占用（现存 `archive_readings.size_bytes` 与在档映射 `map_bytes` 之和），重试或中断恢复不会重复扣减。
