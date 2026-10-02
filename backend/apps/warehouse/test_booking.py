"""
保管位预约：容量、时间区间、并发与生命周期测试
"""
import threading
from datetime import timedelta
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from apps.warehouse.models import (
    Locker, Reservation, StockIn, Unit, Category, Variety, Goods,
)
from apps.warehouse.services import (
    create_reservation, reschedule_reservation, cancel_reservation,
    consume_reservation, release_reservation, expire_due_reservations,
    build_availability,
)


def iso(dt):
    return dt.isoformat()


class BookingFixture:
    """无 TestCase 依赖的夹具，供 TransactionTestCase 组合使用"""
    def setUp(self):
        self.user = User.objects.create_user("booking-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="件", created_by=self.user)
        self.category = Category.objects.create(name="受控器材", unit=self.unit,
                                                created_by=self.user)
        self.variety = Variety.objects.create(name="记录终端", category=self.category,
                                              created_by=self.user)
        self.goods = Goods.objects.create(
            variety=self.variety, name="执法记录终端", code="DEV-BOOK-001",
            quantity=Decimal("0"), warning_threshold=Decimal("5"),
        )
        self.locker = Locker.objects.create(
            code='A-01', name='一号柜', capacity=Decimal('10'), created_by=self.user
        )
        self.now = timezone.now().replace(microsecond=0)
        self.start = self.now - timedelta(hours=1)
        self.end = self.now + timedelta(hours=2)

    def book(self, locker, start, end, qty, team='队伍'):
        return create_reservation(
            locker_id=locker.id, team=team, start_time=start, end_time=end,
            quantity=Decimal(str(qty)), applicant=self.user,
        )


class BookingTestCase(BookingFixture, TransactionTestCase):
    """需要跨连接可见夹具数据的用例（含并发线程）"""
    reset_sequences = True


class AvailabilityTest(BookingTestCase):
    def test_basic_booking_and_availability(self):
        r = self.book(self.locker, self.start, self.end, '6')
        detail = build_availability(self.locker, self.start, self.end)
        self.assertEqual(Decimal(detail['min_available_capacity']), Decimal('4'))
        self.assertEqual(len(detail['conflicts']), 1)
        self.assertEqual(detail['conflicts'][0]['reservation_id'], r.id)
        self.assertEqual(Decimal(detail['busy_periods'][0]['used_capacity']), Decimal('6'))

    def test_over_capacity_conflicts_with_specific_intervals(self):
        self.book(self.locker, self.start, self.end, '6', team='一队')
        resp = self.client.post('/api/reservations/', {
            'locker': self.locker.id, 'team': '二队',
            'start_time': iso(self.start), 'end_time': iso(self.end),
            'quantity': '5',
        }, format='json')
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertFalse(body['success'])
        data = body['data']
        # 值班员可获得冲突的具体区间
        self.assertEqual(data['conflicts'][0]['team'], '一队')
        self.assertEqual(Decimal(data['conflicts'][0]['quantity']), Decimal('6'))
        # 仍可使用的容量
        self.assertEqual(Decimal(data['min_available_capacity']), Decimal('4'))
        seg = data['availability'][0]
        self.assertEqual(Decimal(seg['used_capacity']), Decimal('6'))
        self.assertEqual(Decimal(seg['available_capacity']), Decimal('4'))

    def test_partial_overlap_reports_tightest_segment(self):
        self.book(self.locker, self.now, self.now + timedelta(hours=1), '8', team='一队')
        # 查询窗口只有后半段与预约重叠
        resp = self.client.post('/api/reservations/', {
            'locker': self.locker.id, 'team': '二队',
            'start_time': iso(self.now - timedelta(hours=1)),
            'end_time': iso(self.now + timedelta(hours=2)),
            'quantity': '5',
        }, format='json')
        self.assertEqual(resp.status_code, 409)
        data = resp.json()['data']
        self.assertEqual(Decimal(data['min_available_capacity']), Decimal('2'))
        used = {Decimal(seg['used_capacity']) for seg in data['availability']}
        self.assertIn(Decimal('0'), used)
        self.assertIn(Decimal('8'), used)

    def test_adjacent_intervals_do_not_conflict(self):
        # 半开区间：端点相接允许
        self.book(self.locker, self.start, self.end, '10', team='一队')
        r = self.book(self.locker, self.end, self.end + timedelta(hours=1), '10', team='二队')
        self.assertEqual(r.status, Reservation.STATUS_BOOKED)

    def test_fitting_remaining_capacity_succeeds(self):
        self.book(self.locker, self.start, self.end, '6', team='一队')
        r = self.book(self.locker, self.start, self.end, '4', team='二队')
        detail = build_availability(self.locker, self.start, self.end)
        self.assertEqual(Decimal(detail['min_available_capacity']), Decimal('0'))
        self.assertEqual(r.quantity, Decimal('4'))

    def test_invalid_window_rejected(self):
        resp = self.client.post('/api/reservations/', {
            'locker': self.locker.id, 'team': '二队',
            'start_time': iso(self.end), 'end_time': iso(self.start),
            'quantity': '1',
        }, format='json')
        self.assertEqual(resp.status_code, 400)


class ConcurrencyTest(BookingTestCase):
    """并发申请不能超出容量（共享缓存内存库会立刻报锁，由服务层退避重试串行化）"""

    def _concurrent_bookings(self, quantity, teams):
        results = []
        errors = []

        def worker(team):
            try:
                r = create_reservation(
                    locker_id=self.locker.id, team=team,
                    start_time=self.start, end_time=self.end,
                    quantity=Decimal(str(quantity)),
                )
                results.append((team, r.id))
            except Exception as exc:  # 记录每支队伍的结果
                errors.append((team, type(exc).__name__, getattr(exc, 'code', None)))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(t,)) for t in teams]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results, errors

    def test_parallel_requests_never_exceed_capacity(self):
        results, errors = self._concurrent_bookings('4', ['一队', '二队', '三队', '四队'])
        # 容量 10，每单 4：恰好 2 单成功，2 单 409
        self.assertEqual(len(results), 2, errors)
        self.assertTrue(all(code == 409 for _, _, code in errors))
        detail = build_availability(self.locker, self.start, self.end)
        self.assertEqual(Decimal(detail['min_available_capacity']), Decimal('2'))

    def test_parallel_small_requests_fill_exactly(self):
        teams = [f'队伍{i}' for i in range(10)]
        results, errors = self._concurrent_bookings('1', teams)
        # 核心不变量：任何情况下总持有量都不得超过容量
        total = sum(Reservation.objects.filter(locker=self.locker)
                    .values_list('quantity', flat=True))
        self.assertLessEqual(total, Decimal('10'), errors)
        # 整事务重试后，容量足够的申请全部成功
        self.assertEqual(len(results), 10, errors)
        self.assertEqual(errors, [])
        detail = build_availability(self.locker, self.start, self.end)
        self.assertEqual(Decimal(detail['min_available_capacity']), Decimal('0'))
        # 再多一单必定 409
        resp = self.client.post('/api/reservations/', {
            'locker': self.locker.id, 'team': '后来者',
            'start_time': iso(self.start), 'end_time': iso(self.end),
            'quantity': '1',
        }, format='json')
        self.assertEqual(resp.status_code, 409)


