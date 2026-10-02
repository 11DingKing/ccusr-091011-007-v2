"""
保管位预约领域服务

容量模型
========
保管柜 Locker 有确定容量 capacity。每条有效预约 Reservation 在其时间区间
[start_time, end_time)（半开区间，端点相接不冲突）内持有 quantity 的容量。
任意时刻 t，容量约束为：

    sum(r.quantity for r in holding if r.start_time <= t < r.end_time) <= capacity

holding 状态为 booked（已预约）与 consumed（已占用），二者都持有容量；
cancelled / released / expired 为终态，不再参与容量汇总。

并发控制
========
SQLite 不支持 SELECT ... FOR UPDATE，因此每个保管柜配有一行 LockerLock。
所有改变容量的操作进入事务后第一条语句必须是对该行的条件 UPDATE
（``version = version + 1``）：

* SQLite：写事务持有数据库级保留锁，第二个写者在 busy timeout 内等待；
* MySQL/PostgreSQL：行级写锁使同柜写者串行，后来者在前者提交后才能读到
  其新写入的预约（持锁后再建立一致性读视图）。

串行化后，重叠区间汇总与写入在同一事务内完成，重复承诺不可能发生。
部分取消/释放/消费使用带数量下限的条件 UPDATE 作为第二道防线：
若并发下余额不足，语句影响 0 行，调用方收到 409 并可用返回的最新容量重试。
"""
import logging
import random
import time
from decimal import Decimal

from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from apps.core.exceptions import BusinessException, NotFoundException, ConflictException
from .models import Locker, LockerLock, Reservation, StockIn, Goods

logger = logging.getLogger('apps')

ZERO = Decimal('0')

# 锁冲突时的重试参数
MAX_LOCK_RETRIES = 20
_LOCK_ERROR_MARKERS = ('locked', 'busy', 'deadlock', 'lock wait timeout')


# ==================== 锁与基础校验 ====================

def _is_lock_error(exc):
    return any(m in str(exc).lower() for m in _LOCK_ERROR_MARKERS)


def _retry_db_errors(action):
    """执行无参可调用对象，遇到 SQLite 锁冲突/死锁时整体退避重试。

    只能包裹完整事务（或只读查询），不能在已打开的事务内重试，
    否则睡眠期间会持续持有锁。
    """
    for attempt in range(MAX_LOCK_RETRIES):
        try:
            return action()
        except DatabaseError as exc:
            if not _is_lock_error(exc):
                raise
            if attempt >= MAX_LOCK_RETRIES - 1:
                raise ConflictException('保管位繁忙，请稍后重试')
            time.sleep(min(0.05 * (attempt + 1), 0.3) + random.uniform(0, 0.02))
    raise ConflictException('保管位繁忙，请稍后重试')


def with_locker_lock(locker_id, operation):
    """按保管柜 ID 加串行锁并在事务内执行 operation()，返回其结果。

    取锁（含锁行的 get_or_create）与业务读取都放在同一个可重试的
    事务闭包内：共享缓存 SQLite 下读也可能立刻报 locked，因此连柜位
    查找一并享受退避重试。MySQL/PostgreSQL 上锁行为行级写锁，
    重试路径基本不会触发。
    """
    def txn():
        with transaction.atomic():
            lock, _ = LockerLock.objects.get_or_create(locker_id=locker_id)
            LockerLock.objects.filter(pk=lock.pk).update(version=F('version') + 1)
            return operation()

    return _retry_db_errors(txn)


def with_reservation_lock(reservation_id, operation):
    """定位预约、锁定其保管柜后执行 operation(current_reservation)。

    预约不存在抛 404；读取与取锁均在可重试事务内。
    """
    def txn():
        with transaction.atomic():
            try:
                current = Reservation.objects.select_related('locker').get(pk=reservation_id)
            except Reservation.DoesNotExist:
                raise NotFoundException('预约不存在')
            lock, _ = LockerLock.objects.get_or_create(locker_id=current.locker_id)
            LockerLock.objects.filter(pk=lock.pk).update(version=F('version') + 1)
            # 取锁后重新读取预约，保证容量判断基于最新提交数据
            current = Reservation.objects.select_related('locker').get(pk=reservation_id)
            return operation(current)

    return _retry_db_errors(txn)


