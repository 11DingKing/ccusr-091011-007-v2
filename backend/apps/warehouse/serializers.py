"""
仓库管理序列化器
"""
from decimal import Decimal
from django.utils import timezone
from rest_framework import serializers
from .models import Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval, Locker, Reservation


class UnitSerializer(serializers.ModelSerializer):
    """单位序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Unit
        fields = [
            'id', 'name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class UnitCreateSerializer(serializers.Serializer):
    """单位创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=5, required=True, error_messages={
        'required': '请输入单位名称',
        'blank': '单位名称不能为空',
        'min_length': '单位名称至少1个字',
        'max_length': '单位名称最多5个字',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Unit.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('单位名称已存在')
        else:
            if Unit.objects.filter(name=value).exists():
                raise serializers.ValidationError('单位名称已存在')
        return value


class CategorySerializer(serializers.ModelSerializer):
    """品类序列化器"""
    is_linked = serializers.BooleanField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    unit_name = serializers.CharField(source='unit.name', read_only=True)
    
    class Meta:
        model = Category
        fields = [
            'id', 'name', 'unit', 'unit_name', 'is_linked', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class CategoryCreateSerializer(serializers.Serializer):
    """品类创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=10, required=True, error_messages={
        'required': '请输入品类名称',
        'blank': '品类名称不能为空',
        'min_length': '品类名称至少1个字',
        'max_length': '品类名称最多10个字',
    })
    unit = serializers.IntegerField(required=True, error_messages={
        'required': '请选择单位',
    })
    
    def validate_name(self, value):
        instance = self.context.get('instance')
        if instance:
            if Category.objects.filter(name=value).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('品类名称已存在')
        else:
            if Category.objects.filter(name=value).exists():
                raise serializers.ValidationError('品类名称已存在')
        return value
    
    def validate_unit(self, value):
        if not Unit.objects.filter(pk=value).exists():
            raise serializers.ValidationError('单位不存在')
        return value


class VarietySerializer(serializers.ModelSerializer):
    """品种序列化器"""
    is_in_stock = serializers.BooleanField(read_only=True)
    unit_name = serializers.CharField(read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    
    class Meta:
        model = Variety
        fields = [
            'id', 'name', 'category', 'category_name', 'unit_name',
            'is_in_stock', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class VarietyCreateSerializer(serializers.Serializer):
    """品种创建序列化器"""
    name = serializers.CharField(min_length=1, max_length=20, required=True, error_messages={
        'required': '请输入品种名称',
        'blank': '品种名称不能为空',
        'min_length': '品种名称至少1个字',
        'max_length': '品种名称最多20个字',
    })
    category = serializers.IntegerField(required=True, error_messages={
        'required': '请选择品类',
    })
    
    def validate_category(self, value):
        if not Category.objects.filter(pk=value).exists():
            raise serializers.ValidationError('品类不存在')
        return value
    
    def validate(self, data):
        instance = self.context.get('instance')
        name = data['name']
        category_id = data['category']
        
        if instance:
            if Variety.objects.filter(name=name, category_id=category_id).exclude(pk=instance.pk).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        else:
            if Variety.objects.filter(name=name, category_id=category_id).exists():
                raise serializers.ValidationError('该品类下已存在同名品种')
        return data


class GoodsSerializer(serializers.ModelSerializer):
    """货物序列化器"""
    variety_name = serializers.CharField(source='variety.name', read_only=True)
    category_name = serializers.CharField(source='variety.category.name', read_only=True)
    unit_name = serializers.CharField(source='variety.category.unit.name', read_only=True)
    is_warning = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = Goods
        fields = [
            'id', 'name', 'code', 'variety', 'variety_name',
            'category_name', 'unit_name', 'specification',
            'quantity', 'warning_threshold', 'location',
            'remark', 'is_active', 'is_warning',
            'created_at', 'updated_at'
        ]


class StockInSerializer(serializers.ModelSerializer):
    """入库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    reservation_no = serializers.CharField(source='reservation.reservation_no', read_only=True)
    locker = serializers.IntegerField(source='reservation.locker_id', read_only=True)
    locker_code = serializers.CharField(source='reservation.locker.code', read_only=True)

    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'quantity', 'reservation', 'reservation_no', 'locker', 'locker_code',
            'batch_no', 'supplier', 'stock_in_time', 'remark'
        ]


class StockOutSerializer(serializers.ModelSerializer):
    """出库记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = StockOut
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'receiver', 'receiver_dept', 'quantity', 'status', 'status_display',
            'stock_out_time', 'remark', 'created_at'
        ]


class WarningSerializer(serializers.ModelSerializer):
    """预警记录序列化器"""
    goods_name = serializers.CharField(source='goods.name', read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    
    class Meta:
        model = Warning
        fields = [
            'id', 'goods', 'goods_name', 'type', 'type_display',
            'message', 'is_read', 'created_at'
        ]


class ApprovalSerializer(serializers.ModelSerializer):
    """审批记录序列化器"""
    approver_name = serializers.CharField(source='approver.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Approval
        fields = [
            'id', 'stock_out', 'approver', 'approver_name',
            'status', 'status_display', 'remark', 'created_at', 'updated_at'
        ]


# ==================== 保管位与预约序列化器 ====================

class LockerSerializer(serializers.ModelSerializer):
    """保管柜序列化器，附带当前时刻的占用情况"""
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    used_capacity = serializers.SerializerMethodField()
    available_capacity = serializers.SerializerMethodField()

    class Meta:
        model = Locker
        fields = [
            'id', 'code', 'name', 'location', 'capacity', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'used_capacity', 'available_capacity',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def _used_now(self, obj):
        annotated = getattr(obj, '_used_now', None)
        if annotated is not None:
            return annotated
        if not hasattr(obj, '_used_cache'):
            from .services import current_usage
            obj._used_cache = current_usage(obj, timezone.now())
        return obj._used_cache

    def get_used_capacity(self, obj):
        return str(self._used_now(obj))

    def get_available_capacity(self, obj):
        value = obj.capacity - self._used_now(obj)
        return str(value if value > Decimal('0') else Decimal('0'))


class LockerCreateSerializer(serializers.Serializer):
    """保管柜创建/更新序列化器"""
    code = serializers.CharField(min_length=1, max_length=50, required=True, error_messages={
        'required': '请输入柜位编号', 'blank': '柜位编号不能为空',
    })
    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    location = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    capacity = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'),
                                        required=True, error_messages={
        'required': '请输入柜位容量', 'min_value': '柜位容量必须大于0',
    })
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_code(self, value):
        instance = self.context.get('instance')
        qs = Locker.objects.filter(code=value)
        if instance:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError('柜位编号已存在')
        return value


class ReservationSerializer(serializers.ModelSerializer):
    """预约序列化器"""
    locker_code = serializers.CharField(source='locker.code', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    consume_deadline = serializers.DateTimeField(read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'reservation_no', 'locker', 'locker_code', 'team',
            'applicant', 'applicant_name',
            'start_time', 'end_time', 'consume_deadline',
            'quantity', 'initial_quantity', 'booked_quantity',
            'consumed_quantity', 'cancelled_quantity',
            'released_quantity', 'expired_quantity',
            'grace_minutes', 'status', 'status_display', 'is_expired',
            'consumed_at', 'released_at', 'expired_time',
            'remark', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_is_expired(self, obj):
        return obj.is_expired() if obj.booked_quantity > 0 else False


class ReservationCreateSerializer(serializers.Serializer):
    """预约创建序列化器"""
    locker = serializers.IntegerField(required=True, error_messages={'required': '请选择保管柜'})
    team = serializers.CharField(min_length=1, max_length=100, required=True, error_messages={
        'required': '请填写办案队伍', 'blank': '办案队伍不能为空',
    })
    start_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择预约开始时间', 'invalid': '开始时间格式错误',
    })
    end_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择预约结束时间', 'invalid': '结束时间格式错误',
    })
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2,
                                        min_value=Decimal('0.01'), required=True, error_messages={
        'required': '请填写预约容量', 'min_value': '预约容量必须大于0',
    })
    grace_minutes = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    remark = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['end_time'] <= data['start_time']:
            raise serializers.ValidationError('预约结束时间必须晚于开始时间')
        if not Locker.objects.filter(pk=data['locker']).exists():
            raise serializers.ValidationError({'locker': '保管柜不存在'})
        return data


class ReservationRescheduleSerializer(serializers.Serializer):
    """预约改期序列化器"""
    start_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择新的开始时间', 'invalid': '开始时间格式错误',
    })
    end_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择新的结束时间', 'invalid': '结束时间格式错误',
    })

    def validate(self, data):
        if data['end_time'] <= data['start_time']:
            raise serializers.ValidationError('预约结束时间必须晚于开始时间')
        return data


class ReservationQuantitySerializer(serializers.Serializer):
    """按数量部分操作序列化器（取消/入库/释放）"""
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2,
                                        min_value=Decimal('0.01'), required=False,
                                        error_messages={'min_value': '数量必须大于0'})


class AvailabilityQuerySerializer(serializers.Serializer):
    """可用容量查询参数序列化器"""
    start_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请提供开始时间 start_time', 'invalid': '开始时间格式错误',
    })
    end_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请提供结束时间 end_time', 'invalid': '结束时间格式错误',
    })

    def validate(self, data):
        if data['end_time'] <= data['start_time']:
            raise serializers.ValidationError('结束时间必须晚于开始时间')
        return data


class StockInCreateSerializer(serializers.Serializer):
    """正式入库序列化器：必须提供并消费有效预约"""
    goods = serializers.IntegerField(required=True, error_messages={'required': '请选择货物'})
    reservation = serializers.IntegerField(required=True, error_messages={
        'required': '正式入库必须提供有效预约',
    })
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2,
                                        min_value=Decimal('0.01'), required=False,
                                        error_messages={'min_value': '入库数量必须大于0'})
    batch_no = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    supplier = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    remark = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value).exists():
            raise serializers.ValidationError('货物不存在')
        return value

    def validate_reservation(self, value):
        if not Reservation.objects.filter(pk=value).exists():
            raise serializers.ValidationError('预约不存在')
        return value
