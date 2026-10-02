# 监管物资保管服务

该项目为监管仓、证物室和受控物资保管点提供服务端 API，覆盖人员授权、物资分类、批次登记、收发记录、审批、预警、审计日志与统计报表。数据保存在 SQLite，所有测试和接口验收均可在单个 Linux 应用容器内离线完成。

## 保管位预约

保管位（`StorageLocation`）带容量，办案队伍按时间区间预约容量，避免多支队伍看到同一空位被重复承诺：

- `POST /api/storage-locations/` 登记保管位；`GET /api/storage-locations/{id}/availability/?start=&end=&quantity=` 查询时段内的剩余容量与冲突区间。
- `POST /api/storage-reservations/` 预约。容量不足返回 409，`data.conflicts` 给出冲突的具体区间，`data.remaining` 给出仍可使用的容量。
- `POST /api/storage-reservations/{id}/release/` 释放；传 `quantity` 为部分取消，缺省整体释放；已占用只能整体释放。
- `POST /api/storage-reservations/{id}/reschedule/` 改期。先在事务内验证新时段容量，通过后才释放旧时段，失败时原预约保持不变。
- `POST /api/stock-in/`（或 `POST /api/storage-reservations/{id}/occupy/`）正式入库，必须消费一个处于预约时段内的有效预约，入库后预约转为占用。
- 预约到期自动过期（懒过期 + `apps.reports.cron.expire_storage_reservations` 定时任务），容量随即释放，到期后的重试不会超出容量；逾期未释放的占用继续持有容量直至释放。

容量校验与写入在同一事务内完成，并以保管位行锁串行化并发申请，并发、部分取消与到期重试均不会超售。

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
