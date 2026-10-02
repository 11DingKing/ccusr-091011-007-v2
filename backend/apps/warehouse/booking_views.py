"""
保管位与预约管理视图

冲突响应统一为 HTTP 409，data 中携带：
    conflicts            与请求区间重叠的具体预约区间
    busy_periods         已被占用的时间段及用量
    availability         查询窗口的分段可用容量
    min_available_capacity 窗口内仍可使用的最小容量
"""
import logging
from decimal import Decimal

from django.db.models import Sum, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.exceptions import NotFoundException
from apps.core.response import success_response, error_response
from . import services
from .models import Locker, Reservation
from .serializers import (
    LockerSerializer, LockerCreateSerializer,
    ReservationSerializer, ReservationCreateSerializer,
    ReservationRescheduleSerializer, ReservationQuantitySerializer,
    AvailabilityQuerySerializer,
)

logger = logging.getLogger('apps')


def _annotate_usage(queryset):
    """一次性注解每个柜位「当前时刻」的已用容量，避免列表 N+1 查询"""
    now = timezone.now()
    holding = Q(
        reservations__status__in=Reservation.HOLDING_STATUSES,
        reservations__quantity__gt=Decimal('0'),
        reservations__start_time__lte=now,
        reservations__end_time__gt=now,
    )
    return queryset.annotate(_used_now=Coalesce(
        Sum('reservations__quantity', filter=holding), Decimal('0')
    ))


def _first_error(serializer):
    """提取序列化器的第一条错误信息（与现有视图风格一致）"""
    errors = serializer.errors
    value = list(errors.values())[0]
    if isinstance(value, dict):
        value = list(value.values())[0]
    if isinstance(value, list):
        value = value[0]
    return str(value)


def _paginate(request, queryset, serializer_cls):
    page = int(request.query_params.get('page', 1))
    page_size = int(request.query_params.get('page_size', 10))
    total = queryset.count()
    items = queryset[(page - 1) * page_size:page * page_size]
    return {
        'list': serializer_cls(items, many=True).data,
        'total': total, 'page': page, 'page_size': page_size,
    }


# ==================== 保管柜管理 ====================

class LockerListView(APIView):
    """保管柜列表 / 创建"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = _annotate_usage(Locker.objects.all()).order_by('code')
        status = request.query_params.get('status')
        keyword = request.query_params.get('keyword')
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
        if keyword:
            queryset = queryset.filter(code__icontains=keyword) | \
                       queryset.filter(name__icontains=keyword)
        return success_response(data=_paginate(request, queryset, LockerSerializer))

    def post(self, request):
        serializer = LockerCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data
        locker = Locker.objects.create(created_by=request.user, **data)
        logger.info('用户 %s 创建保管柜 %s', request.user.username, locker.code)
        return success_response(data=LockerSerializer(locker).data, message='创建成功')


class LockerAllView(APIView):
    """所有启用保管柜（下拉选择）"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lockers = _annotate_usage(Locker.objects.filter(is_active=True)).order_by('code')
        return success_response(data=LockerSerializer(lockers, many=True).data)


class LockerDetailView(APIView):
    """保管柜详情 / 更新 / 删除"""
    permission_classes = [IsAuthenticated]

    def _get(self, pk):
        try:
            return Locker.objects.get(pk=pk)
        except Locker.DoesNotExist:
            raise NotFoundException('保管柜不存在')

    def get(self, request, pk):
        return success_response(data=LockerSerializer(self._get(pk)).data)

    def put(self, request, pk):
        locker = self._get(pk)
        serializer = LockerCreateSerializer(data=request.data, context={'instance': locker})
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data
        for field in ('code', 'name', 'location', 'capacity', 'is_active'):
            setattr(locker, field, data[field])
        locker.save()
        logger.info('用户 %s 更新保管柜 %s', request.user.username, locker.code)
        return success_response(data=LockerSerializer(locker).data, message='更新成功')

    def delete(self, request, pk):
        locker = self._get(pk)
        if locker.reservations.exists():
            return error_response(message='该保管柜存在预约记录，无法删除（可停用）')
        code = locker.code
        locker.delete()
        logger.info('用户 %s 删除保管柜 %s', request.user.username, code)
        return success_response(message='删除成功')


