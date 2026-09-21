"""Admin UI — the MVP front end for the ledger."""
from django.contrib import admin
from django.utils.html import format_html

from . import depreciation
from .models import (
    Asset,
    Category,
    DepreciationEntry,
    Expense,
    ImportBatch,
    Listing,
    MonthlyEarnings,
    Owner,
    Property,
    PropertyOwnership,
    Receipt,
    Reservation,
)


class ReceiptInline(admin.TabularInline):
    model = Receipt
    extra = 1
    fields = ("file", "original_name", "note")


class PropertyOwnershipInline(admin.TabularInline):
    model = PropertyOwnership
    extra = 1
    fields = ("owner", "share_pct")
    verbose_name = "owner"
    verbose_name_plural = "ownership (shares should total 100%)"


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "address_short", "total_floor_area_sqm",
                    "rental_floor_area_sqm", "let_share_display",
                    "gst_registered", "ownership_total_display")
    search_fields = ("name", "address")
    inlines = [PropertyOwnershipInline]
    fieldsets = (
        (None, {"fields": ("name", "address", "purchase_date", "purchase_price")}),
        ("Let share", {"fields": ("total_floor_area_sqm", "rental_floor_area_sqm",
                                   "let_percentage")}),
        ("Tax treatment", {"fields": ("gst_registered", "default_depreciation_method")}),
        ("Notes", {"fields": ("notes",)}),
    )

    @admin.display(description="Address")
    def address_short(self, obj):
        return (obj.address or "—")[:40]

    @admin.display(description="Let share")
    def let_share_display(self, obj):
        share = obj.let_share
        return f"{share * 100:.2f}%" if share is not None else "—"

    @admin.display(description="Ownership")
    def ownership_total_display(self, obj):
        total = obj.ownership_total
        if not total:
            return "—"
        marker = "✅" if total == 1 else "⚠️"
        return f"{marker} {total * 100:.2f}%"


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ("name", "email")
    search_fields = ("name", "email")


@admin.register(MonthlyEarnings)
class MonthlyEarningsAdmin(admin.ModelAdmin):
    list_display = ("month", "listing", "gross_earnings", "service_fees",
                    "tax_withheld", "total_earnings", "source")
    list_filter = ("listing", "source")
    date_hierarchy = "month"
    readonly_fields = ("created_at", "updated_at")


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("name", "property", "platform", "external_id", "active")
    list_filter = ("platform", "active")
    search_fields = ("name", "external_id")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "default_apportionment", "details")
    list_filter = ("kind", "default_apportionment")


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("confirmation_code", "listing", "guest_name", "check_in",
                    "check_out", "nights", "gross_earnings", "airbnb_fee",
                    "net_payout", "status")
    list_filter = ("listing", "status", "source")
    search_fields = ("confirmation_code", "guest_name")
    date_hierarchy = "check_in"
    readonly_fields = ("raw", "external_id", "source")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("date", "property", "category", "description", "amount",
                    "apportionment", "deductible_amount", "source")
    list_filter = ("category", "property", "apportionment", "paid", "source")
    search_fields = ("description", "vendor", "notes")
    date_hierarchy = "date"
    inlines = [ReceiptInline]
    readonly_fields = ("deductible_amount",)


class DepreciationEntryInline(admin.TabularInline):
    model = DepreciationEntry
    extra = 0
    can_delete = False
    readonly_fields = ("financial_year", "method", "opening_value",
                       "deduction", "closing_value", "days_held")


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "property", "kind", "purchase_date", "cost",
                    "effective_life_years", "method", "business_use_pct",
                    "low_value_pool", "is_disposed")
    list_filter = ("kind", "method", "low_value_pool", "property")
    search_fields = ("name", "notes")
    inlines = [DepreciationEntryInline]
    actions = ["rebuild_depreciation"]

    @admin.action(description="Rebuild depreciation schedule")
    def rebuild_depreciation(self, request, queryset):
        count = 0
        for asset in queryset:
            depreciation.recompute_schedule(asset)
            count += 1
        self.message_user(request, f"Rebuilt depreciation for {count} asset(s).")


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("original_name", "property", "expense", "asset", "created_at")
    list_filter = ("property",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display
    def download(self, obj):
        if obj.file:
            return format_html('<a href="{}">open</a>', obj.file.url)
        return "—"


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("created_at", "source", "listing", "filename", "period_start",
                    "period_end", "rows_total", "rows_created", "rows_updated",
                    "rows_skipped", "rows_failed")
    list_filter = ("source",)
    readonly_fields = ("created_at", "updated_at", "finished_at", "summary")


admin.site.site_header = "Roost"
admin.site.site_title = "Roost"
admin.site.index_title = "Short-stay income, expenses, assets & depreciation"
