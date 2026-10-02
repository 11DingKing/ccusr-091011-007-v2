"""
保管位预约服务层

并发与事务约定：
1. 过期处理（懒过期）在主事务外独立提交，避免状态变更随业务异常回滚；
2. 每个变更事务的首条语句必须是对保管位行的写操作——SQLite 下这等价于
   BEGIN IMMEDIATE，直接取得写锁并让并发事务排队（若先 SELECT 再写，
   锁升级会被 SQLite 立即判死锁返回 database is locked）；
   PostgreSQL/MySQL 下该行锁起到同样的串行化作用；
3. 锁顺序固定为 保管位 -> 预约记录，避免死锁；
4. 容量校验与写入在同一个事务内完成，杜绝"看到的空位被重复承诺"。
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Goods, StockIn, StorageLocation, StorageReservation

logger = logging.getLogger('apps')

# 逾期未释放的占用视为无限期持有容量
FAR_FUTURE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=dt_timezone.utc)

# 持有容量的状态
HOLDING_STATUSES = ('active', 'occupied')


class CapacityConflictError(Exception):
    """容量冲突异常，携带冲突区间与剩余容量详情"""

    def __init__(self, message, detail):
        self.message = message
        self.detail = detail
        super().__init__(message)


class ReservationStateError(Exception):
    """预约状态不允许当前操作"""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


def _lock_location(location_id, now):
    """
    在事务内锁定保管位并返回最新行。
    首条语句即写操作：SQLite 借此取得写锁（并发事务按 busy timeout 排队），
    PostgreSQL/MySQL 则是行级排他锁。
    """
    StorageLocation.objects.filter(pk=location_id).update(updated_at=now)
    return StorageLocation.objects.get(pk=location_id)


def expire_due_reservations(now=None, location_id=None):
    """
    过期处理：把结束时间已过的 active 预约标记为 expired，释放其容量。
    单条 UPDATE 自身即原子，独立提交，不随调用方事务回滚。
    返回过期数量。可传入 location_id 只处理单个保管位。
    """
    now = now or timezone.now()
    queryset = StorageReservation.objects.filter(status='active', end_time__lte=now)
    if location_id is not None:
        queryset = queryset.filter(location_id=location_id)
    count = queryset.update(status='expired', expired_at=now, updated_at=now)
    if count:
        logger.info(f"过期处理完成，标记过期预约: {count} 条")
    return count


def _get_reservation_or_raise(reservation_id):
    reservation = StorageReservation.objects.filter(pk=reservation_id).first()
    if reservation is None:
        raise StorageReservation.DoesNotExist(reservation_id)
    return reservation


def _holding_intervals(location_id, start, end, now, exclude_ids=()):
    """
    查询与 [start, end) 重叠的容量持有记录，返回 (预约, 有效开始, 有效结束) 列表。
    - active 预约：持有 [start_time, end_time)，已到期（即使尚未懒标记）不再持有；
    - occupied 占用：逾期未释放的，有效结束时间延伸至 FAR_FUTURE。
    """
    queryset = StorageReservation.objects.filter(
        Q(status='active', end_time__gt=now) | Q(status='occupied'),
        location_id=location_id,
        start_time__lt=end,
    ).exclude(pk__in=exclude_ids).order_by('start_time', 'id')

    holdings = []
    for reservation in queryset:
        effective_end = reservation.end_time
        if reservation.status == 'occupied' and effective_end <= now:
            effective_end = FAR_FUTURE
        if effective_end <= start:
            continue
        holdings.append((reservation, reservation.start_time, effective_end))
    return holdings


def _usage_events(holdings, start, end):
    """把持有记录裁剪到 [start, end) 并展开为 (时间点, 容量增减) 事件"""
    events = []
    for reservation, hold_start, hold_end in holdings:
        seg_start = max(hold_start, start)
        seg_end = min(hold_end, end)
        if seg_start >= seg_end:
            continue
        events.append((seg_start, reservation.quantity))
        events.append((seg_end, -reservation.quantity))
    events.sort()
    return events


def _conflict_segments(holdings, start, end, threshold):
    """
    扫描线算法：找出 [start, end) 内累计占用 >= threshold 的具体区间。
    返回 [{'start': ..., 'end': ..., 'used': ...}]，按时间排序。
    """
    segments = []
    current = 0
    previous = None
    for point, delta in _usage_events(holdings, start, end):
        if previous is not None and point > previous and current >= threshold:
            segments.append({'start': previous, 'end': point, 'used': current})
        current += delta
        previous = point
    return segments


def _peak_usage(holdings, start, end):
    """计算 [start, end) 内的峰值占用"""
    peak = 0
    current = 0
    for _, delta in _usage_events(holdings, start, end):
        current += delta
        peak = max(peak, current)
    return peak


def compute_availability(location, start, end, now, quantity=1, exclude_ids=()):
    """
    计算保管位在 [start, end) 内的可用情况。
    返回容量、剩余容量、冲突区间（放不下 quantity 的具体时段）与重叠预约明细。
    注意：调用方需已持有保管位行锁（或接受只读视图）。
    """
    holdings = _holding_intervals(location.id, start, end, now, exclude_ids=exclude_ids)

    # 放得下 quantity 的条件：所有区间占用 <= capacity - quantity
    threshold = location.capacity - quantity + 1
    if threshold > 0:
        conflicts = _conflict_segments(holdings, start, end, threshold)
    else:
        conflicts = [{'start': start, 'end': end, 'used': location.capacity}]
    peak = _peak_usage(holdings, start, end)

    return {
        'location': location.id,
        'location_name': location.name,
        'capacity': location.capacity,
        'window': {'start': start, 'end': end},
        'requested_quantity': quantity,
        'used_peak': peak,
        'remaining': max(location.capacity - peak, 0),
        'is_available': len(conflicts) == 0,
        'conflicts': conflicts,
        'holdings': [
            {
                'reservation_id': reservation.id,
                'team_name': reservation.team_name,
                'quantity': reservation.quantity,
                'status': reservation.status,
                'start_time': hold_start,
                'end_time': hold_end,
            }
            for reservation, hold_start, hold_end in holdings
        ],
    }


def get_availability(location_id, start, end, quantity=1):
    """可用性查询入口：先懒过期，再在锁内返回一致的容量视图"""
    now = timezone.now()
    expire_due_reservations(now=now, location_id=location_id)
    with transaction.atomic():
        location = _lock_location(location_id, now)
        return compute_availability(location, start, end, now, quantity=quantity)


def create_reservation(location, applicant, team_name, quantity, start, end, remark=''):
    """
    预约保管位容量。容量不足时抛出 CapacityConflictError，携带冲突详情。
    """
    now = timezone.now()
    if start >= end:
        raise ReservationStateError('结束时间必须晚于开始时间')
    if end <= now:
        raise ReservationStateError('预约时段已过期，无法预约')
    if quantity < 1:
        raise ReservationStateError('预约容量必须大于0')
    if not location.is_active:
        raise ReservationStateError('保管位已停用')
    if quantity > location.capacity:
        raise ReservationStateError(f'预约容量超出保管位总容量（{location.capacity}）')

    # 先过期处理（独立提交），再校验容量，保证到期重试能拿到释放出的容量
    expire_due_reservations(now=now, location_id=location.id)

    with transaction.atomic():
        locked_location = _lock_location(location.id, now)
        if not locked_location.is_active:
            raise ReservationStateError('保管位已停用')
        availability = compute_availability(locked_location, start, end, now, quantity=quantity)
        if not availability['is_available']:
            raise CapacityConflictError(
                f'保管位容量不足，时段内剩余容量 {availability["remaining"]}，申请 {quantity}',
                availability,
            )

        reservation = StorageReservation.objects.create(
            location=locked_location,
            applicant=applicant,
            team_name=team_name,
            quantity=quantity,
            start_time=start,
            end_time=end,
            remark=remark,
        )
        logger.info(
            f"创建预约: {locked_location.name} {team_name} "
            f"[{start} ~ {end}] 容量 {quantity}"
        )
        return reservation


def release_reservation(reservation_id, quantity=None):
    """
    释放预约。active 状态支持部分释放（按容量数量递减），
    occupied 状态仅允许整体释放（物资离场）。
    """
    now = timezone.now()
    reservation = _get_reservation_or_raise(reservation_id)
    expire_due_reservations(now=now, location_id=reservation.location_id)

    with transaction.atomic():
        _lock_location(reservation.location_id, now)
        reservation = StorageReservation.objects.select_for_update().get(pk=reservation_id)

        if reservation.status not in ('active', 'occupied'):
            raise ReservationStateError(f'当前状态（{reservation.get_status_display()}）不允许释放')

        if reservation.status == 'occupied':
            if quantity is not None and quantity < reservation.quantity:
                raise ReservationStateError('已占用的预约只能整体释放')
            reservation.status = 'released'
            reservation.released_at = now
            reservation.save(update_fields=['status', 'released_at', 'updated_at'])
            logger.info(f"释放占用: 预约 {reservation.id} 已整体释放")
            return reservation

        # active：支持部分取消
        if quantity is None or quantity >= reservation.quantity:
            reservation.status = 'released'
            reservation.released_at = now
            reservation.save(update_fields=['status', 'released_at', 'updated_at'])
            logger.info(f"释放预约: 预约 {reservation.id} 已整体释放")
        else:
            if quantity < 1:
                raise ReservationStateError('释放数量必须大于0')
            reservation.quantity -= quantity
            reservation.save(update_fields=['quantity', 'updated_at'])
            logger.info(f"部分释放: 预约 {reservation.id} 释放容量 {quantity}，剩余 {reservation.quantity}")
        return reservation


def reschedule_reservation(reservation_id, start, end, quantity=None):
    """
    改期：先在事务内验证新时段容量（排除自身），通过后才更新，
    旧时段的容量占用随记录更新一并转移，不会出现"旧时段已释放、新时段没约上"的空窗。
    """
    now = timezone.now()
    if start >= end:
        raise ReservationStateError('结束时间必须晚于开始时间')
    if end <= now:
        raise ReservationStateError('新时段已过期，无法改期')

    reservation = _get_reservation_or_raise(reservation_id)
    expire_due_reservations(now=now, location_id=reservation.location_id)

    with transaction.atomic():
        locked_location = _lock_location(reservation.location_id, now)
        reservation = StorageReservation.objects.select_for_update().get(pk=reservation_id)

        if reservation.status != 'active':
            raise ReservationStateError(f'当前状态（{reservation.get_status_display()}）不允许改期')

        new_quantity = quantity if quantity is not None else reservation.quantity
        if new_quantity < 1:
            raise ReservationStateError('预约容量必须大于0')
        if new_quantity > locked_location.capacity:
            raise ReservationStateError(f'预约容量超出保管位总容量（{locked_location.capacity}）')

        # 先验证新时段（排除自身占用），通过后才更新记录
        availability = compute_availability(
            locked_location, start, end, now,
            quantity=new_quantity, exclude_ids=(reservation.id,),
        )
        if not availability['is_available']:
            raise CapacityConflictError(
                f'新时段容量不足，时段内剩余容量 {availability["remaining"]}，申请 {new_quantity}',
                availability,
            )

        reservation.start_time = start
        reservation.end_time = end
        reservation.quantity = new_quantity
        reservation.save(update_fields=['start_time', 'end_time', 'quantity', 'updated_at'])
        logger.info(
            f"预约改期: 预约 {reservation.id} 改至 [{start} ~ {end}] 容量 {new_quantity}"
        )
        return reservation


def occupy_reservation(reservation_id, goods, operator, quantity, batch_no='', supplier='', remark=''):
    """
    占用（正式入库）：消费有效预约，生成入库记录并增加库存。
    预约必须处于 active 且当前时间在预约时段内。
    """
    now = timezone.now()
    reservation = _get_reservation_or_raise(reservation_id)
    expire_due_reservations(now=now, location_id=reservation.location_id)

    with transaction.atomic():
        locked_location = _lock_location(reservation.location_id, now)
        reservation = StorageReservation.objects.select_for_update().get(pk=reservation_id)

        if reservation.status != 'active':
            raise ReservationStateError(f'当前状态（{reservation.get_status_display()}）无法办理入库')
        if now < reservation.start_time:
            raise ReservationStateError('预约尚未生效，不能提前入库')
        if now >= reservation.end_time:
            raise ReservationStateError('预约已过期，请重新预约')

        locked_goods = Goods.objects.select_for_update().get(pk=goods.pk)
        locked_goods.quantity += quantity
        locked_goods.location = locked_location.name
        locked_goods.save(update_fields=['quantity', 'location', 'updated_at'])

        stock_in = StockIn.objects.create(
            goods=locked_goods,
            reservation=reservation,
            operator=operator,
            quantity=quantity,
            batch_no=batch_no,
            supplier=supplier,
            remark=remark,
        )

        reservation.status = 'occupied'
        reservation.occupied_at = now
        reservation.save(update_fields=['status', 'occupied_at', 'updated_at'])
        logger.info(
            f"正式入库: 预约 {reservation.id} 转占用，货物 {locked_goods.name} 数量 {quantity}"
        )
        return stock_in
