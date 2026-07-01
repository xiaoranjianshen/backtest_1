# 数据下载中心说明

这是独立的数据读取网站，不属于回测报告页。

## 启动

在项目根目录运行：

```powershell
python run_scripts\run_data_portal.py
```

默认监听：

```text
http://0.0.0.0:8601
```

本机访问：

```text
http://localhost:8601
```

公司内网其他电脑访问时，把 `localhost` 换成运行机器的内网 IP。

## 登录

默认网页登录账号和密码取：

```text
DATA_PORTAL_LOGIN_USER
DATA_PORTAL_LOGIN_PASSWORD
```

如果没有设置这两个环境变量，会回退到项目里的 ClickHouse 账号配置。

建议正式给别人用时单独设置网页登录账号密码，不要把数据库密码直接发给别人。

PowerShell 示例：

```powershell
$env:DATA_PORTAL_LOGIN_USER="data_viewer"
$env:DATA_PORTAL_LOGIN_PASSWORD="your-portal-password"
python run_scripts\run_data_portal.py
```

## 数据库连接

默认使用项目现有 ClickHouse 配置：

```text
CH_HOST / CH_USER / CH_PASS
```

也可以用环境变量覆盖：

```powershell
$env:DATA_PORTAL_CH_HOST="192.168.99.12"
$env:DATA_PORTAL_CH_USER="your-clickhouse-user"
$env:DATA_PORTAL_CH_PASSWORD="your-clickhouse-password"
$env:DATA_PORTAL_CH_PORT="9000"
python run_scripts\run_data_portal.py
```

## 功能

- 查看当前账号可访问的数据库。
- 查看数据库里的表。
- 查看表结构。
- 选择字段。
- 按时间字段过滤。
- 按品种、合约、symbol、instrument 等字段过滤。
- 支持精确匹配、前缀匹配、包含匹配。
- 预览数据。
- 统计当前过滤条件行数。
- 导出 CSV 或 Parquet。

## 只读边界

当前网站不提供自由 SQL 输入框。

代码只生成以下查询：

```text
SHOW
DESCRIBE
SELECT
```

并且查询时使用 ClickHouse 的 `readonly=1` 设置。表名和列名来自数据库元数据选择，不允许用户手写任意表名或任意 SQL。

## 大文件下载

下载文件会先生成在：

```text
data_portal/downloads/
```

默认页面直接下载的文件上限是 512 MB。超过后页面会显示服务器文件路径，避免把超大文件一次性塞进浏览器内存。

可以用环境变量调整：

```powershell
$env:DATA_PORTAL_MAX_DOWNLOAD_MB="1024"
python run_scripts\run_data_portal.py
```
