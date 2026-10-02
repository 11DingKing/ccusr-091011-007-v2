"""
仓库管理序列化器
"""
from rest_framework import serializers
from .models import (
    Unit, Category, Variety, Goods, StockIn, StockOut, Warning, Approval,
    StorageLocation, StorageReservation,
)


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
    location_name = serializers.CharField(
        source='reservation.location.name', read_only=True, default=''
    )

    class Meta:
        model = StockIn
        fields = [
            'id', 'goods', 'goods_name', 'operator', 'operator_name',
            'reservation', 'location_name',
            'quantity', 'batch_no', 'supplier', 'stock_in_time', 'remark'
        ]


class StockInCreateSerializer(serializers.Serializer):
    """正式入库序列化器：必须消费一个有效预约"""
    reservation_id = serializers.IntegerField(required=True, error_messages={
        'required': '正式入库必须关联有效预约',
    })
    goods = serializers.IntegerField(required=True, error_messages={
        'required': '请选择货物',
    })
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=True,
        error_messages={'required': '请输入入库数量'},
    )
    batch_no = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    supplier = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    remark = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_goods(self, value):
        if not Goods.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('货物不存在或已停用')
        return value

    def validate_quantity(self, value):
        if value <= 0:
            raise serializers.ValidationError('入库数量必须大于0')
        return value


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


class StorageLocationSerializer(serializers.ModelSerializer):
    """保管位序列化器"""
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = StorageLocation
        fields = [
            'id', 'name', 'capacity', 'description', 'is_active',
            'created_by', 'created_by_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class StorageLocationCreateSerializer(serializers.Serializer):
    """保管位创建/更新序列化器"""
    name = serializers.CharField(min_length=1, max_length=50, required=True, error_messages={
        'required': '请输入保管位名称',
        'blank': '保管位名称不能为空',
        'max_length': '保管位名称最多50个字',
    })
    capacity = serializers.IntegerField(min_value=1, required=True, error_messages={
        'required': '请输入保管位容量',
        'min_value': '容量至少为1',
    })
    description = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_name(self, value):
        instance = self.context.get('instance')
        queryset = StorageLocation.objects.filter(name=value)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        if queryset.exists():
            raise serializers.ValidationError('保管位名称已存在')
        return value


class StorageReservationSerializer(serializers.ModelSerializer):
    """保管位预约序列化器"""
    location_name = serializers.CharField(source='location.name', read_only=True)
    applicant_name = serializers.CharField(source='applicant.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = StorageReservation
        fields = [
            'id', 'location', 'location_name', 'applicant', 'applicant_name',
            'team_name', 'quantity', 'start_time', 'end_time',
            'status', 'status_display',
            'occupied_at', 'released_at', 'expired_at',
            'remark', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class StorageReservationCreateSerializer(serializers.Serializer):
    """预约创建序列化器"""
    location = serializers.IntegerField(required=True, error_messages={
        'required': '请选择保管位',
    })
    team_name = serializers.CharField(min_length=1, max_length=100, required=True, error_messages={
        'required': '请输入办案队伍名称',
        'blank': '办案队伍名称不能为空',
        'max_length': '办案队伍名称最多100个字',
    })
    quantity = serializers.IntegerField(min_value=1, required=True, error_messages={
        'required': '请输入预约容量',
        'min_value': '预约容量至少为1',
    })
    start_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择开始时间',
    })
    end_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择结束时间',
    })
    remark = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_location(self, value):
        if not StorageLocation.objects.filter(pk=value).exists():
            raise serializers.ValidationError('保管位不存在')
        return value

    def validate(self, data):
        if data['start_time'] >= data['end_time']:
            raise serializers.ValidationError('结束时间必须晚于开始时间')
        return data


class StorageReservationReleaseSerializer(serializers.Serializer):
    """释放序列化器：quantity 缺省为整体释放，传入则为部分取消"""
    quantity = serializers.IntegerField(min_value=1, required=False, error_messages={
        'min_value': '释放数量至少为1',
    })


class StorageReservationRescheduleSerializer(serializers.Serializer):
    """改期序列化器"""
    start_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择新的开始时间',
    })
    end_time = serializers.DateTimeField(required=True, error_messages={
        'required': '请选择新的结束时间',
    })
    quantity = serializers.IntegerField(min_value=1, required=False, error_messages={
        'min_value': '预约容量至少为1',
    })

    def validate(self, data):
        if data['start_time'] >= data['end_time']:
            raise serializers.ValidationError('结束时间必须晚于开始时间')
        return data
