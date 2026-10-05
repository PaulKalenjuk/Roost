"""REST serializers for the Roost API."""
import json

from rest_framework import serializers

from . import fiscal


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
    AssetPhoto,
    Category,
    DepreciationEntry,
    EarningsSummary,
    Expense,
    ImportBatch,
    Listing,
    MonthlyEarnings,
    Owner,
    Property,
    PropertyOwnership,
    Receipt,
    Reservation,
    UnallocatedExpense,
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
    image_url = serializers.SerializerMethodField()
    ownerships = PropertyOwnershipSerializer(many=True, read_only=True)

    class Meta:
        model = Property
        fields = [
            "id", "name", "address", "purchase_date", "purchase_price",
            "total_floor_area_sqm", "rental_floor_area_sqm", "let_percentage",
            "gst_registered", "default_depreciation_method", "notes",
            "image", "image_url",
            "let_share", "rental_area_share", "ownership_total", "ownerships",
        ]

    def get_image_url(self, obj):
        """Absolute URL for the property image (media is served to logged-in users)."""
        if not obj.image:
            return None
        request = self.context.get("request")
        return (
            request.build_absolute_uri(obj.image.url)
            if request
            else obj.image.url
        )


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


class UnallocatedExpenseSerializer(BaseSerializer):
    """A photographed receipt in the unallocated staging area.

    ``image`` is uploaded (multipart); ``highlights`` is the list of normalised
    highlight boxes drawn over it. ``lines`` mirrors what the vision model read.
    """

    category_name = serializers.CharField(source="category.name", read_only=True)
    image_url = serializers.SerializerMethodField()
    highlight_count = serializers.SerializerMethodField()
    lines = serializers.SerializerMethodField()

    class Meta:
        model = UnallocatedExpense
        fields = [
            "id", "property", "image", "image_url", "highlights", "status",
            "date", "vendor", "description", "amount", "gst_amount",
            "category", "category_name", "confidence", "rationale", "extracted",
            "expense", "note", "highlight_count", "lines", "created_at",
        ]
        read_only_fields = [
            "status", "confidence", "rationale", "extracted", "expense",
        ]

    def to_internal_value(self, data):
        """Parse ``highlights`` sent as a JSON string over multipart.

        ``BaseSerializer`` turns the QueryDict into a plain dict (to normalise
        blank fields), which defeats DRF's HTML-input detection and leaves a
        JSONField as the raw string. Decode it here.
        """
        value = super().to_internal_value(data)
        highlights = value.get("highlights")
        if isinstance(highlights, str):
            try:
                value["highlights"] = json.loads(highlights)
            except json.JSONDecodeError:
                raise serializers.ValidationError(
                    {"highlights": "Must be valid JSON."}
                )
        return value

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url if obj.image else None

    def get_highlight_count(self, obj):
        return obj.highlight_count()

    def get_lines(self, obj):
        return (obj.extracted or {}).get("lines", [])


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


class AssetPhotoSerializer(BaseSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = AssetPhoto
        fields = ["id", "asset", "image", "image_url", "caption", "created_at"]

    def get_image_url(self, obj):
        request = self.context.get("request")
        if obj.image and request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url if obj.image else None


class AssetSerializer(BaseSerializer):
    property_name = serializers.CharField(source="property.name", read_only=True)
    depreciation_entries = DepreciationEntrySerializer(many=True, read_only=True)
    receipts = ReceiptSerializer(many=True, read_only=True)
    photos = AssetPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id", "property", "property_name", "name", "kind", "purchase_date",
            "cost", "effective_life_years", "method", "business_use_pct",
            "low_value_pool", "effective_life_is_estimate", "disposed_date",
            "disposal_value", "notes", "photos",
            "depreciation_entries", "receipts",
        ]


class EarningsSummarySerializer(BaseSerializer):
    listing_name = serializers.CharField(source="listing.name", read_only=True)

    class Meta:
        model = EarningsSummary
        fields = [
            "id", "listing", "listing_name", "financial_year", "period_start",
            "period_end", "nights_booked", "avg_night_stay", "gross_earnings",
            "service_fees", "total_earnings", "source", "source_file",
        ]

    def validate(self, attrs):
        # Keep the denormalised FY in step if the period is edited.
        start = attrs.get("period_start", getattr(self.instance, "period_start", None))
        end = attrs.get("period_end", getattr(self.instance, "period_end", None))
        if start or end:
            attrs["financial_year"] = fiscal.fy_label(end or start)
        return attrs


class ImportBatchSerializer(serializers.ModelSerializer):
    report_url = serializers.SerializerMethodField()

    class Meta:
        model = ImportBatch
        fields = ["id", "source", "listing", "filename", "report_file",
                  "report_url", "period_start", "period_end", "summary",
                  "rows_total", "rows_created", "rows_updated", "rows_skipped",
                  "rows_failed", "log", "created_at"]
        extra_kwargs = {"report_file": {"read_only": True}}

    def get_report_url(self, obj):
        """Absolute download link for the retained PDF (media needs login)."""
        if not obj.report_file:
            return None
        request = self.context.get("request")
        return (
            request.build_absolute_uri(obj.report_file.url)
            if request
            else obj.report_file.url
        )