def retry_read(action):
    """只读查询的锁冲突重试（供可用性查询等读接口使用）。"""
    return _retry_db_errors(action)


def _validate_window(start_time, end_time):
    if start_time is None or end_time is None:
        raise BusinessException('请提供预约开始与结束时间')
    if end_time <= start_time:
        raise BusinessException('预约结束时间必须晚于开始时间')


def _get_locker(locker_id):
    try:
        locker = Locker.objects.get(pk=locker_id)
    except Locker.DoesNotExist:
        raise NotFoundException('保管柜不存在')
    if not locker.is_active:
        raise BusinessException('该保管柜已停用')
    return locker


# ==================== 容量计算 ====================

def _holding_intervals(locker, start, end, exclude_id=None):
    """读取与 [start, end) 相交、当前仍持有容量的预约区间。"""
    qs = Reservation.objects.filter(
        locker=locker,
        status__in=Reservation.HOLDING_STATUSES,
        quantity__gt=ZERO,
        start_time__lt=end,
        end_time__gt=start,
    ).order_by('start_time', 'id')
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return list(qs)


def current_usage(locker, now=None):
    """某一时刻保管柜已被预约/占用的容量。"""
    from django.db.models import Sum
    now = now or timezone.now()
    total = Reservation.objects.filter(
        locker=locker,
        status__in=Reservation.HOLDING_STATUSES,
        quantity__gt=ZERO,
        start_time__lte=now,
        end_time__gt=now,
    ).aggregate(total=Sum('quantity'))['total']
    return total or ZERO


def _sweep_segments(intervals, win_start, win_end):
    """扫描线算法：把重叠区间合并成「使用量恒定」的时间段。

    返回 [(segment_start, segment_end, used_quantity), ...]，覆盖整个查询窗口。
    同一时刻结束(-)排在开始(+)之前，契合半开区间语义。
    """
    events = []
    for r in intervals:
        seg_start = r.start_time if r.start_time > win_start else win_start
        seg_end = r.end_time if r.end_time < win_end else win_end
        if seg_start < seg_end:
            events.append((seg_start, r.quantity))
            events.append((seg_end, -r.quantity))
    events.sort(key=lambda e: (e[0], e[1]))

    segments = []
    used = ZERO
    cursor = win_start
    for moment, delta in events:
        if moment > cursor:
            segments.append((cursor, moment, used))
        used += delta
        cursor = moment
    if cursor < win_end:
        segments.append((cursor, win_end, used))
    if not segments:
        segments.append((win_start, win_end, ZERO))
    # 数值保护：使用量不应为负
    return [(s, e, u if u > ZERO else ZERO) for s, e, u in segments]


def build_availability(locker, start, end, exclude_id=None, requested_quantity=None):
    """构造容量明细：冲突区间、分段可用量、窗口内最小剩余容量。

    供预约/改期失败时返回 409 明细，也供可用性查询 API 直接使用。
    """
    _validate_window(start, end)
    intervals = _holding_intervals(locker, start, end, exclude_id=exclude_id)
    capacity = locker.capacity
    segments = _sweep_segments(intervals, start, end)

    availability = []
    busy_periods = []
    min_available = capacity
    for seg_start, seg_end, used in segments:
        available = capacity - used
        if available < ZERO:
            available = ZERO
        item = {
            'start_time': seg_start.isoformat(),
            'end_time': seg_end.isoformat(),
            'used_capacity': str(used),
            'available_capacity': str(available),
        }
        availability.append(item)
        if used > ZERO:
            busy_periods.append(item)
        if available < min_available:
            min_available = available

    conflicts = [{
        'reservation_id': r.id,
        'reservation_no': r.reservation_no,
        'team': r.team,
        'status': r.status,
        'status_display': r.get_status_display(),
        'start_time': r.start_time.isoformat(),
        'end_time': r.end_time.isoformat(),
        'quantity': str(r.quantity),
    } for r in intervals]

    return {
        'locker': locker.id,
        'locker_code': locker.code,
        'capacity': str(capacity),
        'requested': {
            'start_time': start.isoformat(),
            'end_time': end.isoformat(),
            'quantity': str(requested_quantity) if requested_quantity is not None else None,
        },
        'min_available_capacity': str(min_available),
        'conflicts': conflicts,
        'busy_periods': busy_periods,
        'availability': availability,
    }


