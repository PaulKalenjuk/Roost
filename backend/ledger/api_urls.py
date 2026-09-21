"""URL routing for the Roost API."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register("properties", api.PropertyViewSet, basename="property")
router.register("owners", api.OwnerViewSet, basename="owner")
router.register("ownerships", api.PropertyOwnershipViewSet, basename="ownership")
router.register("listings", api.ListingViewSet, basename="listing")
router.register("categories", api.CategoryViewSet, basename="category")
router.register("expenses", api.ExpenseViewSet, basename="expense")
router.register("utility-types", api.UtilityTypeViewSet, basename="utility-type")
router.register("utility-bills", api.UtilityBillViewSet, basename="utility-bill")
router.register("assets", api.AssetViewSet, basename="asset")
router.register("reservations", api.ReservationViewSet, basename="reservation")
router.register("monthly-earnings", api.MonthlyEarningsViewSet, basename="monthly-earnings")
router.register("earnings-summaries", api.EarningsSummaryViewSet, basename="earnings-summary")
router.register("receipts", api.ReceiptViewSet, basename="receipt")
router.register("imports", api.ImportBatchViewSet, basename="import")

urlpatterns = [
    path("auth/csrf/", api.csrf, name="api-csrf"),
    path("auth/login/", api.login_view, name="api-login"),
    path("auth/logout/", api.logout_view, name="api-logout"),
    path("auth/me/", api.me, name="api-me"),
    path("income/import-pdf/", api.import_income_pdf, name="api-income-import-pdf"),
    path("assets/extract/", api.extract_asset, name="api-asset-extract"),
    path("expenses/extract/", api.extract_expense, name="api-expense-extract"),
    path("utilities/extract/", api.extract_bill, name="api-utility-extract"),
    path("utilities/coverage/", api.utilities_coverage, name="api-utilities-coverage"),
    path("reports/fy/", api.fy_report, name="api-fy-report"),
    path("", include(router.urls)),
]