class LifecycleTest(BookingTestCase):
    def test_partial_cancel_frees_capacity_for_retry(self):
        r = self.book(self.locker, self.start, self.end, '8', team='一队')
        # 只剩 2，申请 4 失败
        resp = self.client.post('/api/reservations/', {
            'locker': self.locker.id, 'team': '二队',
            'start_time': iso(self.start), 'end_time': iso(self.end),
            'quantity': '4',
        }, format='json')
        self.assertEqual(resp.status_code, 409)

        # 一队部分取消 3
        cancel_reservation(r.id, Decimal('3'))
        r.refresh_from_db()
        self.assertEqual(r.quantity, Decimal('5'))
        self.assertEqual(r.booked_quantity, Decimal('5'))
        self.assertEqual(r.cancelled_quantity, Decimal('3'))

        # 二队重试：释放出的容量使其成功
        r2 = self.book(self.locker, self.start, self.end, '4', team='二队')
        self.assertEqual(r2.quantity, Decimal('4'))

    def test_cancel_beyond_booked_rejected(self):
        r = self.book(self.locker, self.start, self.end, '5', team='一队')
        with self.assertRaises(Exception):
            cancel_reservation(r.id, Decimal('6'))

    def test_full_cancel_is_terminal_and_holds_no_capacity(self):
        r = self.book(self.locker, self.start, self.end, '10', team='一队')
        cancel_reservation(r.id)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CANCELLED)
        self.assertEqual(r.quantity, Decimal('0'))
        detail = build_availability(self.locker, self.start, self.end)
        self.assertEqual(Decimal(detail['min_available_capacity']), Decimal('10'))
        # 终态不能再取消/消费/释放
        with self.assertRaises(Exception):
            cancel_reservation(r.id)

    def test_consume_and_partial_release(self):
        r = self.book(self.locker, self.start, self.end, '6', team='一队')
        consume_reservation(r.id, Decimal('4'))
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_BOOKED)
        self.assertEqual(r.booked_quantity, Decimal('2'))
        self.assertEqual(r.consumed_quantity, Decimal('4'))
        self.assertEqual(r.quantity, Decimal('6'))  # 总持有量不变

        # 释放超过已占用 -> 拒绝
        with self.assertRaises(Exception):
            release_reservation(r.id, Decimal('5'))

        release_reservation(r.id, Decimal('3'))
        r.refresh_from_db()
        self.assertEqual(r.consumed_quantity, Decimal('1'))
        self.assertEqual(r.quantity, Decimal('3'))

        release_reservation(r.id)
        r.refresh_from_db()
        # 仍有 2 待入库，预约仍是 booked
        self.assertEqual(r.status, Reservation.STATUS_BOOKED)
        self.assertEqual(r.quantity, Decimal('2'))

        # 剩余送达并释放后进入 released 终态
        consume_reservation(r.id)
        release_reservation(r.id)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_RELEASED)
        self.assertEqual(r.quantity, Decimal('0'))

    def test_reschedule_validates_new_window_before_releasing_old(self):
        other = self.book(self.locker, self.now + timedelta(hours=3),
                          self.now + timedelta(hours=5), '10', team='二队')
        r = self.book(self.locker, self.start, self.end, '10', team='一队')

        new_start = self.now + timedelta(hours=3)
        new_end = self.now + timedelta(hours=5)
        # 新时段冲突：旧时段必须保持不变
        with self.assertRaises(Exception):
            reschedule_reservation(r.id, start_time=new_start, end_time=new_end)
        r.refresh_from_db()
        self.assertEqual(r.start_time, self.start)
        self.assertEqual(r.end_time, self.end)

        # 改到空闲时段成功
        reschedule_reservation(
            r.id,
            start_time=self.now + timedelta(hours=6),
            end_time=self.now + timedelta(hours=8),
        )
        r.refresh_from_db()
        self.assertEqual(r.start_time, self.now + timedelta(hours=6))
        self.assertEqual(other.status, Reservation.STATUS_BOOKED)

    def test_reschedule_after_delivery_rejected(self):
        r = self.book(self.locker, self.start, self.end, '6', team='一队')
        consume_reservation(r.id, Decimal('2'))
        with self.assertRaises(Exception):
            reschedule_reservation(r.id, start_time=self.start,
                                   end_time=self.end + timedelta(hours=1))