def _ensure_fits(locker, start, end, quantity, exclude_id=None):
    """容量不足时抛出携带完整冲突明细的 409。"""
    detail = build_availability(
        locker, start, end, exclude_id=exclude_id, requested_quantity=quantity
    )
    if Decimal(detail['min_available_capacity']) < quantity:
        raise ConflictException(
            f"保管柜 {locker.code} 在所选时段容量不足，窗口内最少剩余 "
            f"{detail['min_available_capacity']}，需要 {quantity}",
            data=detail,
        )
    return detail


# ==================== 预约生命周期 ====================

def create_reservation(*, locker_id, team, start_time, end_time, quantity,
                       applicant=None, grace_minutes=None, remark=''):
    """预约保管位容量。容量不足（含并发申请）抛 409 并附冲突明细。"""
    _validate_window(start_time, end_time)
    if quantity is None or quantity <= ZERO:
        raise BusinessException('预约容量必须大于 0')
    locker = retry_read(lambda: _get_locker(locker_id))

    def operation():
        if not Locker.objects.filter(pk=locker.id, is_active=True).exists():
            raise BusinessException('该保管柜已停用')
        _ensure_fits(locker, start_time, end_time, quantity)
        return Reservation.objects.create(
            locker=locker,
            team=team,
            applicant=applicant,
            start_time=start_time,
            end_time=end_time,
            quantity=quantity,
            initial_quantity=quantity,
            booked_quantity=quantity,
            grace_minutes=grace_minutes,
            remark=remark or '',
        )

    reservation = with_locker_lock(locker.id, operation)
    logger.info(
        '预约创建 %s 柜位 %s [%s, %s) 容量 %s by %s',
        reservation.reservation_no, locker.code, start_time, end_time,
        quantity, getattr(applicant, 'username', 'system'),
    )
    return reservation


def reschedule_reservation(reservation_id, *, start_time, end_time):
    """改期：先在新时段上验证容量，通过后才释放旧时段。

    新时段验证失败时旧时段保持不变，调用方拿到新时段的冲突明细。
    """
    _validate_window(start_time, end_time)

    def operation(current):
        locker = current.locker
        if not locker.is_active:
            raise BusinessException('该保管柜已停用')
        if current.status not in Reservation.HOLDING_STATUSES or current.quantity <= ZERO:
            raise BusinessException('当前状态的预约不允许改期')
        if current.consumed_quantity > ZERO:
            raise BusinessException('已有物资入库占用，不能改期（可取消剩余未入库部分后重新预约）')
        # 关键顺序：新时段容量校验（排除自身）在前，旧时段释放在后
        _ensure_fits(locker, start_time, end_time, current.quantity,
                     exclude_id=current.id)
        current.start_time = start_time
        current.end_time = end_time
        current.save(update_fields=['start_time', 'end_time', 'updated_at'])
        return current

    reservation = with_reservation_lock(reservation_id, operation)
    logger.info('预约改期 %s -> [%s, %s)', reservation.reservation_no, start_time, end_time)
    return reservation


