"""过期处理管理命令：回收超过入库截止时间仍未消费的预约容量。"""
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.management.base import BaseCommand
from apps.warehouse.services import expire_due_reservations


class Command(BaseCommand):
    help = '扫描并回收已到期但未消费的保管位预约容量'

    def add_arguments(self, parser):
        parser.add_argument('--now', default=None,
                            help='覆盖当前时间（ISO 格式，用于补偿执行/验证）')

    def handle(self, *args, **options):
        now = timezone.now()
        if options['now']:
            parsed = parse_datetime(options['now'])
            now = timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed

        expired = expire_due_reservations(now=now)
        for r in expired:
            self.stdout.write(self.style.SUCCESS(
                f'{r.reservation_no} 过期 {r.expired_quantity} 容量，状态 {r.status}'
            ))
        self.stdout.write(f'共处理 {len(expired)} 条到期预约')