class ExpiryTest(BookingTestCase):
    def test_expiry_reclaims_capacity_and_blocks_retries(self):
        past_start = self.now - timedelta(hours=3)
        past_end = self.now - timedelta(hours=1)
        r = self.book(self.locker, past_start, past_end, '8', team='一队')

        # 到期后重试消费被拒绝
        with self.assertRaises(Exception):
            consume_reservation(r.id, Decimal('8'))

        expired = expire_due_reservations(now=self.now)
        self.assertEqual(len(expired), 1)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_EXPIRED)
        self.assertEqual(r.booked_quantity, Decimal('0'))
        self.assertEqual(r.expired_quantity, Decimal('8'))
        self.assertEqual(r.quantity, Decimal('0'))

        # 容量已归还，新队伍可立即预约同一时段
        r2 = self.book(self.locker, past_start, past_end, '10', team='二队')
        self.assertEqual(r2.status, Reservation.STATUS_BOOKED)

    def test_expiry_is_idempotent(self):
        r = self.book(self.locker, self.now - timedelta(hours=3),
                      self.now - timedelta(hours=1), '5', team='一队')
        first = expire_due_reservations(now=self.now)
        second = expire_due_reservations(now=self.now)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        r.refresh_from_db()
        self.assertEqual(r.expired_quantity, Decimal('5'))

    def test_partial_expiry_keeps_consumed_capacity(self):
        r = self.book(self.locker, self.now - timedelta(hours=3),
                      self.now + timedelta(hours=3), '6', team='一队')
        consume_reservation(r.id, Decimal('2'))
        # 待入库的 4 已过 end（调整结束时间模拟）
        Reservation.objects.filter(pk=r.id).update(end_time=self.now - timedelta(minutes=1))
        expired = expire_due_reservations(now=self.now)
        self.assertEqual(len(expired), 1)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CONSUMED)
        self.assertEqual(r.booked_quantity, Decimal('0'))
        self.assertEqual(r.consumed_quantity, Decimal('2'))
        self.assertEqual(r.expired_quantity, Decimal('4'))
        self.assertEqual(r.quantity, Decimal('2'))

    def test_grace_period_allows_late_consume(self):
        r = create_reservation(
            locker_id=self.locker.id, team='一队',
            start_time=self.now - timedelta(hours=3),
            end_time=self.now - timedelta(minutes=30),
            quantity=Decimal('5'), grace_minutes=60, applicant=self.user,
        )
        # 已过 end 但在宽限期内
        self.assertFalse(r.is_expired(self.now))
        consume_reservation(r.id, Decimal('5'))
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CONSUMED)

    def test_management_command_and_cron(self):
        from django.core.management import call_command
        from io import StringIO
        self.book(self.locker, self.now - timedelta(hours=2),
                  self.now - timedelta(minutes=5), '3', team='一队')
        out = StringIO()
        call_command('expire_reservations', stdout=out)
        self.assertIn('共处理 1 条', out.getvalue())

        from apps.reports.cron import expire_reservations
        expire_reservations()  # 幂等，不应抛错