def cancel_reservation(reservation_id, quantity=None):
    """取消预约（可部分取消）。仅作用于尚未送达的 booked_quantity。

    已送达占用的容量须走 release。带数量下限的条件更新保证并发下不会超取消。
    """
    def operation(current):
        if current.status not in Reservation.HOLDING_STATUSES:
            raise BusinessException('当前状态的预约不允许取消')
        amount = current.booked_quantity if quantity is None else quantity
        if amount is None or amount <= ZERO:
            raise BusinessException('取消容量必须大于 0')
        if amount > current.booked_quantity:
            raise BusinessException(
                f'取消数量超过待入库容量，当前待入库 {current.booked_quantity}'
            )

        updated = Reservation.objects.filter(
            pk=current.id,
            status__in=Reservation.HOLDING_STATUSES,
            booked_quantity__gte=amount,
        ).update(
            booked_quantity=F('booked_quantity') - amount,
            quantity=F('quantity') - amount,
            cancelled_quantity=F('cancelled_quantity') + amount,
        )
        if updated != 1:
            raise ConflictException('预约状态已变化，请刷新后重试')

        current.refresh_from_db()
        if current.quantity <= ZERO:
            current.status = Reservation.STATUS_CANCELLED
        elif current.booked_quantity <= ZERO:
            current.status = Reservation.STATUS_CONSUMED
        current.save(update_fields=['status', 'updated_at'])
        return current, amount

    reservation, amount = with_reservation_lock(reservation_id, operation)
    logger.info('预约取消 %s 数量 %s，剩余持有 %s',
                reservation.reservation_no, amount, reservation.quantity)
    return reservation


def _consume_locked(reservation, quantity, now):
    """在已持有保管柜锁的事务内消费预约，返回 (预约, 本次消费数量)。"""
    if reservation.status == Reservation.STATUS_CANCELLED:
        raise BusinessException('预约已取消，无法入库')
    if reservation.status not in Reservation.HOLDING_STATUSES:
        raise BusinessException('当前状态的预约无法入库')
    if not reservation.is_valid_for_consume(now):
        if reservation.booked_quantity <= ZERO:
            raise BusinessException('预约容量已全部入库，请勿重复提交')
        window_end = max(now + timezone.timedelta(minutes=1), reservation.end_time)
        detail = build_availability(reservation.locker, now, window_end,
                                    exclude_id=reservation.id)
        raise ConflictException(
            f'预约已过期（入库截止 {reservation.consume_deadline():%Y-%m-%d %H:%M}），请重新预约',
            data=detail,
        )

    amount = reservation.booked_quantity if quantity is None else quantity
    if amount <= ZERO:
        raise BusinessException('入库容量必须大于 0')
    if amount > reservation.booked_quantity:
        raise BusinessException(
            f'入库数量超过预约待入库容量，当前待入库 {reservation.booked_quantity}'
        )

    updated = Reservation.objects.filter(
        pk=reservation.id,
        status__in=Reservation.HOLDING_STATUSES,
        booked_quantity__gte=amount,
    ).update(
        booked_quantity=F('booked_quantity') - amount,
        consumed_quantity=F('consumed_quantity') + amount,
    )
    if updated != 1:
        raise ConflictException('预约状态已变化，请刷新后重试')

    reservation.refresh_from_db()
    update_fields = ['updated_at']
    if reservation.consumed_at is None:
        reservation.consumed_at = now
        update_fields.append('consumed_at')
    if reservation.booked_quantity <= ZERO:
        reservation.status = Reservation.STATUS_CONSUMED
        update_fields.append('status')
    reservation.save(update_fields=update_fields)
    return reservation, amount


def consume_reservation(reservation_id, quantity=None, now=None):
    """正式入库时消费预约：booked 容量转为 consumed（物理占用），总持有量不变。

    仅当预约有效（未取消、有待入库容量、当前时间落在 [start, end+宽限] 内）
    时允许；到期后的重试会被拒绝，不可能靠重试突破容量。
    """
    now = now or timezone.now()
    reservation, amount = with_reservation_lock(
        reservation_id, lambda current: _consume_locked(current, quantity, now)
    )
    logger.info('预约消费 %s 入库数量 %s', reservation.reservation_no, amount)
    return reservation


def record_stock_in(*, goods, reservation_id, quantity, operator,
                    batch_no='', supplier='', remark=''):
    """正式入库：在保管柜锁事务内消费有效预约并写入入库记录。

    预约无效/过期/容量不足时不会产生任何入库记录；库存数量与预约消费
    原子提交，避免「有记录无柜位」。
    """
    now = timezone.now()

    def operation(current):
        current, amount = _consume_locked(current, quantity, now)
        stock_in = StockIn.objects.create(
            goods=goods,
            operator=operator,
            quantity=amount,
            reservation=current,
            batch_no=batch_no or '',
            supplier=supplier or '',
            remark=remark or '',
        )
        Goods.objects.filter(pk=goods.pk).update(quantity=F('quantity') + amount)
        return current, amount, stock_in

    reservation, amount, stock_in = with_reservation_lock(reservation_id, operation)
    logger.info('正式入库 货物 %s 数量 %s，消费预约 %s',
                goods.code, amount, reservation.reservation_no)
    return stock_in, reservation


