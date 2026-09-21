"""REST serializers for the Roost API."""
from rest_framework import serializers


class BaseSerializer(serializers.ModelSerializer):
    """Tolerate cleared form fields by treating ``""`` as ``null``.

    DRF rejects an empty string for nullable Decimal/Date fields (a browser form
    sends ``""`` when a field is left blank, including on multipart PATCH when you
    clear a date), which is a footgun that bites every client.  Normalise it here.
    """

    def to_internal_value(self, data):
        if hasattr(data, "items"):
            data = {
                key: (
                    None
                    if value == ""
                    and key in self.fields
                    and self.fields[key].allow_null
                    else value
                )
                for key, value in data.items()
            }
        return super().to_internal_value(data)

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
    UtilityBill,
    UtilityType,
)


class OwnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Owner
        fields = ["id", "name", "email", "notes"]


class PropertyOwnershipSerializer(BaseSerializer):
    owner_name = serializers.CharField(source="owner.name", read_only=True)

    class Meta:
        model = PropertyOwnership
        fields = ["id", "property", "owner", "owner_name", "share_pct"]


class PropertySerializer(BaseSerializer):
    let_share = serializers.DecimalField(max_digits=8, decimal_places=6, read_only=True)
    rental_area_share = serializers.DecimalField(max_digits=8, decimal_places=6, read_only=True)
    ownership_total = serializers.DecimalField(max_digits=8, decimal_places=4, read_only=True)
    ownerships = PropertyOwnershipSerializer(many=True, read_only=True)

    class Meta:
        model = Property
        fields = [
            "id", "name", "address", "purchase_date", "purchase_price",
            "total_floor_area_sqm", "rental_floor_area_sqm", "let_percentage",
            "gst_registered", "default_depreciation_method", "notes",
            "let_share", "rental_area_share", "ownership_total", "ownerships",
        ]


class ListingSerializer(BaseSerializer):
    property_name = serializers.CharField(source="property.name", read_only=True)

    class Meta:
        model = Listing
        fields = ["id", "property", "property_name", "platform", "name",
                  "external_id", "url", "active"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "kind", "default_apportionment", "details"]


class ReservationSerializer(serializers.ModelSerializer):
    listing_name = serializers.CharField(source="listing.name", read_only=True)

    class Meta:
        model = Reservation
        fields = [
            "id", "listing", "listing_name", "confirmation_code", "guest_name",
            "check_in", "check_out", "nights", "currency", "accommodation_amount",
            "cleaning_fee", "gross_earnings", "airbnb_fee", "taxes_collected",
            "net_payout", "payout_date", "status", "source",
        ]


class MonthlyEarningsSerializer(serializers.ModelSerializer):
    listing_name = serializers.CharField(source="listing.name", read_only=True)

    class Meta:
        model = MonthlyEarnings
        fields = [
            "id", "listing", "listing_name", "month", "currency", "gross_earnings",
            "adjustments", "service_fees", "tax_withheld", "total_earnings",
            "source", "source_file",
        ]


class ReceiptSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = Receipt
        fields = ["id", "property", "file", "url", "original_name", "note",
                  "expense", "asset", "created_at"]
        extra_kwargs = {"file": {"required": True}}

    def get_url(self, obj):
        request = self.context.get("request")
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return obj.file.url if obj.file else None

    def create(self, validated_data):
        upload = validated_data.get("file")
        if upload and not validated_data.get("original_name"):
            validated_data["original_name"] = getattr(upload, "name", "")
        return super().create(validated_data)


class ExpenseSerializer(BaseSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    property_name = serializers.CharField(source="property.name", read_only=True)
    deductible_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    receipts = ReceiptSerializer(many=True, read_only=True)

    class Meta:
        model = Expense
        fields = [
            "id", "property", "property_name", "listing", "category", "category_name",
            "kind", "date", "vendor", "description", "amount", "gst_amount",
            "apportionment", "apportionment_pct", "deductible_amount", "paid",
            "payment_method", "notes", "source", "receipts",
        ]
        read_only_fields = ["source"]


class UtilityTypeSerializer(BaseSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = UtilityType
        fields = ["id", "property", "name", "category", "category_name",
                  "frequency", "supplier", "apportionment", "coverage_start", "notes"]


class UtilityBillSerializer(BaseSerializer):
    utility_type_name = serializers.CharField(source="utility_type.name", read_only=True)
    claimable_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    attachment_url = serializers.SerializerMethodField()

    class Meta:
        model = UtilityBill
        fields = [
            "id", "utility_type", "utility_type_name", "bill_date", "period_start",
            "period_end", "amount", "gst_amount", "paid", "attachment",
            "attachment_url", "extracted", "notes", "expense", "claimable_amount",
        ]
        read_only_fields = ["expense"]

    def get_attachment_url(self, obj):
        request = self.context.get("request")
        if obj.attachment and request:
            return request.build_absolute_uri(obj.attachment.url)
        return obj.attachment.url if obj.attachment else None


class DepreciationEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = DepreciationEntry
        fields = ["id", "financial_year", "method", "opening_value", "deduction",
                  "closing_value", "days_held"]


class AssetSerializer(BaseSerializer):
    property_name = serializers.CharField(source="property.name", read_only=True)
    depreciation_entries = DepreciationEntrySerializer(many=True, read_only=True)
    receipts = ReceiptSerializer(many=True, read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id", "property", "property_name", "name", "kind", "purchase_date",
            "cost", "effective_life_years", "method", "business_use_pct",
            "low_value_pool", "effective_life_is_estimate", "disposed_date",
            "disposal_value", "notes", "depreciation_entries", "receipts",
        ]


class ImportBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportBatch
        fields = ["id", "source", "listing", "filename", "period_start", "period_end",
                  "summary", "rows_total", "rows_created", "rows_updated",
                  "rows_skipped", "rows_failed", "log", "created_at"]