class StockInFlowTest(BookingTestCase):
    def _stock_in(self, reservation_id, quantity=None):
        payload = {'goods': self.goods.id, 'reservation': reservation_id}
        if quantity is not None:
            payload['quantity'] = str(quantity)
        return self.client.post('/api/stock-in/', payload, format='json')

    def test_formal_stock_in_requires_valid_reservation(self):
        resp = self.client.post('/api/stock-in/', {
            'goods': self.goods.id, 'quantity': '3',
        }, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_stock_in_consumes_reservation_and_writes_record(self):
        r = self.book(self.locker, self.start, self.end, '5', team='一队')
        before = self.goods.quantity
        resp = self._stock_in(r.id, '5')
        self.assertEqual(resp.status_code, 200, resp.json())
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CONSUMED)
        self.assertEqual(r.consumed_quantity, Decimal('5'))
        record = StockIn.objects.get(reservation=r)
        self.assertEqual(record.quantity, Decimal('5'))
        self.assertEqual(record.reservation.locker_id, self.locker.id)
        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, before + Decimal('5'))
        self.assertEqual(resp.json()['data']['reservation_no'], r.reservation_no)
        self.assertEqual(resp.json()['data']['locker_code'], 'A-01')

    def test_duplicate_stock_in_after_full_consume_rejected(self):
        r = self.book(self.locker, self.start, self.end, '3', team='一队')
        first = self._stock_in(r.id, '3')
        self.assertEqual(first.status_code, 200)
        second = self._stock_in(r.id, '3')
        self.assertEqual(second.status_code, 400)

    def test_stock_in_after_expiry_rejected_no_record(self):
        r = self.book(self.locker, self.now - timedelta(hours=3),
                      self.now - timedelta(minutes=1), '4', team='一队')
        resp = self._stock_in(r.id, '4')
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(StockIn.objects.filter(reservation=r).exists())
        # 409 明细仍给出可预约容量
        self.assertIn('availability', resp.json()['data'])

    def test_partial_stock_in_then_rest(self):
        r = self.book(self.locker, self.start, self.end, '6', team='一队')
        self.assertEqual(self._stock_in(r.id, '2').status_code, 200)
        r.refresh_from_db()
        self.assertEqual(r.booked_quantity, Decimal('4'))
        self.assertEqual(self._stock_in(r.id, '4').status_code, 200)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.STATUS_CONSUMED)
        self.assertEqual(StockIn.objects.filter(reservation=r).count(), 2)


