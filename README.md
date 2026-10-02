# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 运行环境

- Python 3.11
- Django REST Framework
- SQLite

## 安装与初始化

```bash
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate --run-syncdb
```

## 测试

```bash
cd backend
pytest -q
```

## 编译检查

```bash
python -m compileall -q backend
```

## API 验收

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py shell -c "from rest_framework.test import APIClient; from apps.authentication.models import User; u=User.objects.create_user('smoke','safe-pass',role='admin'); c=APIClient(); r=c.post('/api/auth/login/',{'username':'smoke','password':'safe-pass'},format='json'); print(r.status_code, bool(r.json()['data']['token']))"
```

## 容器

```bash
docker build -t custody-service .
docker run --rm custody-service
```

## 保管位预约 API

保管柜（`/api/lockers/`）拥有确定容量，办案队伍按半开时间区间
`[start_time, end_time)` 预约容量；端点相接的区间互不冲突。

| 接口 | 说明 |
| --- | --- |
| `POST /api/lockers/` | 创建保管柜（code、capacity） |
| `GET /api/lockers/{id}/availability/?start_time=&end_time=` | 查询区间内分段可用容量与占用区间 |
| `POST /api/reservations/` | 预约容量（容量不足返回 **409**） |
| `POST /api/reservations/{id}/reschedule/` | 改期：先验证新时段，通过后才释放旧时段 |
| `POST /api/reservations/{id}/cancel/` | 取消（body 可带 `quantity` 部分取消） |
| `POST /api/reservations/{id}/consume/` | 物资送达、消费预约为占用（支持部分） |
| `POST /api/reservations/{id}/release/` | 出库释放占用（支持部分释放） |
| `POST /api/reservations/expire/` | 立即执行一次过期回收 |
| `POST /api/stock-in/` | 正式入库，**必须提供有效 `reservation`**，与消费原子提交 |

过期回收也可定时执行（`apps.reports.cron.expire_reservations`）或用命令：

```bash
python manage.py expire_reservations
```

### 冲突响应（HTTP 409）

容量不足（含并发申请竞争、到期后的重试）时返回 409，`data` 内含值班员
可直接使用的明细：

```json
{
  "success": false,
  "code": 409,
  "message": "保管柜 C-01 在所选时段容量不足，窗口内最少剩余 2.00，需要 5.00",
  "data": {
    "capacity": "10.00",
    "min_available_capacity": "2.00",
    "conflicts": [{"reservation_no": "R…", "team": "一队", "start_time": "…", "end_time": "…", "quantity": "8.00"}],
    "busy_periods": [{"start_time": "…", "end_time": "…", "used_capacity": "8.00", "available_capacity": "2.00"}],
    "availability": [{"start_time": "…", "end_time": "…", "used_capacity": "8.00", "available_capacity": "2.00"}]
  }
}
```

`availability` 为扫描线计算出的「使用量恒定」分段，区间部分重叠时仍能
给出每段的真实剩余容量。并发安全由每柜一行的行锁 + 整事务退避重试保证，
部分取消/消费/释放另带数量下限的条件更新，任何情况下总持有量不会超过容量。

