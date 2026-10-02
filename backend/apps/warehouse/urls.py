"""
仓库管理URL配置
"""
from django.urls import path
from .views import (
    UnitListView, UnitDetailView, UnitBatchDeleteView, UnitAllView,
    CategoryListView, CategoryDetailView, CategoryBatchDeleteView, CategoryAllView,
    VarietyListView, VarietyDetailView, VarietyBatchDeleteView,
    VarietyTemplateView, VarietyImportView,
    DashboardView, GoodsListView, StockInListView, StockOutListView,
    WarningListView, ApprovalListView
)
from .booking_views import (
    LockerListView, LockerAllView, LockerDetailView, LockerAvailabilityView,
    ReservationListView, ReservationDetailView, ReservationRescheduleView,
    ReservationCancelView, ReservationConsumeView, ReservationReleaseView,
    ReservationExpireView,
)

urlpatterns = [
    # 仪表盘
    path('dashboard/', DashboardView.as_view(), name='dashboard'),

    # 单位管理
    path('units/', UnitListView.as_view(), name='unit-list'),
    path('units/all/', UnitAllView.as_view(), name='unit-all'),
    path('units/batch-delete/', UnitBatchDeleteView.as_view(), name='unit-batch-delete'),
    path('units/<int:pk>/', UnitDetailView.as_view(), name='unit-detail'),

    # 品类管理
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('categories/all/', CategoryAllView.as_view(), name='category-all'),
    path('categories/batch-delete/', CategoryBatchDeleteView.as_view(), name='category-batch-delete'),
    path('categories/<int:pk>/', CategoryDetailView.as_view(), name='category-detail'),

    # 品种管理
    path('varieties/', VarietyListView.as_view(), name='variety-list'),
    path('varieties/batch-delete/', VarietyBatchDeleteView.as_view(), name='variety-batch-delete'),
    path('varieties/template/', VarietyTemplateView.as_view(), name='variety-template'),
    path('varieties/import/', VarietyImportView.as_view(), name='variety-import'),
    path('varieties/<int:pk>/', VarietyDetailView.as_view(), name='variety-detail'),

    # 货物管理
    path('goods/', GoodsListView.as_view(), name='goods-list'),

    # 保管柜管理
    path('lockers/', LockerListView.as_view(), name='locker-list'),
    path('lockers/all/', LockerAllView.as_view(), name='locker-all'),
    path('lockers/<int:pk>/', LockerDetailView.as_view(), name='locker-detail'),
    path('lockers/<int:pk>/availability/', LockerAvailabilityView.as_view(),
         name='locker-availability'),

    # 保管位预约管理
    path('reservations/', ReservationListView.as_view(), name='reservation-list'),
    path('reservations/expire/', ReservationExpireView.as_view(), name='reservation-expire'),
    path('reservations/<int:pk>/', ReservationDetailView.as_view(), name='reservation-detail'),
    path('reservations/<int:pk>/reschedule/', ReservationRescheduleView.as_view(),
         name='reservation-reschedule'),
    path('reservations/<int:pk>/cancel/', ReservationCancelView.as_view(),
         name='reservation-cancel'),
    path('reservations/<int:pk>/consume/', ReservationConsumeView.as_view(),
         name='reservation-consume'),
    path('reservations/<int:pk>/release/', ReservationReleaseView.as_view(),
         name='reservation-release'),

    # 入库管理（正式入库须消费有效预约）
    path('stock-in/', StockInListView.as_view(), name='stock-in-list'),

    # 出库管理
    path('stock-out/', StockOutListView.as_view(), name='stock-out-list'),

    # 预警管理
    path('warnings/', WarningListView.as_view(), name='warning-list'),

    # 审批管理
    path('approvals/', ApprovalListView.as_view(), name='approval-list'),
]
