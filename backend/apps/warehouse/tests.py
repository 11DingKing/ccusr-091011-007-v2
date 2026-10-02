from decimal import Decimal

import threading
from datetime import timedelta

from django.db import IntegrityError, connection
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from apps.authentication.backends import generate_token
from apps.authentication.models import User
from . import services
from .models import (
    Approval, Category, Goods, StockIn, StockOut, Unit, Variety, Warning,
    StorageLocation, StorageReservation,
)


class WarehouseFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("warehouse-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="件", created_by=self.user)
        self.category = Category.objects.create(name="受控器材", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="记录终端", category=self.category, created_by=self.user)
        self.goods = Goods.objects.create(
            variety=self.variety,
            name="执法记录终端",
            code="DEV-001",
            quantity=Decimal("12"),
            warning_threshold=Decimal("5"),
        )


class WarehouseModelTest(WarehouseFixture):
    def test_relationship_flags(self):
        self.assertTrue(self.unit.is_linked)
        self.assertTrue(self.category.is_linked)
        self.assertTrue(self.variety.is_in_stock)
        self.assertFalse(self.goods.is_warning)

    def test_unique_unit_name(self):
        with self.assertRaises(IntegrityError):
            Unit.objects.create(name="件", created_by=self.user)

    def test_stock_records_and_approval(self):
        inbound = StockIn.objects.create(goods=self.goods, operator=self.user, quantity=Decimal("3"))
        outbound = StockOut.objects.create(
            goods=self.goods, operator=self.user, receiver="保管员", quantity=Decimal("2")
        )
        approval = Approval.objects.create(stock_out=outbound, approver=self.user)
        self.assertEqual(inbound.goods_id, self.goods.id)
        self.assertEqual(approval.status, "pending")

    def test_warning_record(self):
        warning = Warning.objects.create(goods=self.goods, type="low_stock", message="库存不足")
        self.assertFalse(warning.is_read)
        self.assertIn("执法记录终端", str(warning))


class WarehouseAPITest(WarehouseFixture):
    def test_list_units(self):
        response = self.client.get("/api/units/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total"], 1)

    def test_create_unit_and_reject_duplicate(self):
        created = self.client.post("/api/units/", {"name": "箱"}, format="json")
        duplicate = self.client.post("/api/units/", {"name": "箱"}, format="json")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)

    def test_update_linked_unit(self):
        response = self.client.put(f"/api/units/{self.unit.id}/", {"name": "台"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.name, "台")

    def test_refuse_delete_linked_unit(self):
        response = self.client.delete(f"/api/units/{self.unit.id}/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Unit.objects.filter(pk=self.unit.id).exists())

    def test_create_category_validates_unit(self):
        ok = self.client.post("/api/categories/", {"name": "封存介质", "unit": self.unit.id}, format="json")
        bad = self.client.post("/api/categories/", {"name": "无效分类", "unit": 99999}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 400)

    def test_create_variety_and_duplicate_boundary(self):
        ok = self.client.post("/api/varieties/", {"name": "封存硬盘", "category": self.category.id}, format="json")
        duplicate = self.client.post("/api/varieties/", {"name": "封存硬盘", "category": self.category.id}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)

    def test_requires_authentication(self):
        anonymous = APIClient().get("/api/units/")
        self.assertEqual(anonymous.status_code, 401)


class StorageFixture(TestCase):
    """保管位与预约测试基类"""

    def setUp(self):
        self.user = User.objects.create_user("storage-user", "testpass123", role="admin")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {generate_token(self.user)}")
        self.unit = Unit.objects.create(name="件", created_by=self.user)
        self.category = Category.objects.create(name="受控器材", unit=self.unit, created_by=self.user)
        self.variety = Variety.objects.create(name="记录终端", category=self.category, created_by=self.user)
        self.goods = Goods.objects.create(
            variety=self.variety,
            name="执法记录终端",
            code="DEV-001",
            quantity=Decimal("12"),
            warning_threshold=Decimal("5"),
        )
        self.location = StorageLocation.objects.create(
            name="A-01柜", capacity=2, created_by=self.user
        )
        self.now = timezone.now()

    def _window(self, start_hours, end_hours):
        return (
            self.now + timedelta(hours=start_hours),
            self.now + timedelta(hours=end_hours),
        )

    def _reserve_api(self, team, quantity, start, end, location=None):
        return self.client.post("/api/storage-reservations/", {
            "location": (location or self.location).id,
            "team_name": team,
            "quantity": quantity,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }, format="json")

    def _reserve(self, team, quantity, start, end, location=None):
        return services.create_reservation(
            location or self.location, self.user, team, quantity, start, end
        )


class StorageLocationAPITest(StorageFixture):
    def test_create_and_list_locations(self):
        created = self.client.post("/api/storage-locations/", {
            "name": "B-02柜", "capacity": 5, "description": "二层东侧",
        }, format="json")
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["data"]["capacity"], 5)

        listed = self.client.get("/api/storage-locations/")
        self.assertEqual(listed.json()["data"]["total"], 2)

    def test_reject_duplicate_name_and_invalid_capacity(self):
        duplicate = self.client.post("/api/storage-locations/", {
            "name": "A-01柜", "capacity": 1,
        }, format="json")
        self.assertEqual(duplicate.status_code, 400)

        bad_capacity = self.client.post("/api/storage-locations/", {
            "name": "C-03柜", "capacity": 0,
        }, format="json")
        self.assertEqual(bad_capacity.status_code, 400)

    def test_delete_blocked_by_active_reservation(self):
        start, end = self._window(1, 3)
        reservation = self._reserve("刑侦一队", 1, start, end)

        blocked = self.client.delete(f"/api/storage-locations/{self.location.id}/")
        self.assertEqual(blocked.status_code, 400)

        # 释放后历史预约记录仍在，为保证可追溯仍不可删除
        services.release_reservation(reservation.id)
        still_blocked = self.client.delete(f"/api/storage-locations/{self.location.id}/")
        self.assertEqual(still_blocked.status_code, 400)
        self.assertTrue(StorageLocation.objects.filter(pk=self.location.id).exists())

        # 从未产生预约的保管位可以删除
        fresh = StorageLocation.objects.create(name="临时柜", capacity=1, created_by=self.user)
        allowed = self.client.delete(f"/api/storage-locations/{fresh.id}/")
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(StorageLocation.objects.filter(pk=fresh.id).exists())

    def test_capacity_shrink_blocked_while_reserved(self):
        start, end = self._window(1, 3)
        self._reserve("刑侦一队", 1, start, end)

        response = self.client.put(f"/api/storage-locations/{self.location.id}/", {
            "name": "A-01柜", "capacity": 1,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.location.refresh_from_db()
        self.assertEqual(self.location.capacity, 2)


class StorageReservationAPITest(StorageFixture):
    def test_reserve_and_list(self):
        start, end = self._window(1, 3)
        response = self._reserve_api("刑侦一队", 1, start, end)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "active")

        listed = self.client.get("/api/storage-reservations/", {"status": "active"})
        self.assertEqual(listed.json()["data"]["total"], 1)

    def test_overlapping_reservation_conflict_returns_detail(self):
        start, end = self._window(1, 4)
        first = self._reserve_api("刑侦一队", 2, start, end)
        self.assertEqual(first.status_code, 200)

        conflict = self._reserve_api("经侦二队", 1, start, end)
        self.assertEqual(conflict.status_code, 409)
        body = conflict.json()
        self.assertIn("剩余容量", body["message"])
        detail = body["data"]
        self.assertEqual(detail["remaining"], 0)
        self.assertFalse(detail["is_available"])
        self.assertEqual(len(detail["conflicts"]), 1)
        self.assertEqual(parse_datetime(detail["conflicts"][0]["start"]), start)
        self.assertEqual(parse_datetime(detail["conflicts"][0]["end"]), end)
        self.assertEqual(detail["conflicts"][0]["used"], 2)

    def test_adjacent_windows_do_not_conflict(self):
        start, end = self._window(1, 3)
        self._reserve_api("刑侦一队", 2, start, end)

        # 紧接其后的时段 [end, end+2h) 与原预约首尾相接，不算重叠
        follow_up = self._reserve_api("经侦二队", 2, end, end + timedelta(hours=2))
        self.assertEqual(follow_up.status_code, 200)

    def test_partial_overlap_conflict_interval_is_precise(self):
        # 容量2：R1 [+1h,+3h] 占1，R2 [+2h,+4h] 占1 → 仅 [+2h,+3h] 满员
        r1_start, r1_end = self._window(1, 3)
        r2_start, r2_end = self._window(2, 4)
        self._reserve("刑侦一队", 1, r1_start, r1_end)
        self._reserve("经侦二队", 1, r2_start, r2_end)

        query_start, query_end = self._window(0, 5)
        response = self.client.get(
            f"/api/storage-locations/{self.location.id}/availability/",
            {"start": query_start.isoformat(), "end": query_end.isoformat()},
        )
        data = response.json()["data"]
        self.assertEqual(data["capacity"], 2)
        self.assertEqual(data["remaining"], 0)
        self.assertFalse(data["is_available"])
        self.assertEqual(len(data["conflicts"]), 1)
        self.assertEqual(parse_datetime(data["conflicts"][0]["start"]), r2_start)
        self.assertEqual(parse_datetime(data["conflicts"][0]["end"]), r1_end)
        self.assertEqual(len(data["holdings"]), 2)

        # 只查 [+3h,+4h] 时剩余容量为1，可以再放一个
        response = self.client.get(
            f"/api/storage-locations/{self.location.id}/availability/",
            {"start": r1_end.isoformat(), "end": r2_end.isoformat(), "quantity": 1},
        )
        data = response.json()["data"]
        self.assertTrue(data["is_available"])
        self.assertEqual(data["remaining"], 1)

    def test_reject_invalid_requests(self):
        start, end = self._window(1, 3)
        # 超出总容量
        over = self._reserve_api("刑侦一队", 3, start, end)
        self.assertEqual(over.status_code, 400)
        # 结束时间早于开始时间
        inverted = self._reserve_api("刑侦一队", 1, end, start)
        self.assertEqual(inverted.status_code, 400)
        # 已过去的时段
        past_start, past_end = self._window(-3, -1)
        past = self._reserve_api("刑侦一队", 1, past_start, past_end)
        self.assertEqual(past.status_code, 400)

    def test_partial_release_frees_capacity(self):
        start, end = self._window(1, 4)
        reservation = self._reserve("刑侦一队", 2, start, end)

        # 部分取消1个容量后，另一支队伍可以补上
        partial = self.client.post(
            f"/api/storage-reservations/{reservation.id}/release/",
            {"quantity": 1}, format="json",
        )
        self.assertEqual(partial.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "active")
        self.assertEqual(reservation.quantity, 1)

        second = self._reserve_api("经侦二队", 1, start, end)
        self.assertEqual(second.status_code, 200)

        # 容量再次占满，新的申请被拒
        third = self._reserve_api("技侦三队", 1, start, end)
        self.assertEqual(third.status_code, 409)

    def test_full_release_and_release_twice(self):
        start, end = self._window(1, 4)
        reservation = self._reserve("刑侦一队", 2, start, end)

        released = self.client.post(f"/api/storage-reservations/{reservation.id}/release/")
        self.assertEqual(released.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "released")
        self.assertIsNotNone(reservation.released_at)

        again = self.client.post(f"/api/storage-reservations/{reservation.id}/release/")
        self.assertEqual(again.status_code, 400)

        # 容量已完全释放
        refill = self._reserve_api("经侦二队", 2, start, end)
        self.assertEqual(refill.status_code, 200)

    def test_expired_reservation_frees_capacity_for_retry(self):
        start, end = self._window(1, 3)
        reservation = self._reserve("刑侦一队", 2, start, end)

        # 模拟时间流逝：预约窗口已过
        StorageReservation.objects.filter(pk=reservation.id).update(
            end_time=timezone.now() - timedelta(minutes=1)
        )

        # 详情接口触发懒过期
        detail = self.client.get(f"/api/storage-reservations/{reservation.id}/")
        self.assertEqual(detail.json()["data"]["status"], "expired")

        # 到期后的重试可以拿到释放出的容量
        retry_start, retry_end = self._window(1, 3)
        retry = self._reserve_api("经侦二队", 2, retry_start, retry_end)
        self.assertEqual(retry.status_code, 200)

    def test_cron_expire_task(self):
        start, end = self._window(1, 3)
        reservation = self._reserve("刑侦一队", 1, start, end)
        StorageReservation.objects.filter(pk=reservation.id).update(
            end_time=timezone.now() - timedelta(minutes=1)
        )

        from apps.reports.cron import expire_storage_reservations
        expire_storage_reservations()

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "expired")
        self.assertIsNotNone(reservation.expired_at)

    def test_reschedule_validates_new_window_before_releasing_old(self):
        other = StorageLocation.objects.create(name="B-01柜", capacity=1, created_by=self.user)
        r1_start, r1_end = self._window(1, 3)
        r2_start, r2_end = self._window(5, 7)
        blocker = self._reserve("刑侦一队", 1, r1_start, r1_end, location=other)
        mover = self._reserve("经侦二队", 1, r2_start, r2_end, location=other)

        # 新时段与刑侦一队冲突 → 409，旧时段必须保留
        conflict = self.client.post(
            f"/api/storage-reservations/{mover.id}/reschedule/",
            {"start_time": r1_start.isoformat(), "end_time": r1_end.isoformat()},
            format="json",
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(conflict.json()["data"]["conflicts"]), 1)
        mover.refresh_from_db()
        self.assertEqual(mover.status, "active")
        self.assertEqual(mover.start_time, r2_start)
        self.assertEqual(mover.end_time, r2_end)

        # 改到空时段成功，旧时段随即释放，可被他人预约
        new_start, new_end = self._window(3, 5)
        moved = self.client.post(
            f"/api/storage-reservations/{mover.id}/reschedule/",
            {"start_time": new_start.isoformat(), "end_time": new_end.isoformat()},
            format="json",
        )
        self.assertEqual(moved.status_code, 200)
        mover.refresh_from_db()
        self.assertEqual(mover.start_time, new_start)

        old_slot_reused = self._reserve_api("技侦三队", 1, r2_start, r2_end, location=other)
        self.assertEqual(old_slot_reused.status_code, 200)

    def test_reschedule_rejects_non_active(self):
        start, end = self._window(1, 3)
        reservation = self._reserve("刑侦一队", 1, start, end)
        services.release_reservation(reservation.id)

        new_start, new_end = self._window(4, 6)
        response = self.client.post(
            f"/api/storage-reservations/{reservation.id}/reschedule/",
            {"start_time": new_start.isoformat(), "end_time": new_end.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_overdue_occupation_still_holds_capacity(self):
        # 占用后逾期未释放，未来时段的容量仍被持有
        start, end = self._window(-1, 1)
        reservation = self._reserve("刑侦一队", 2, start, end)
        services.occupy_reservation(reservation.id, self.goods, self.user, Decimal("2"))
        StorageReservation.objects.filter(pk=reservation.id).update(
            end_time=timezone.now() - timedelta(minutes=1)
        )

        future_start, future_end = self._window(2, 4)
        blocked = self._reserve_api("经侦二队", 1, future_start, future_end)
        self.assertEqual(blocked.status_code, 409)

        # 释放占用后容量恢复
        self.client.post(f"/api/storage-reservations/{reservation.id}/release/")
        freed = self._reserve_api("经侦二队", 1, future_start, future_end)
        self.assertEqual(freed.status_code, 200)


class StockInReservationAPITest(StorageFixture):
    def _active_reservation(self, quantity=1):
        start, end = self._window(-1, 2)
        return self._reserve("刑侦一队", quantity, start, end)

    def test_stock_in_requires_reservation(self):
        response = self.client.post("/api/stock-in/", {
            "goods": self.goods.id, "quantity": "2",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("预约", response.json()["message"])

    def test_stock_in_consumes_reservation(self):
        reservation = self._active_reservation()
        response = self.client.post("/api/stock-in/", {
            "reservation_id": reservation.id,
            "goods": self.goods.id,
            "quantity": "4",
            "batch_no": "BATCH-01",
        }, format="json")
        self.assertEqual(response.status_code, 200)

        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "occupied")
        self.assertIsNotNone(reservation.occupied_at)

        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("16"))
        self.assertEqual(self.goods.location, "A-01柜")

        stock_in = StockIn.objects.get(reservation=reservation)
        self.assertEqual(stock_in.quantity, Decimal("4"))

        # 入库列表可查
        listed = self.client.get("/api/stock-in/")
        self.assertEqual(listed.json()["data"]["total"], 1)

    def test_stock_in_rejects_reused_reservation(self):
        reservation = self._active_reservation()
        payload = {
            "reservation_id": reservation.id,
            "goods": self.goods.id,
            "quantity": "1",
        }
        first = self.client.post("/api/stock-in/", payload, format="json")
        self.assertEqual(first.status_code, 200)

        second = self.client.post("/api/stock-in/", payload, format="json")
        self.assertEqual(second.status_code, 400)

    def test_stock_in_rejects_expired_reservation(self):
        reservation = self._active_reservation()
        StorageReservation.objects.filter(pk=reservation.id).update(
            end_time=timezone.now() - timedelta(minutes=1)
        )
        response = self.client.post("/api/stock-in/", {
            "reservation_id": reservation.id,
            "goods": self.goods.id,
            "quantity": "1",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "expired")

    def test_stock_in_rejects_early_arrival(self):
        start, end = self._window(2, 4)
        reservation = self._reserve("刑侦一队", 1, start, end)
        response = self.client.post("/api/stock-in/", {
            "reservation_id": reservation.id,
            "goods": self.goods.id,
            "quantity": "1",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("尚未生效", response.json()["message"])

    def test_occupy_endpoint(self):
        reservation = self._active_reservation()
        response = self.client.post(
            f"/api/storage-reservations/{reservation.id}/occupy/",
            {"goods": self.goods.id, "quantity": "3"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, "occupied")

        self.goods.refresh_from_db()
        self.assertEqual(self.goods.quantity, Decimal("15"))


class StorageConcurrencyTest(TransactionTestCase):
    """并发预约：多支队伍同时抢同一个保管位，总占用不能超过容量"""

    def setUp(self):
        self.user = User.objects.create_user("concurrent-user", "testpass123", role="admin")
        self.location = StorageLocation.objects.create(
            name="并发柜", capacity=3, created_by=self.user
        )

    def test_concurrent_reservations_never_exceed_capacity(self):
        start = timezone.now() + timedelta(hours=1)
        end = start + timedelta(hours=2)
        team_count = 8
        successes = []
        conflicts = []
        errors = []
        lock = threading.Lock()

        def worker(team_idx):
            try:
                services.create_reservation(
                    self.location, self.user, f"队伍{team_idx}", 1, start, end
                )
                with lock:
                    successes.append(team_idx)
            except services.CapacityConflictError:
                with lock:
                    conflicts.append(team_idx)
            except Exception as exc:  # noqa: BLE001 - 测试需要暴露任何意外异常
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(team_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        for thread in threads:
            self.assertFalse(thread.is_alive(), "并发线程未在限定时间内结束")

        self.assertEqual(errors, [])
        self.assertEqual(len(successes), 3)
        self.assertEqual(len(conflicts), team_count - 3)

        held = StorageReservation.objects.filter(
            location=self.location, status="active"
        ).aggregate(total=Sum("quantity"))["total"]
        self.assertEqual(held, 3)
