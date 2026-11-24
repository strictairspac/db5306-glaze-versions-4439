# 初始结构

`src/models/tables.py` 定义初始关系结构。API 启动时显式创建演示库的表；此版本只包含初始 schema。需要在已有部署升级表结构时，应单独引入版本化迁移。

# 启动期幂等升级

`src/models/upgrade.py` 在 `create_all` 之后对已有卷做幂等修补：为分段迁存补充
`business_cases.archive_state` 列，并把 `business_cases.payload` 放宽为可空
（迁存切换后原件清空）。新建部署由 `create_all` 直接建出完整结构，升级语句不会执行。
