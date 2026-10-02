"""
库房管理模型
"""
import secrets
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from apps.authentication.models import User


class Unit(models.Model):
    """单位模型"""
    name = models.CharField('单位名称', max_length=5, unique=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_units', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_unit'
        verbose_name = '单位'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品类"""
        return self.categories.exists()


class Category(models.Model):
    """品类模型"""
    name = models.CharField('品类名称', max_length=10, unique=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT,
        related_name='categories', verbose_name='单位'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_categories', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_category'
        verbose_name = '品类'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_linked(self):
        """是否已关联至品种"""
        return self.varieties.exists()


class Variety(models.Model):
    """品种模型"""
    name = models.CharField('品种名称', max_length=20)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        related_name='varieties', verbose_name='所属品类'
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_varieties', verbose_name='创建人'
    )
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_variety'
        verbose_name = '品种'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        unique_together = ['category', 'name']
    
    def __str__(self):
        return f"{self.category.name} - {self.name}"
    
    @property
    def is_in_stock(self):
        """是否已入库"""
        return self.goods.exists()
    
    @property
    def unit_name(self):
        """获取单位名称"""
        return self.category.unit.name if self.category and self.category.unit else ''


class Goods(models.Model):
    """货物模型"""
    variety = models.ForeignKey(
        Variety, on_delete=models.CASCADE,
        related_name='goods', verbose_name='所属品种'
    )
    name = models.CharField('货物名称', max_length=200)
    code = models.CharField('货物编码', max_length=50, unique=True)
    specification = models.CharField('规格型号', max_length=200, blank=True)
    quantity = models.DecimalField('库存数量', max_digits=12, decimal_places=2, default=0)
    warning_threshold = models.DecimalField('预警阈值', max_digits=12, decimal_places=2, default=10)
    location = models.CharField('存放位置', max_length=100, blank=True)
    remark = models.TextField('备注', blank=True)
    is_active = models.BooleanField('是否启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)
    
    class Meta:
        db_table = 'wh_goods'
        verbose_name = '货物'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_warning(self):
        """是否预警"""
        return self.quantity <= self.warning_threshold


class StockIn(models.Model):
    """入库记录模型"""
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_ins', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_in_operations', verbose_name='操作人'
    )
    quantity = models.DecimalField('入库数量', max_digits=12, decimal_places=2)
    reservation = models.ForeignKey(
        'Reservation', on_delete=models.PROTECT, null=True, blank=True,
        related_name='stock_ins', verbose_name='消费的预约'
    )
    batch_no = models.CharField('批次号', max_length=50, blank=True)
    supplier = models.CharField('供应商', max_length=200, blank=True)
    stock_in_time = models.DateTimeField('入库时间', auto_now_add=True)
    remark = models.TextField('备注', blank=True)
    
    class Meta:
        db_table = 'wh_stock_in'
        verbose_name = '入库记录'
        verbose_name_plural = verbose_name
        ordering = ['-stock_in_time']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class StockOut(models.Model):
    """出库记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
        ('completed', '已完成'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='stock_outs', verbose_name='货物'
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='stock_out_operations', verbose_name='操作人'
    )
    receiver = models.CharField('领用人', max_length=100)
    receiver_dept = models.CharField('领用部门', max_length=100, blank=True)
    quantity = models.DecimalField('出库数量', max_digits=12, decimal_places=2)
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    stock_out_time = models.DateTimeField('出库时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_stock_out'
        verbose_name = '出库记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.quantity}"


class Warning(models.Model):
    """预警记录模型"""
    TYPE_CHOICES = [
        ('low_stock', '库存不足'),
        ('expiring', '即将过期'),
        ('expired', '已过期'),
    ]
    
    goods = models.ForeignKey(
        Goods, on_delete=models.CASCADE,
        related_name='warnings', verbose_name='货物'
    )
    type = models.CharField('预警类型', max_length=20, choices=TYPE_CHOICES)
    message = models.TextField('预警信息')
    is_read = models.BooleanField('是否已读', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    
    class Meta:
        db_table = 'wh_warning'
        verbose_name = '预警记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.goods.name} - {self.get_type_display()}"


class Approval(models.Model):
    """审批记录模型"""
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已通过'),
        ('rejected', '已拒绝'),
    ]

    stock_out = models.ForeignKey(
        StockOut, on_delete=models.CASCADE,
        related_name='approvals', verbose_name='出库记录'
    )
    approver = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='approvals', verbose_name='审批人'
    )
    status = models.CharField('审批状态', max_length=20, choices=STATUS_CHOICES, default='pending')
    remark = models.TextField('审批意见', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_approval'
        verbose_name = '审批记录'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.stock_out} - {self.get_status_display()}"


# ==================== 保管位预约模型 ====================

class Locker(models.Model):
    """保管柜（保管位）模型

    保管柜有确定的容量 capacity，多个办案队伍可在互不重叠的时间区间内
    共享同一容量。容量占用通过 Reservation 记录，任何时间点上
    「有效预约 + 已占用」的数量之和不得超过 capacity。
    """
    code = models.CharField('柜位编号', max_length=50, unique=True)
    name = models.CharField('柜位名称', max_length=100, blank=True)
    location = models.CharField('存放位置', max_length=100, blank=True)
    capacity = models.DecimalField('总容量', max_digits=12, decimal_places=2)
    is_active = models.BooleanField('是否启用', default=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='created_lockers', verbose_name='创建人'
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_locker'
        verbose_name = '保管柜'
        verbose_name_plural = verbose_name
        ordering = ['code']

    def __str__(self):
        return self.code

    @property
    def active_reservations(self):
        """当前仍持有容量的预约（预约中或已占用）"""
        return self.reservations.filter(status__in=[
            Reservation.STATUS_BOOKED, Reservation.STATUS_CONSUMED
        ])


@receiver(post_save, sender=Locker)
def ensure_locker_lock(sender, instance, created, **kwargs):
    """为每个保管柜建立专用行锁记录，写操作通过更新该行串行化"""
    if created:
        LockerLock.objects.get_or_create(locker=instance)


class LockerLock(models.Model):
    """保管柜行锁

    不保存业务数据，仅用于 SELECT ... FOR UPDATE / 条件更新，
    使同一保管柜上的容量写操作在数据库层串行化（含 SQLite/MySQL）。
    """
    locker = models.OneToOneField(
        Locker, on_delete=models.CASCADE,
        related_name='lock_row', verbose_name='保管柜'
    )
    version = models.PositiveBigIntegerField('版本号', default=0)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_locker_lock'
        verbose_name = '保管柜行锁'
        verbose_name_plural = verbose_name


class Reservation(models.Model):
    """保管位预约记录

    生命周期：
        booked   已预约（持有容量，物资未送达）
        consumed 已占用（物资正式入库时消费预约）
        released 已释放（占用结束，容量归还）
        cancelled 已取消（预约取消，容量归还；支持部分取消）
        expired  已过期（预约时间窗结束仍未入库，容量由过期任务回收）

    数量支持部分处理：
        booked_quantity    尚未送达、仍处于预约态的容量
        consumed_quantity  已正式入库（占用）的容量
        quantity           当前仍持有容量 = booked + consumed，容量校验只汇总该字段
    取消/释放/过期均按数量递减，支持部分取消与部分释放，处理轨迹通过
    cancelled_quantity / released_quantity / expired_quantity 留痕。
    """
    STATUS_BOOKED = 'booked'
    STATUS_CONSUMED = 'consumed'
    STATUS_RELEASED = 'released'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_BOOKED, '已预约'),
        (STATUS_CONSUMED, '已占用'),
        (STATUS_RELEASED, '已释放'),
        (STATUS_CANCELLED, '已取消'),
        (STATUS_EXPIRED, '已过期'),
    ]
    HOLDING_STATUSES = (STATUS_BOOKED, STATUS_CONSUMED)
    FINAL_STATUSES = (STATUS_RELEASED, STATUS_CANCELLED, STATUS_EXPIRED)

    locker = models.ForeignKey(
        Locker, on_delete=models.PROTECT,
        related_name='reservations', verbose_name='保管柜'
    )
    reservation_no = models.CharField('预约编号', max_length=40, unique=True, blank=True)
    team = models.CharField('办案队伍', max_length=100)
    applicant = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='reservations', verbose_name='预约人'
    )
    start_time = models.DateTimeField('预约开始时间')
    end_time = models.DateTimeField('预约结束时间')
    quantity = models.DecimalField('当前持有容量', max_digits=12, decimal_places=2)
    initial_quantity = models.DecimalField('预约容量', max_digits=12, decimal_places=2)
    booked_quantity = models.DecimalField('待入库容量', max_digits=12, decimal_places=2)
    consumed_quantity = models.DecimalField('已入库占用容量', max_digits=12, decimal_places=2, default=0)
    cancelled_quantity = models.DecimalField('累计取消容量', max_digits=12, decimal_places=2, default=0)
    released_quantity = models.DecimalField('累计释放容量', max_digits=12, decimal_places=2, default=0)
    expired_quantity = models.DecimalField('累计过期容量', max_digits=12, decimal_places=2, default=0)
    grace_minutes = models.PositiveIntegerField('入库宽限分钟数', null=True, blank=True,
                                                help_text='到期后再宽限多久仍可入库，为空则到期即过期')
    status = models.CharField('状态', max_length=20, choices=STATUS_CHOICES, default=STATUS_BOOKED)
    consumed_at = models.DateTimeField('首次占用时间', null=True, blank=True)
    released_at = models.DateTimeField('全部释放时间', null=True, blank=True)
    expired_time = models.DateTimeField('实际过期时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'wh_reservation'
        verbose_name = '保管位预约'
        verbose_name_plural = verbose_name
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['locker', 'status', 'start_time', 'end_time']),
            models.Index(fields=['status', 'end_time']),
        ]

    def __str__(self):
        return f"{self.reservation_no or '预约'} - {self.locker.code}"

    def save(self, *args, **kwargs):
        if not self.reservation_no:
            self.reservation_no = f"R{timezone.now():%Y%m%d%H%M%S}{secrets.token_hex(3).upper()}"
        super().save(*args, **kwargs)

    @property
    def is_holding(self):
        """是否仍持有容量"""
        return self.status in self.HOLDING_STATUSES

    def overlaps(self, start, end):
        """是否与给定时间区间相交（半开区间，端点相接不算冲突）"""
        return self.start_time < end and start < self.end_time

    def consume_deadline(self):
        """预约的入库截止时间（结束时间 + 宽限期）"""
        deadline = self.end_time
        if self.grace_minutes:
            deadline += timezone.timedelta(minutes=self.grace_minutes)
        return deadline

    def is_expired(self, now=None):
        """预约待入库部分是否已超过入库截止时间"""
        now = now or timezone.now()
        return now > self.consume_deadline()

    def is_valid_for_consume(self, now=None):
        """预约是否仍可用于正式入库：尚有待入库容量且未超过截止时间"""
        now = now or timezone.now()
        if self.status == self.STATUS_CANCELLED or self.booked_quantity <= 0:
            return False
        return self.start_time <= now <= self.consume_deadline()