class LockerAvailabilityView(APIView):
    """查询保管柜在给定时间区间内的占用与可用容量明细"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            locker = Locker.objects.get(pk=pk)
        except Locker.DoesNotExist:
            raise NotFoundException('保管柜不存在')
        serializer = AvailabilityQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        detail = services.retry_read(lambda: services.build_availability(
            locker,
            serializer.validated_data['start_time'],
            serializer.validated_data['end_time'],
        ))
        return success_response(data=detail)


# ==================== 预约管理 ====================

class ReservationListView(APIView):
    """预约列表 / 创建预约"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Reservation.objects.select_related('locker', 'applicant').all()
        params = request.query_params
        if params.get('locker'):
            queryset = queryset.filter(locker_id=params['locker'])
        if params.get('status'):
            queryset = queryset.filter(status=params['status'])
        if params.get('team'):
            queryset = queryset.filter(team__icontains=params['team'])
        if params.get('start'):
            queryset = queryset.filter(end_time__gte=params['start'])
        if params.get('end'):
            queryset = queryset.filter(start_time__lte=params['end'])
        queryset = queryset.order_by('-created_at')
        return success_response(data=_paginate(request, queryset, ReservationSerializer))

    def post(self, request):
        """创建预约；容量不足返回 409 及冲突区间与可用容量"""
        serializer = ReservationCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        data = serializer.validated_data
        reservation = services.create_reservation(
            locker_id=data['locker'],
            team=data['team'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            quantity=data['quantity'],
            applicant=request.user,
            grace_minutes=data.get('grace_minutes'),
            remark=data.get('remark', ''),
        )
        return success_response(data=ReservationSerializer(reservation).data, message='预约成功')


class ReservationDetailView(APIView):
    """预约详情"""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            reservation = Reservation.objects.select_related('locker').get(pk=pk)
        except Reservation.DoesNotExist:
            raise NotFoundException('预约不存在')
        return success_response(data=ReservationSerializer(reservation).data)


class ReservationRescheduleView(APIView):
    """预约改期：先验证新时段容量，通过后才释放旧时段"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = ReservationRescheduleSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        reservation = services.reschedule_reservation(
            pk,
            start_time=serializer.validated_data['start_time'],
            end_time=serializer.validated_data['end_time'],
        )
        return success_response(data=ReservationSerializer(reservation).data, message='改期成功')


class ReservationCancelView(APIView):
    """取消预约（可通过 quantity 部分取消）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = ReservationQuantitySerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        reservation = services.cancel_reservation(
            pk, quantity=serializer.validated_data.get('quantity')
        )
        return success_response(data=ReservationSerializer(reservation).data, message='取消成功')


class ReservationConsumeView(APIView):
    """消费预约（物资送达、正式占用柜位容量）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = ReservationQuantitySerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        reservation = services.consume_reservation(
            pk, quantity=serializer.validated_data.get('quantity')
        )
        return success_response(data=ReservationSerializer(reservation).data, message='入库占用成功')


class ReservationReleaseView(APIView):
    """释放占用（物资出库归还容量，可部分释放）"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = ReservationQuantitySerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(message=_first_error(serializer))
        reservation = services.release_reservation(
            pk, quantity=serializer.validated_data.get('quantity')
        )
        return success_response(data=ReservationSerializer(reservation).data, message='释放成功')


class ReservationExpireView(APIView):
    """手动触发预约过期回收（定时任务亦可调用）"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        expired = services.expire_due_reservations()
        return success_response(data={
            'expired_count': len(expired),
            'list': ReservationSerializer(expired, many=True).data,
        }, message=f'过期回收 {len(expired)} 条')