def release_reservation(reservation_id, quantity=None):
    """释放已占用容量（物资出库归还），支持部分释放。"""
    def operation(current):
        if current.status not in Reservation.HOLDING_STATUSES:
            raise BusinessException('当前状态的预约不允许释放')
        amount = current.consumed_quantity if quantity is None else quantity
        if amount is None or amount <= ZERO:
            raise BusinessException('释放容量必须大于 0')
        if amount > current.consumed_quantity:
            raise BusinessException(
                f'释放数量超过已占用容量，当前已占用 {current.consumed_quantity}'
            )

        updated = Reservation.objects.filter(
            pk=current.id,
            status__in=Reservation.HOLDING_STATUSES,
            consumed_quantity__gte=amount,
        ).update(
            consumed_quantity=F('consumed_quantity') - amount,
            quantity=F('quantity') - amount,
            released_quantity=F('released_quantity') + amount,
            released_at=timezone.now(),
        )
        if updated != 1:
            raise ConflictException('预约状态已变化，请刷新后重试')

        current.refresh_from_db()
        if current.quantity <= ZERO:
            current.status = Reservation.STATUS_RELEASED
            current.save(update_fields=['status', 'updated_at'])
        return current, amount

    reservation, amount = with_reservation_lock(reservation_id, operation)
    logger.info('预约释放 %s 数量 %s，剩余持有 %s',
                reservation.reservation_no, amount, reservation.quantity)
    return reservation


def expire_due_reservations(now=None, locker=None):
    """过期处理：超过入库截止时间仍未送达的 booked 容量回收。

    按保管柜分组加锁，逐柜把到期的待入库容量转为 expired；
    若仍有已占用部分则状态回到 consumed（部分过期），否则置 expired。
    返回过期的预约列表（含部分过期），重复执行是幂等的。
    """
    now = now or timezone.now()

    def collect_ids():
        candidate = Reservation.objects.filter(
            status__in=Reservation.HOLDING_STATUSES,
            booked_quantity__gt=ZERO,
            end_time__lt=now,
        )
        if locker:
            candidate = candidate.filter(locker_id=locker.id)
        return sorted(candidate.values_list('locker_id', flat=True).distinct())

    locker_ids = retry_read(collect_ids)
    expired = []

    for current_locker_id in locker_ids:
        if not retry_read(lambda: Locker.objects.filter(pk=current_locker_id).exists()):
            continue

        def operation():
            reclaimed = []
            due = Reservation.objects.filter(
                locker_id=current_locker_id,
                status__in=Reservation.HOLDING_STATUSES,
                booked_quantity__gt=ZERO,
                end_time__lt=now,
            )
            for reservation in due:
                # Python 侧应用宽限期；取锁后读取的是最新提交数据
                if reservation.is_expired(now):
                    Reservation.objects.filter(
                        pk=reservation.id,
                        status__in=Reservation.HOLDING_STATUSES,
                        booked_quantity__gt=ZERO,
                    ).update(
                        quantity=F('quantity') - F('booked_quantity'),
                        expired_quantity=F('expired_quantity') + F('booked_quantity'),
                        booked_quantity=ZERO,
                        expired_time=now,
                    )
                    reservation.refresh_from_db()
                    reservation.status = (
                        Reservation.STATUS_EXPIRED if reservation.quantity <= ZERO
                        else Reservation.STATUS_CONSUMED
                    )
                    reservation.save(update_fields=['status', 'updated_at'])
                    reclaimed.append(reservation)
            return reclaimed

        expired.extend(with_locker_lock(current_locker_id, operation))
    if expired:
        logger.info('过期回收完成，处理预约 %s 条', len(expired))
    return expired