class APISurfaceTest(BookingTestCase):
    def test_locker_crud_and_availability_endpoint(self):
        resp = self.client.post('/api/lockers/', {
            'code': 'B-02', 'name': '二号柜', 'capacity': '20',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        locker_id = resp.json()['data']['id']

        listing = self.client.get('/api/lockers/')
        self.assertEqual(listing.json()['data']['total'], 2)

        av = self.client.get(
            f'/api/lockers/{locker_id}/availability/',
            data={'start_time': iso(self.start), 'end_time': iso(self.end)},
        )
        self.assertEqual(av.status_code, 200)
        self.assertEqual(Decimal(av.json()['data']['min_available_capacity']),
                         Decimal('20'))

        # 有预约的柜位禁止删除
        self.book(self.locker, self.start, self.end, '1', team='一队')
        deleted = self.client.delete(f'/api/lockers/{self.locker.id}/')
        self.assertEqual(deleted.status_code, 400)

    def test_reservation_action_endpoints(self):
        r = self.book(self.locker, self.start, self.end, '4', team='一队')
        detail = self.client.get(f'/api/reservations/{r.id}/')
        self.assertEqual(detail.status_code, 200)

        rescheduled = self.client.post(
            f'/api/reservations/{r.id}/reschedule/',
            {'start_time': iso(self.start),
             'end_time': iso(self.end + timedelta(hours=2))}, format='json')
        self.assertEqual(rescheduled.status_code, 200)

        cancelled = self.client.post(f'/api/reservations/{r.id}/cancel/',
                                     {'quantity': '1'}, format='json')
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(Decimal(cancelled.json()['data']['cancelled_quantity']),
                         Decimal('1'))

        consumed = self.client.post(f'/api/reservations/{r.id}/consume/',
                                    {'quantity': '2'}, format='json')
        self.assertEqual(consumed.status_code, 200)
        self.assertEqual(Decimal(consumed.json()['data']['consumed_quantity']),
                         Decimal('2'))

        released = self.client.post(f'/api/reservations/{r.id}/release/',
                                    {'quantity': '2'}, format='json')
        self.assertEqual(released.status_code, 200)

    def test_requires_authentication(self):
        client = APIClient()
        self.assertEqual(client.get('/api/lockers/').status_code, 401)
        self.assertEqual(client.get('/api/reservations/').status_code, 401)
